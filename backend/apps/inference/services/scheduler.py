"""持久化生成工作项的调度、恢复、取消与产物保留服务。

这里刻意只把数据库当作调度事实源。Celery 消息可以重复投递、丢失或延迟，
真正决定任务能否执行的是 ``GenerationWorkItem`` 的租约和状态版本；因此 worker
重启后重新收到同一消息不会重复进入模型调用。
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import logging
import uuid
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional
from urllib.parse import urlparse

import requests
from django.conf import settings
from django.db import connection, transaction
from django.db.models import Count, F, Q
from django.utils import timezone

from apps.projects.models import ProjectStage
from core.ai_client.factory import create_ai_client

from ..models import BudgetReservation, GenerationWorkItem, MediaArtifact
from .errors import InferenceErrorClassifier
from .events import publish_work_item_event
from .gates import PaidCallGate
from .hybrid import HybridInferenceService
from .projection import WorkItemResultProjector
from .resources import ResourceLeaseService, ResourceLeaseUnavailable
from .security import mask_sensitive_data
from .work_items import InvalidWorkItemTransition, WorkItemService


logger = logging.getLogger(__name__)

TERMINAL_STATES = frozenset({'succeeded', 'failed', 'cancelled'})
ACTIVE_AGENT_STATES = frozenset({'queued', 'running'})
QUEUE_BY_CAPABILITY = {
    'llm': 'llm',
    'text2image': 'image',
    'image_edit': 'image',
    'image2video': 'video',
    'motion_render': 'video',
}


def queue_for_capability(capability: str) -> str:
    """返回能力专用队列；未知能力先进入编排队列等待人工修正。"""

    return QUEUE_BY_CAPABILITY.get(capability, 'orchestration')


def _locked_queryset(queryset):
    return queryset.select_for_update() if connection.features.has_select_for_update else queryset


def _resolve_awaitable(value):
    """兼容仓库中同步和异步并存的旧执行器接口。"""

    if not inspect.isawaitable(value):
        return value
    # Celery worker 的任务入口是同步函数，正常不会运行事件循环。显式拒绝嵌套
    # 循环比静默创建线程更安全，也能暴露不符合执行器契约的实现。
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(value)
    raise RuntimeError('同步调度器不能在已运行的 asyncio 事件循环中等待模型执行器')


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


class RuntimeAgentControl:
    """Runtime Agent 查询和取消的最小控制面客户端。"""

    timeout_seconds = 10

    @staticmethod
    def _base_url(node) -> str:
        return str(getattr(node, 'agent_url', '') or getattr(node, 'endpoint', '')).rstrip('/')

    @staticmethod
    def _headers(node) -> Dict[str, str]:
        token = str(getattr(node, 'access_token', '') or '')
        return {'Authorization': f'Bearer {token}'} if token else {}

    def get_job(self, node, job_id: str) -> Dict[str, Any]:
        base_url = self._base_url(node)
        if not base_url:
            raise RuntimeError('Runtime Agent 节点未配置地址')
        response = requests.get(
            f'{base_url}/v1/jobs/{job_id}',
            headers=self._headers(node),
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return response.json()

    def cancel_job(self, node, job_id: str) -> Dict[str, Any]:
        base_url = self._base_url(node)
        if not base_url:
            raise RuntimeError('Runtime Agent 节点未配置地址')
        response = requests.delete(
            f'{base_url}/v1/jobs/{job_id}',
            headers=self._headers(node),
            timeout=self.timeout_seconds,
        )
        if response.status_code not in {200, 202, 204}:
            response.raise_for_status()
        return response.json() if response.content else {'status': 'cancel_requested'}

    def materialize_artifacts(self, node, job: Dict[str, Any], work_item) -> list:
        """把 Agent 产物校验后原子接收到 Django 中央存储。

        worker 可能在 Agent 完成后、下载前退出；reconciler 必须补完这个步骤，
        不能只把 Agent 的临时 artifact_id 当成业务最终产物。
        """

        base_url = self._base_url(node)
        root = Path(settings.STORAGE_ROOT).resolve(strict=False)
        target_dir = root / 'runtime-recovered' / str(work_item.pk)
        target_dir.mkdir(parents=True, exist_ok=True)
        recovered = []
        for artifact in job.get('artifacts') or []:
            item = dict(artifact)
            artifact_id = str(item.get('artifact_id') or item.get('id') or '')
            if not artifact_id:
                failure = RuntimeError('Runtime Agent 产物缺少 artifact_id')
                failure.code = 'OUTPUT_SCHEMA_INVALID'
                raise failure
            suffix = Path(str(item.get('filename') or '')).suffix or '.bin'
            safe_name = hashlib.sha256(artifact_id.encode('utf-8')).hexdigest()[:32]
            target = target_dir / f'{safe_name}{suffix}'
            expected = str(item.get('sha256') or '').lower()
            if target.exists() and (not expected or _file_sha256(target) == expected):
                item.update({'storage_path': str(target), 'uri': str(target)})
                recovered.append(item)
                continue

            download_url = str(item.get('download_url') or f'/v1/artifacts/{artifact_id}')
            if not download_url.startswith('/'):
                failure = RuntimeError('Runtime Agent 产物下载地址必须是节点内相对路径')
                failure.code = 'OUTPUT_SCHEMA_INVALID'
                raise failure
            response = requests.get(
                f'{base_url}{download_url}',
                headers=self._headers(node),
                stream=True,
                timeout=max(self.timeout_seconds, 30),
            )
            response.raise_for_status()
            partial = target.with_name(f'{target.name}.{uuid.uuid4().hex}.partial')
            digest = hashlib.sha256()
            try:
                with partial.open('wb') as stream:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            stream.write(chunk)
                            digest.update(chunk)
                actual = digest.hexdigest()
                header_sha = str(response.headers.get('X-Artifact-SHA256') or '').lower()
                expected = expected or header_sha
                if expected and actual != expected:
                    failure = RuntimeError('Runtime Agent 产物 SHA-256 校验失败')
                    failure.code = 'CHECKSUM_MISMATCH'
                    raise failure
                partial.replace(target)
            finally:
                partial.unlink(missing_ok=True)
            item.update({
                'storage_path': str(target),
                'uri': str(target),
                'sha256': actual,
                'file_size': target.stat().st_size,
            })
            recovered.append(item)
        return recovered


class WorkItemScheduler:
    """围绕数据库租约执行并恢复一个生成工作项。"""

    lease_seconds = 120

    def __init__(
        self,
        *,
        hybrid_service=HybridInferenceService,
        client_factory: Callable = create_ai_client,
        agent_control: Optional[RuntimeAgentControl] = None,
        now: Optional[Callable] = None,
    ):
        self.hybrid_service = hybrid_service
        self.client_factory = client_factory
        self.agent_control = agent_control or RuntimeAgentControl()
        self.now = now or timezone.now

    def dispatch(self, work_item_id, lease_owner: str) -> Dict[str, Any]:
        """幂等认领并执行工作项。

        Celery 的 ``acks_late`` 可能让同一消息再次出现。终态直接返回；非 waiting
        状态也不抢占现有执行者。只有数据库租约认领成功后才允许进入模型调用。
        """

        work_item = GenerationWorkItem.objects.select_related(
            'project', 'provider', 'runtime_node'
        ).get(pk=work_item_id)
        if work_item.status in TERMINAL_STATES:
            return self._result(work_item, idempotent=True)
        if work_item.status != 'waiting':
            return self._result(work_item, idempotent=True, in_progress=True)
        blocking_dependencies = work_item.depends_on.exclude(status='succeeded')
        if blocking_dependencies.exists():
            if blocking_dependencies.filter(status__in=['failed', 'cancelled']).exists():
                failed = WorkItemService.fail(
                    work_item,
                    error={
                        'category': 'dependency',
                        'code': 'DEPENDENCY_FAILED',
                        'message': '前置工作项失败或取消，当前工作项未提交模型。',
                    },
                    now=self.now(),
                )
                self.aggregate_stage(failed.project_id, failed.stage_type)
                publish_work_item_event(failed, 'failed', error_code='DEPENDENCY_FAILED')
                return self._result(failed)
            return self._result(work_item, idempotent=True, waiting_dependencies=True)

        try:
            leased = WorkItemService.claim(
                work_item,
                lease_owner=lease_owner,
                lease_seconds=self.lease_seconds,
                expected_version=work_item.version,
                now=self.now(),
            )
            running = WorkItemService.start(
                leased,
                lease_owner=lease_owner,
                expected_version=leased.version,
                now=self.now(),
            )
            publish_work_item_event(running, 'running')
        except InvalidWorkItemTransition:
            # 并发 worker 已抢到租约时，当前消息正常结束，不能把冲突当作失败重试。
            current = GenerationWorkItem.objects.get(pk=work_item_id)
            return self._result(current, idempotent=True, in_progress=True)

        try:
            invocation_parameters = self._invocation_parameters(running)
            result = self.hybrid_service.execute(
                project=running.project,
                capability=running.capability,
                stage_type=running.stage_type,
                invoke=lambda provider, parameters, repaired: self._invoke_provider(
                    provider,
                    running.capability,
                    parameters,
                    repaired_structure=repaired,
                    work_item_id=running.pk,
                    lease_owner=lease_owner,
                ),
                request_parameters=invocation_parameters,
                usage_estimate=self._usage_estimate(running),
                work_item=running,
                explicit_provider=running.provider,
                manual_api=bool((running.request_parameters or {}).get('manual_api', False)),
            )
            self._record_artifacts(running, result.value)
            WorkItemResultProjector.persist(running, result.value, result.provider)
            usage = self._usage_from_log(result.usage_log)
            route_snapshot = {
                **(running.route_snapshot or {}),
                'selected_provider_id': str(result.provider.pk),
                'selected_target_id': str(getattr(result.target, 'pk', '') or ''),
                'completed_at': self.now().isoformat(),
            }
            updates = {
                'provider': result.provider,
                'runtime_node': getattr(result.provider, 'runtime_node', None),
                'target': result.target if getattr(result.target, 'pk', None) else None,
                'actual_cost': getattr(result.usage_log, 'settled_cost', Decimal('0')) or Decimal('0'),
                'route_snapshot': route_snapshot,
            }
            agent_job_id = self._extract_job_id(result.value, result.provider)
            if agent_job_id:
                updates['agent_job_id'] = agent_job_id
            completed = WorkItemService.succeed(
                running,
                lease_owner=lease_owner,
                expected_version=running.version,
                now=self.now(),
                **updates,
            )
            # usage 只含数值计量，不是凭据。通用日志遮罩器会把 input_tokens 这类
            # 字段名误识别为 access token，因此在状态事务完成后单独写入计量值。
            GenerationWorkItem.objects.filter(pk=completed.pk).update(usage=usage)
            completed.usage = usage
            self.aggregate_stage(completed.project_id, completed.stage_type)
            self._enqueue_ready_dependents(completed)
            publish_work_item_event(
                completed,
                'succeeded',
                provider_id=str(completed.provider_id or ''),
                actual_cost=str(completed.actual_cost),
            )
            return self._result(completed)
        except Exception as error:  # Hybrid 和旧执行器均在此归一为稳定状态
            failed = self._handle_execution_failure(running, lease_owner, error)
            self.aggregate_stage(failed.project_id, failed.stage_type)
            publish_work_item_event(
                failed,
                'retry_wait' if failed.status == 'retry_wait' else 'failed',
                error_code=failed.error_code,
            )
            return self._result(failed)

    def _invoke_provider(
        self,
        provider,
        capability: str,
        parameters,
        repaired_structure=False,
        work_item_id=None,
        lease_owner='',
    ):
        """把统一工作项参数转换为现有四类执行器调用。"""

        resource_lease = self._acquire_resource_lease(
            provider, capability, work_item_id=work_item_id, lease_owner=lease_owner
        )
        try:
            return self._call_provider_client(
                provider,
                capability,
                parameters,
                repaired_structure=repaired_structure,
                work_item_id=work_item_id,
                lease_owner=lease_owner,
            )
        finally:
            if resource_lease is not None:
                try:
                    ResourceLeaseService.release(resource_lease)
                except Exception as error:
                    logger.warning('Redis 资源租约释放失败 lease=%s error=%s', resource_lease.key, error)

    def _call_provider_client(
        self,
        provider,
        capability,
        parameters,
        *,
        repaired_structure=False,
        work_item_id=None,
        lease_owner='',
    ):
        client = self.client_factory(provider)
        transport = getattr(client, 'transport', None)
        if (
            getattr(provider, 'deployment_mode', '') == 'local'
            and transport is not None
            and work_item_id
        ):
            transport.on_job_submitted = lambda job_id, _job: self._persist_agent_job_id(
                work_item_id,
                lease_owner,
                job_id,
                provider,
            )
        payload = dict(parameters or {})
        prompt = str(payload.pop('prompt', '') or '')
        if repaired_structure:
            payload['repair_structure'] = True

        if capability == 'llm':
            max_tokens = int(payload.pop('max_tokens', getattr(provider, 'max_tokens', 2000)))
            temperature = float(payload.pop('temperature', getattr(provider, 'temperature', 0.7)))
            method = getattr(client, '_generate_text', None)
            value = (
                method(prompt, max_tokens, temperature, **payload)
                if method
                else client.generate(prompt, max_tokens=max_tokens, temperature=temperature, **payload)
            )
            return _resolve_awaitable(value)

        if capability == 'text2image':
            return _resolve_awaitable(client.generate(prompt=prompt, **payload))

        if capability == 'image_edit':
            image_url = payload.pop('image_url', '') or payload.pop('image_uri', '')
            return _resolve_awaitable(client.generate(image_url=image_url, prompt=prompt, **payload))

        if capability in {'image2video', 'motion_render'}:
            # 仓库内新旧视频执行器都以 _generate_video 作为稳定入口，返回值可能是
            # AIResponse 或旧版 dict，HybridInferenceService 会统一判断成功与失败。
            return _resolve_awaitable(client._generate_video(prompt=prompt, **payload))

        failure = RuntimeError(f'不支持的生成能力：{capability}')
        failure.code = 'INVALID_REQUEST'
        raise failure

    @staticmethod
    def _acquire_resource_lease(provider, capability, *, work_item_id=None, lease_owner=''):
        """Django Redis 租约与 Agent 本机锁共同保护单 GPU。"""

        if (
            getattr(provider, 'deployment_mode', '') != 'local'
            or not getattr(settings, 'AI_DISTRIBUTED_RESOURCE_LEASES_ENABLED', True)
        ):
            return None
        node = getattr(provider, 'runtime_node', None)
        if node is None:
            failure = RuntimeError('本地 Provider 未绑定 RuntimeNode')
            failure.code = 'RUNTIME_NOT_READY'
            raise failure
        group = 'cpu_motion' if capability == 'motion_render' else 'gpu'
        capacity = int((node.resource_groups or {}).get(group) or node.slot_count or 1)
        owner = f'{lease_owner or "worker"}:{work_item_id or uuid.uuid4()}:{provider.pk}'
        ttl = max(60, int(getattr(provider, 'timeout', 300) or 300) + 60)
        try:
            return ResourceLeaseService.acquire(
                node.pk,
                group,
                owner,
                capacity=capacity,
                ttl_seconds=ttl,
            )
        except ResourceLeaseUnavailable:
            raise
        except Exception as error:
            failure = RuntimeError(f'Redis 资源租约不可用：{error}')
            failure.code = 'RESOURCE_BUSY'
            raise failure from error

    def _persist_agent_job_id(self, work_item_id, lease_owner: str, job_id: str, provider) -> None:
        """在 Agent 接单后、同步轮询前固化远端任务标识。

        这里只更新运行元数据，不推进状态版本；当前 worker 完成后仍要使用原版本
        写入终态。若用户已经取消或租约被其他执行者接管，则拒绝迟到的 job_id，
        避免把新 Agent 任务错误关联到已终止工作项。
        """

        now = self.now()
        with transaction.atomic():
            item = _locked_queryset(GenerationWorkItem.objects.filter(pk=work_item_id)).get()
            if item.status != 'running' or item.lease_owner != lease_owner:
                failure = RuntimeError('工作项已不再由当前 worker 执行')
                failure.code = 'USER_CANCELLED' if item.status == 'cancelled' else 'RUNTIME_CRASH'
                raise failure
            if item.agent_job_id and item.agent_job_id != str(job_id):
                failure = RuntimeError('同一工作项返回了不同的 Runtime Agent job_id')
                failure.code = 'OUTPUT_SCHEMA_INVALID'
                raise failure
            item.agent_job_id = str(job_id)
            item.provider = provider
            item.runtime_node = getattr(provider, 'runtime_node', None)
            item.heartbeat_at = now
            item.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
            item.save(update_fields=[
                'agent_job_id', 'provider', 'runtime_node', 'heartbeat_at',
                'lease_expires_at', 'updated_at',
            ])

    @classmethod
    def _invocation_parameters(cls, work_item) -> Dict[str, Any]:
        params = dict(work_item.request_parameters or {})
        for key in (
            'usage_estimate', 'manual_api', 'retry_delay_seconds', 'artifact_lifecycle',
            'artifact_expires_at',
        ):
            params.pop(key, None)
        params.setdefault('project_id', str(work_item.project_id))
        params.setdefault('stage_type', work_item.stage_type)
        params.setdefault('work_item_id', str(work_item.pk))
        params.setdefault('idempotency_key', work_item.idempotency_key)
        params['input_artifacts'] = cls._resolve_dependency_artifacts(
            work_item,
            params.get('input_artifacts') or [],
        )
        if not params.get('image_url'):
            previous_frame = next((
                item for item in params['input_artifacts']
                if item.get('role') == 'previous_final_frame' and item.get('uri')
            ), None)
            if previous_frame:
                params['image_url'] = previous_frame['uri']
        return params

    @staticmethod
    def _resolve_dependency_artifacts(work_item, requested):
        dependencies = list(work_item.depends_on.prefetch_related('artifacts').all())
        by_logical_key = {
            str((dependency.route_snapshot or {}).get('logical_key') or ''): dependency
            for dependency in dependencies
        }
        resolved = []
        for raw in requested:
            item = dict(raw) if isinstance(raw, dict) else {'uri': str(raw)}
            dependency_key = str(item.pop('from_dependency', '') or '')
            if not dependency_key:
                resolved.append(item)
                continue
            dependency = by_logical_key.get(dependency_key)
            if dependency is None or dependency.status != 'succeeded':
                failure = RuntimeError(f'前置工作项 {dependency_key} 尚未成功')
                failure.code = 'INPUT_MISSING'
                raise failure
            artifact = dependency.artifacts.filter(status='ready').order_by(
                '-protected', '-created_at'
            ).first()
            if artifact is None:
                failure = RuntimeError(f'前置工作项 {dependency_key} 没有可用产物')
                failure.code = 'INPUT_MISSING'
                raise failure
            item['uri'] = artifact.uri
            item['artifact_id'] = str(artifact.pk)
            # 合成器需要每段真实时长来计算串联 xfade 偏移；这些都是已校验
            # MediaArtifact 的结构化元数据，不信任调用方在 placeholder 中自报。
            item['sha256'] = artifact.sha256
            item['file_size'] = artifact.file_size
            item['width'] = artifact.width
            item['height'] = artifact.height
            item['duration_seconds'] = float(artifact.duration_seconds or 0)
            item['fps'] = float(artifact.fps or 0)
            resolved.append(item)
        return resolved

    @staticmethod
    def _usage_estimate(work_item) -> Dict[str, Any]:
        configured = (work_item.request_parameters or {}).get('usage_estimate') or {}
        return {**configured, **(work_item.usage or {})}

    @staticmethod
    def _usage_from_log(log) -> Dict[str, Any]:
        return {
            'input_tokens': int(getattr(log, 'input_tokens', 0) or 0),
            'output_tokens': int(getattr(log, 'output_tokens', 0) or 0),
            'image_count': int(getattr(log, 'image_count', 0) or 0),
            'video_seconds': float(getattr(log, 'video_seconds', 0) or 0),
        }

    @staticmethod
    def _extract_job_id(value, provider) -> str:
        if getattr(provider, 'deployment_mode', '') != 'local':
            return ''
        if isinstance(value, dict):
            metadata = value.get('metadata') or {}
            return str(value.get('job_id') or value.get('id') or metadata.get('job_id') or '')
        metadata = getattr(value, 'metadata', {}) or {}
        return str(metadata.get('job_id') or metadata.get('task_id') or '')

    def _handle_execution_failure(self, running, lease_owner: str, error: Exception):
        classification = InferenceErrorClassifier.classify(error=error)
        code = classification.code or getattr(error, 'code', '') or 'RUNTIME_CRASH'
        error_payload = {
            'category': classification.category,
            'code': code,
            'message': str(error),
        }
        current = GenerationWorkItem.objects.get(pk=running.pk)
        if current.status in TERMINAL_STATES:
            # 用户取消与模型完成可能同时发生。终态以数据库为准，迟到的 worker
            # 不得再把 cancelled/succeeded 改写为 failed。
            return current
        paid_ambiguous = self._mark_paid_ambiguous(current, error)
        if code == 'RESOURCE_BUSY' and not paid_ambiguous:
            deferred = WorkItemService.schedule_retry(
                current,
                delay_seconds=max(
                    1, int((current.request_parameters or {}).get('resource_retry_seconds', 15))
                ),
                lease_owner=lease_owner,
                error=error_payload,
                expected_version=current.version,
                now=self.now(),
            )
            # 资源等待不是一次模型尝试，不消耗 max_attempts，也绝不触发付费回退。
            GenerationWorkItem.objects.filter(
                pk=deferred.pk, attempt_count__gt=0
            ).update(attempt_count=F('attempt_count') - 1)
            deferred.refresh_from_db()
            return deferred
        if (
            not paid_ambiguous
            and InferenceErrorClassifier.can_auto_fallback(classification)
            and current.attempt_count < current.max_attempts
        ):
            delay = max(0, int((current.request_parameters or {}).get('retry_delay_seconds', 30)))
            retry_updates = {}
            if (current.route_snapshot or {}).get('automatic_route'):
                # 自动工作项每轮都应重新解析完整 Route；Agent job 回调临时写入的
                # Provider 只是恢复事实，不能把下一轮退化成显式单目标执行。
                retry_updates = {
                    'provider': None,
                    'runtime_node': None,
                    'target': None,
                    'agent_job_id': '',
                }
            return WorkItemService.schedule_retry(
                current,
                delay_seconds=delay,
                lease_owner=lease_owner,
                error=error_payload,
                expected_version=current.version,
                now=self.now(),
                **retry_updates,
            )
        if paid_ambiguous:
            error_payload = {
                'category': 'manual_review',
                'code': 'PAID_RESULT_AMBIGUOUS',
                'message': '付费请求是否计费无法确认，已保留预算并禁止自动重提',
            }
        return WorkItemService.fail(
            current,
            error=error_payload,
            lease_owner=lease_owner,
            expected_version=current.version,
            now=self.now(),
        )

    @staticmethod
    def _mark_paid_ambiguous(work_item, reason) -> bool:
        """付费提交状态不明时保持预留，绝不能用 Celery 重试再次扣费。"""

        reservations = list(
            BudgetReservation.objects.filter(work_item=work_item).exclude(
                status__in=['released', 'expired']
            )
        )
        for reservation in reservations:
            if reservation.status == 'active':
                PaidCallGate.mark_ambiguous(reservation, reason)
        # 没有预算预留说明调用在真正外部提交前已被三重门拒绝；不能仅因 Provider
        # 类型是 API 就误报“可能已计费”，否则预算 0/未授权错误会被错误锁死。
        return bool(reservations)

    def reconcile(self, *, limit: int = 100, enqueue: Optional[Callable] = None) -> Dict[str, int]:
        """回收租约并恢复可证明安全的任务。

        running 任务只有三种自动动作：Agent 明确报告状态；本地幂等调用进入重试；
        付费状态不明则进入人工核对。Agent 查询失败时保持 running，不猜测完成状态，
        也不重新提交。
        """

        now = self.now()
        limit = max(1, min(int(limit), 1000))
        stats = {
            'released_leases': 0,
            'agent_recovered': 0,
            'agent_deferred': 0,
            'retry_released': 0,
            'paid_manual_review': 0,
            'failed': 0,
            'dependency_failed': 0,
            'enqueued': 0,
            'enqueue_failed': 0,
        }
        enqueue_ids = {}
        touched_stages = set()

        expired_leased = list(
            GenerationWorkItem.objects.filter(
                status='leased', lease_expires_at__lte=now
            ).order_by('lease_expires_at').values_list('pk', flat=True)[:limit]
        )
        for item_id in expired_leased:
            item = GenerationWorkItem.objects.get(pk=item_id)
            try:
                released = WorkItemService.release_lease(
                    item,
                    lease_owner=item.lease_owner,
                    expected_version=item.version,
                    now=now,
                )
            except InvalidWorkItemTransition:
                continue
            stats['released_leases'] += 1
            enqueue_ids.setdefault(released.pk, None)
            touched_stages.add((released.project_id, released.stage_type))

        expired_running = list(
            GenerationWorkItem.objects.select_related('runtime_node', 'provider__runtime_node').filter(
                status='running', lease_expires_at__lte=now
            ).order_by('lease_expires_at')[:limit]
        )
        for item in expired_running:
            touched_stages.add((item.project_id, item.stage_type))
            outcome = self._reconcile_running(item, now)
            stats[outcome] = stats.get(outcome, 0) + 1
            current = GenerationWorkItem.objects.get(pk=item.pk)
            if current.status == 'retry_wait' and current.next_retry_at and current.next_retry_at <= now:
                current = WorkItemService.retry(current, expected_version=current.version, now=now)
                enqueue_ids.setdefault(current.pk, None)
                stats['retry_released'] += 1

        due_retries = list(
            GenerationWorkItem.objects.filter(
                status='retry_wait', next_retry_at__lte=now
            ).order_by('-priority', 'next_retry_at').values_list('pk', flat=True)[:limit]
        )
        for item_id in due_retries:
            item = GenerationWorkItem.objects.get(pk=item_id)
            try:
                waiting = WorkItemService.retry(item, expected_version=item.version, now=now)
            except InvalidWorkItemTransition:
                continue
            stats['retry_released'] += 1
            enqueue_ids.setdefault(waiting.pk, None)
            touched_stages.add((waiting.project_id, waiting.stage_type))

        blocked_ids = list(
            GenerationWorkItem.objects.filter(
                status='waiting', depends_on__status__in=['failed', 'cancelled']
            ).distinct().order_by('created_at').values_list('pk', flat=True)[:limit]
        )
        for item_id in blocked_ids:
            item = GenerationWorkItem.objects.get(pk=item_id)
            try:
                failed = WorkItemService.fail(
                    item,
                    error={
                        'category': 'dependency',
                        'code': 'DEPENDENCY_FAILED',
                        'message': '前置工作项失败或取消，当前工作项未提交模型。',
                    },
                    expected_version=item.version,
                    now=now,
                )
            except InvalidWorkItemTransition:
                continue
            stats['dependency_failed'] += 1
            touched_stages.add((failed.project_id, failed.stage_type))

        # 周期调度器也扫描尚未发出消息的 waiting 项，消息投递失败时下一轮自然补发。
        waiting_ids = self._fair_waiting_ids(now, limit)
        for item_id in waiting_ids:
            enqueue_ids.setdefault(item_id, None)

        if enqueue:
            for item_id in list(enqueue_ids)[:limit]:
                try:
                    enqueue(str(item_id))
                    stats['enqueued'] += 1
                except Exception as error:  # broker 暂不可用时保持 waiting，下一轮继续
                    logger.warning('生成工作项消息投递失败 work_item=%s error=%s', item_id, error)
                    stats['enqueue_failed'] += 1

        for project_id, stage_type in touched_stages:
            self.aggregate_stage(project_id, stage_type)
        return stats

    @staticmethod
    def _fair_waiting_ids(now, limit: int) -> list:
        """同优先级按项目轮询，避免一个大项目长期占满单 GPU 队列。"""

        candidate_limit = min(max(limit * 20, limit), 5000)
        rows = list(
            GenerationWorkItem.objects.filter(status='waiting').filter(
                Q(scheduled_at__isnull=True) | Q(scheduled_at__lte=now)
            ).exclude(
                depends_on__status__in=[
                    'waiting', 'leased', 'running', 'retry_wait', 'failed', 'cancelled',
                ]
            ).order_by('-priority', 'created_at').distinct().values(
                'pk', 'project_id', 'priority'
            )[:candidate_limit]
        )
        by_priority: Dict[int, Dict[Any, list]] = {}
        for row in rows:
            project_queues = by_priority.setdefault(row['priority'], {})
            project_queues.setdefault(row['project_id'], []).append(row['pk'])

        selected = []
        for priority in sorted(by_priority, reverse=True):
            project_queues = by_priority[priority]
            while project_queues and len(selected) < limit:
                for project_id in list(project_queues):
                    queue = project_queues[project_id]
                    selected.append(queue.pop(0))
                    if not queue:
                        project_queues.pop(project_id)
                    if len(selected) >= limit:
                        break
            if len(selected) >= limit:
                break
        return selected

    def _reconcile_running(self, item, now) -> str:
        node = item.runtime_node or getattr(item.provider, 'runtime_node', None)
        if item.agent_job_id and node:
            try:
                job = self.agent_control.get_job(node, item.agent_job_id)
            except Exception as error:
                # Agent 离线不等于执行失败。此处不续一个“已验证”租约，也不重提，
                # 保留过期 running 供下一轮再次查询或人工处置。
                logger.warning('Runtime Agent 任务状态暂不可确认 work_item=%s error=%s', item.pk, error)
                return 'agent_deferred'

            status = str(job.get('status') or '').lower()
            if status in ACTIVE_AGENT_STATES:
                self._renew_verified_running_lease(item.pk, now)
                return 'agent_recovered'
            if status == 'succeeded':
                try:
                    recovered = self.agent_control.materialize_artifacts(node, job, item)
                    if item.capability != 'llm' and not recovered:
                        failure = RuntimeError('Runtime Agent 成功状态缺少媒体产物')
                        failure.code = 'EMPTY_OUTPUT'
                        raise failure
                except Exception as error:
                    classification = InferenceErrorClassifier.classify(error=error)
                    code = classification.code or getattr(error, 'code', '') or 'ARTIFACT_DOWNLOAD_FAILED'
                    current = GenerationWorkItem.objects.get(pk=item.pk)
                    if current.attempt_count < current.max_attempts:
                        WorkItemService.schedule_retry(
                            current,
                            delay_seconds=30,
                            lease_owner=current.lease_owner,
                            error={
                                'category': 'technical_failure',
                                'code': code,
                                'message': str(error),
                            },
                            expected_version=current.version,
                            now=now,
                        )
                        return 'agent_recovered'
                    WorkItemService.fail(
                        current,
                        lease_owner=current.lease_owner,
                        error={
                            'category': 'technical_failure',
                            'code': code,
                            'message': str(error),
                        },
                        expected_version=current.version,
                        now=now,
                    )
                    return 'failed'
                recovered_job = {**job, 'artifacts': recovered}
                self._record_artifacts(item, recovered_job)
                WorkItemResultProjector.persist(item, recovered_job, item.provider)
                completed = WorkItemService.succeed(
                    item,
                    lease_owner=item.lease_owner,
                    expected_version=item.version,
                    now=now,
                )
                self._enqueue_ready_dependents(completed)
                return 'agent_recovered'
            if status == 'cancelled':
                WorkItemService.cancel(item, expected_version=item.version, now=now)
                return 'agent_recovered'
            if status == 'failed':
                error = job.get('error') or {}
                failure = RuntimeError(error.get('message') or 'Runtime Agent 任务失败')
                failure.code = error.get('code') or 'RUNTIME_CRASH'
                current = GenerationWorkItem.objects.get(pk=item.pk)
                if (
                    InferenceErrorClassifier.can_auto_fallback(
                        InferenceErrorClassifier.classify(error=failure)
                    )
                    and current.attempt_count < current.max_attempts
                ):
                    WorkItemService.schedule_retry(
                        current,
                        delay_seconds=30,
                        lease_owner=current.lease_owner,
                        error={'category': 'technical_failure', 'code': failure.code, 'message': str(failure)},
                        expected_version=current.version,
                        now=now,
                    )
                    return 'agent_recovered'
                WorkItemService.fail(
                    current,
                    lease_owner=current.lease_owner,
                    error={'category': 'technical_failure', 'code': failure.code, 'message': str(failure)},
                    expected_version=current.version,
                    now=now,
                )
                return 'failed'
            return 'agent_deferred'

        if self._mark_paid_ambiguous(item, 'worker_restarted_with_unknown_submission_state'):
            WorkItemService.fail(
                item,
                lease_owner=item.lease_owner,
                error={
                    'category': 'manual_review',
                    'code': 'PAID_RESULT_AMBIGUOUS',
                    'message': 'worker 重启后无法确认付费请求状态，禁止自动重复提交',
                },
                expected_version=item.version,
                now=now,
            )
            return 'paid_manual_review'

        # 本地/Mock 工作项仍携带固定幂等键，重新提交会命中 Runtime Agent journal；
        # 因而可在剩余尝试次数内恢复，而不产生第二份 GPU 作业。
        if item.attempt_count < item.max_attempts:
            WorkItemService.schedule_retry(
                item,
                delay_seconds=0,
                lease_owner=item.lease_owner,
                error={
                    'category': 'technical_failure',
                    'code': 'RUNTIME_CRASH',
                    'message': 'worker 中断，按原幂等键恢复本地任务',
                },
                expected_version=item.version,
                now=now,
            )
            return 'agent_recovered'
        WorkItemService.fail(
            item,
            lease_owner=item.lease_owner,
            error={
                'category': 'technical_failure',
                'code': 'RUNTIME_CRASH',
                'message': 'worker 中断且已达到最大尝试次数',
            },
            expected_version=item.version,
            now=now,
        )
        return 'failed'

    def _renew_verified_running_lease(self, item_id, now):
        """Agent 明确仍在执行时才允许恢复已过期的数据库租约。"""

        with transaction.atomic():
            item = _locked_queryset(GenerationWorkItem.objects.filter(pk=item_id)).get()
            if item.status != 'running':
                return item
            item.heartbeat_at = now
            item.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
            # 续租只延长同一执行者的时间窗口，不是业务状态迁移。若这里递增
            # version，仍在正常轮询 Agent 的原 worker 会在写终态时发生 CAS 冲突。
            item.save(update_fields=['heartbeat_at', 'lease_expires_at', 'updated_at'])
            return item

    def cancel(self, work_item_id) -> Dict[str, Any]:
        """先固化用户取消意图，再尽力传播到 Runtime Agent。"""

        item = GenerationWorkItem.objects.select_related('runtime_node', 'provider__runtime_node').get(
            pk=work_item_id
        )
        if item.status == 'cancelled':
            return self._result(item, idempotent=True, agent_cancelled=bool(item.agent_job_id))
        if item.status in {'succeeded', 'failed'}:
            return self._result(item, idempotent=True, agent_cancelled=False)

        cancelled = WorkItemService.cancel(item, expected_version=item.version, now=self.now())
        agent_cancelled = False
        cancel_error = ''
        node = item.runtime_node or getattr(item.provider, 'runtime_node', None)
        if item.agent_job_id and node:
            try:
                self.agent_control.cancel_job(node, item.agent_job_id)
                agent_cancelled = True
            except Exception as error:
                # 数据库已是 cancelled，迟到的 Agent 结果只会被忽略；错误留给运维查看。
                cancel_error = str(error)
                logger.warning('Runtime Agent 取消传播失败 work_item=%s error=%s', item.pk, error)
        self.aggregate_stage(cancelled.project_id, cancelled.stage_type)
        publish_work_item_event(cancelled, 'cancelled', agent_cancelled=agent_cancelled)
        return self._result(
            cancelled,
            agent_cancelled=agent_cancelled,
            agent_cancel_error=cancel_error,
        )

    @staticmethod
    def aggregate_stage(project_id, stage_type: str) -> Optional[Dict[str, Any]]:
        """只按数据库工作项终态聚合阶段，不依赖 worker 内存计数。"""

        if not stage_type:
            return None
        queryset = GenerationWorkItem.objects.filter(project_id=project_id, stage_type=stage_type)
        counts = {row['status']: row['count'] for row in queryset.values('status').annotate(count=Count('id'))}
        total = sum(counts.values())
        if total == 0:
            return None
        stage = ProjectStage.objects.filter(project_id=project_id, stage_type=stage_type).first()
        if stage is None:
            return None

        nonterminal = total - sum(counts.get(state, 0) for state in TERMINAL_STATES)
        summary = {'total': total, 'nonterminal': nonterminal, **counts}
        stage.output_data = {**(stage.output_data or {}), 'generation_work_items': summary}
        update_fields = ['output_data']
        if nonterminal:
            if stage.status in {'pending', 'failed'}:
                stage.status = 'processing'
                stage.completed_at = None
                update_fields.extend(['status', 'completed_at'])
        else:
            has_failure = bool(counts.get('failed') or counts.get('cancelled'))
            stage.status = 'failed' if has_failure else 'completed'
            stage.completed_at = timezone.now()
            stage.error_message = (
                '存在失败或取消的生成工作项，请按工作项重试。' if has_failure else ''
            )
            update_fields.extend(['status', 'completed_at', 'error_message'])
        stage.save(update_fields=list(dict.fromkeys(update_fields)))
        return summary

    def _record_artifacts(self, work_item, value) -> list:
        artifacts = self._artifact_items(value)
        if not artifacts:
            return []
        requested_lifecycle = str(
            (work_item.request_parameters or {}).get('artifact_lifecycle', 'intermediate')
        )
        lifecycle = requested_lifecycle if requested_lifecycle in {
            'source', 'intermediate', 'failed', 'final'
        } else 'intermediate'
        expires_at = None
        if lifecycle in {'intermediate', 'failed'}:
            expires_at = self.now() + timedelta(
                days=max(1, int(getattr(settings, 'AI_INTERMEDIATE_RETENTION_DAYS', 30)))
            )
        kind = {
            'llm': 'text',
            'text2image': 'image',
            'image_edit': 'image',
            'image2video': 'video',
            'motion_render': 'video',
        }.get(work_item.capability, 'metadata')
        persisted = []
        for index, raw in enumerate(artifacts):
            item = dict(raw) if isinstance(raw, dict) else {'uri': str(raw)}
            uri = str(
                item.get('storage_path') or item.get('uri') or item.get('url')
                or item.get('path') or ''
            )
            if not uri:
                continue
            checksum = str(item.get('sha256') or item.get('checksum') or '')
            artifact, _ = MediaArtifact.objects.get_or_create(
                work_item=work_item,
                uri=uri,
                defaults={
                    'project_id': work_item.project_id,
                    'kind': str(item.get('kind') or kind),
                    'status': 'ready',
                    'lifecycle': lifecycle,
                    'storage_backend': str(item.get('storage_backend') or 'local'),
                    'mime_type': str(item.get('content_type') or item.get('mime_type') or ''),
                    'checksum': checksum,
                    'sha256': checksum if len(checksum) == 64 else '',
                    'file_size': int(item.get('file_size') or 0),
                    'width': int(item.get('width') or 0),
                    'height': int(item.get('height') or 0),
                    'duration_seconds': Decimal(str(item.get('duration_seconds') or item.get('duration') or 0)),
                    'fps': Decimal(str(item.get('fps') or 0)),
                    'segment_index': work_item.segment_index if work_item.segment_index is not None else index,
                    'metadata': mask_sensitive_data({
                        key: item.get(key) for key in ('format', 'artifact_id', 'filename') if item.get(key)
                    }),
                    'expires_at': expires_at,
                    'protected': lifecycle in {'source', 'final'},
                },
            )
            persisted.append(artifact)
        return persisted

    @staticmethod
    def _enqueue_ready_dependents(work_item) -> None:
        """前置项成功后只唤醒依赖全部完成的后续项。"""

        ready_ids = []
        for dependent in work_item.dependent_work_items.filter(status='waiting'):
            if not dependent.depends_on.exclude(status='succeeded').exists():
                ready_ids.append(str(dependent.pk))
        if not ready_ids:
            return

        def enqueue():
            from ..tasks import enqueue_work_item

            for item_id in ready_ids:
                enqueue_work_item.apply_async(args=[item_id], queue='orchestration')

        transaction.on_commit(enqueue)

    @staticmethod
    def _artifact_items(value) -> Iterable[Any]:
        if isinstance(value, dict):
            data = value.get('artifacts') or value.get('data') or []
        else:
            data = getattr(value, 'data', None) or []
        if isinstance(data, dict):
            data = data.get('artifacts') or data.get('items') or [data]
        if isinstance(data, (str, bytes)):
            return [data]
        return list(data or [])

    @staticmethod
    def _result(work_item, **extra) -> Dict[str, Any]:
        return {
            'work_item_id': str(work_item.pk),
            'status': work_item.status,
            'attempt_count': work_item.attempt_count,
            **extra,
        }


class ArtifactRetentionService:
    """只清理已确认过期且位于中央存储根目录内的中间/失败产物。"""

    @classmethod
    def cleanup(cls, *, limit: int = 200, now=None) -> Dict[str, int]:
        if not getattr(settings, 'AI_ARTIFACT_CLEANUP_ENABLED', False):
            return {'enabled': 0, 'deleted': 0, 'missing': 0, 'unsafe': 0, 'failed': 0}

        now = now or timezone.now()
        root = Path(settings.STORAGE_ROOT).resolve(strict=False)
        artifact_ids = list(
            MediaArtifact.objects.filter(
                lifecycle__in=['intermediate', 'failed'],
                expires_at__lte=now,
                protected=False,
                deleted_at__isnull=True,
                storage_backend='local',
            ).exclude(status='deleted').order_by('expires_at').values_list('pk', flat=True)[:max(1, limit)]
        )
        stats = {'enabled': 1, 'deleted': 0, 'missing': 0, 'unsafe': 0, 'failed': 0}
        for artifact_id in artifact_ids:
            with transaction.atomic():
                artifact = _locked_queryset(MediaArtifact.objects.filter(pk=artifact_id)).get()
                if (
                    artifact.lifecycle not in {'intermediate', 'failed'}
                    or artifact.protected
                    or artifact.deleted_at
                    or not artifact.expires_at
                    or artifact.expires_at > now
                ):
                    continue
                path = cls._safe_local_path(root, artifact.uri)
                if path is None:
                    stats['unsafe'] += 1
                    continue
                try:
                    if path.exists():
                        if not path.is_file():
                            stats['unsafe'] += 1
                            continue
                        path.unlink()
                        stats['deleted'] += 1
                    else:
                        stats['missing'] += 1
                except OSError as error:
                    logger.warning('AI 中间产物清理失败 artifact=%s error=%s', artifact.pk, error)
                    stats['failed'] += 1
                    continue
                artifact.status = 'deleted'
                artifact.deleted_at = now
                artifact.save(update_fields=['status', 'deleted_at', 'updated_at'])
        return stats

    @staticmethod
    def _safe_local_path(root: Path, uri: str) -> Optional[Path]:
        raw = str(uri or '').strip()
        if not raw:
            return None
        candidate = Path(raw)
        if not candidate.is_absolute():
            parsed = urlparse(raw)
            if parsed.scheme or parsed.netloc:
                return None
            normalized = raw.replace('\\', '/').lstrip('/')
            storage_prefix = str(getattr(settings, 'STORAGE_URL', 'storage/')).strip('/')
            if storage_prefix and normalized.startswith(f'{storage_prefix}/'):
                normalized = normalized[len(storage_prefix) + 1:]
            candidate = root / normalized
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError:
            return None
        return resolved
