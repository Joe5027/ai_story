"""Django 到本地 AI Runtime Agent 的兼容执行器。

这些执行器只负责把现有 Provider 调用协议转换为 Agent Job 协议。GPU 锁、
模型进程和本地任务恢复由 Agent 自己负责；付费回退、预算和隐私判断必须留在
Django 路由层，不能下沉到这个客户端中。
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import time
import uuid
from datetime import date
from pathlib import Path
from typing import Any, Dict, Generator, Iterable, Optional

import requests
from django.conf import settings

from .base import AIResponse, Image2VideoClient, ImageEditClient, LLMClient, Text2ImageClient
from .schemas import ImageEditRequest, Text2ImageRequest


class RuntimeAgentError(RuntimeError):
    """保留 Agent 稳定错误码，供上层路由器判断是否允许技术回退。"""

    def __init__(self, code: str, message: str, retryable: bool = False, details=None):
        super().__init__(message)
        self.code = code or 'RUNTIME_CRASH'
        self.retryable = bool(retryable)
        self.details = details or {}


class RuntimeAgentTransport:
    """同步提交并轮询 Agent Job，适配现有同步图片/视频处理器。"""

    TERMINAL_STATES = {'succeeded', 'failed', 'cancelled'}

    def __init__(self, api_url: str, api_key: str, model_name: str, **config):
        self.api_url = api_url.rstrip('/')
        self.api_key = api_key
        self.model_name = model_name
        self.config = config
        self.timeout = int(config.get('timeout', 300))
        self.poll_interval = max(float(config.get('poll_interval', 1.0)), 0.1)
        self.on_job_submitted = None

    @property
    def headers(self) -> Dict[str, str]:
        headers = {'Content-Type': 'application/json'}
        if self.api_key:
            headers['Authorization'] = f'Bearer {self.api_key}'
        return headers

    def health(self) -> Dict[str, Any]:
        response = requests.get(
            f'{self.api_url}/v1/health/ready',
            headers=self.headers,
            timeout=min(self.timeout, 10),
        )
        self._raise_for_error(response)
        return response.json()

    def run_job(
        self,
        capability: str,
        prompt: str,
        *,
        negative_prompt: str = '',
        input_artifacts: Optional[Iterable[Any]] = None,
        output_spec: Optional[Dict[str, Any]] = None,
        seed: Optional[int] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        work_item_id = str(kwargs.pop('work_item_id', '') or uuid.uuid4())
        payload = {
            'capability': capability,
            'model_id': kwargs.pop('model_id', '') or self.model_name,
            'profile': kwargs.pop('profile', '') or self.config.get('profile', 'draft'),
            'prompt': prompt or '',
            'negative_prompt': negative_prompt or '',
            'input_artifacts': list(input_artifacts or []),
            'output_spec': output_spec or {},
            'seed': seed,
            'workflow_version': kwargs.pop('workflow_version', '') or self.config.get('workflow_version', 'mock-v1'),
            'project_id': str(kwargs.pop('project_id', '') or self.config.get('project_id', 'legacy')),
            'stage_type': kwargs.pop('stage_type', '') or capability,
            'work_item_id': work_item_id,
        }
        extra = {key: value for key, value in kwargs.items() if value is not None}
        if extra:
            payload['output_spec']['runtime_overrides'] = extra

        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
        idempotency_key = str(
            payload['output_spec'].pop('idempotency_key', '')
            or self.config.get('idempotency_key', '')
            or hashlib.sha256(f'{canonical}:{work_item_id}'.encode('utf-8')).hexdigest()
        )
        headers = {
            **self.headers,
            'Idempotency-Key': idempotency_key,
            'X-Request-ID': str(self.config.get('request_id') or uuid.uuid4()),
        }
        response = requests.post(
            f'{self.api_url}/v1/jobs',
            headers=headers,
            json=payload,
            timeout=min(self.timeout, 30),
        )
        self._raise_for_error(response)
        job = response.json()
        job_id = job.get('id') or job.get('job_id')
        if not job_id:
            raise RuntimeAgentError('OUTPUT_SCHEMA_INVALID', 'Runtime Agent 未返回 job_id')

        # Agent 已接受任务后立即把 job_id 交给 Django 持久化。不能等轮询结束后
        # 再保存，否则 worker 在“提交成功、结果返回前”退出时，reconciler 无法
        # 查询原任务，取消操作也无法传播到 Agent。
        if callable(self.on_job_submitted):
            try:
                self.on_job_submitted(str(job_id), job)
            except Exception:
                # 若用户在 Agent 接单后立刻取消，Django 会拒绝把 job_id 写到
                # 已终止工作项；此时必须尽力撤销刚创建的 Agent 作业，避免孤儿 GPU 任务。
                try:
                    requests.delete(
                        f'{self.api_url}/v1/jobs/{job_id}',
                        headers=headers,
                        timeout=min(self.timeout, 30),
                    )
                finally:
                    raise

        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            current = requests.get(
                f'{self.api_url}/v1/jobs/{job_id}',
                headers=headers,
                timeout=min(self.timeout, 30),
            )
            self._raise_for_error(current)
            job = current.json()
            if job.get('status') in self.TERMINAL_STATES:
                break
            time.sleep(self.poll_interval)
        else:
            raise RuntimeAgentError('EXECUTION_TIMEOUT', '等待 Runtime Agent 任务超时', retryable=True)

        if job.get('status') != 'succeeded':
            error = job.get('error') or {}
            raise RuntimeAgentError(
                error.get('code', 'RUNTIME_CRASH'),
                error.get('message', f'Runtime Agent 任务状态为 {job.get("status")}'),
                error.get('retryable', False),
                error.get('details'),
            )
        return job

    def artifact_items(self, job: Dict[str, Any], category: str = 'runtime') -> list:
        """将受保护的 Agent 产物原子下载到 Django 中央存储。

        前端不能持有 Runtime Agent Token，因此不能直接保存 Agent 下载 URL。
        文件先写入 `.partial`，SHA-256 验证通过后才进入业务可见路径。
        """
        items = []
        for artifact in job.get('artifacts') or []:
            item = dict(artifact) if isinstance(artifact, dict) else {'artifact_id': str(artifact)}
            artifact_id = item.get('artifact_id') or item.get('id')
            if artifact_id:
                response = requests.get(
                    f'{self.api_url}/v1/artifacts/{artifact_id}',
                    headers=self.headers,
                    stream=True,
                    timeout=self.timeout,
                )
                self._raise_for_error(response)
                content_type = response.headers.get('Content-Type', '').split(';', 1)[0]
                suffix = Path(str(item.get('filename') or '')).suffix
                if not suffix:
                    suffix = mimetypes.guess_extension(content_type) or '.bin'
                relative_dir = Path(category) / date.today().isoformat()
                target_dir = Path(settings.STORAGE_ROOT) / relative_dir
                target_dir.mkdir(parents=True, exist_ok=True)
                target = target_dir / f'{uuid.uuid4().hex}{suffix}'
                partial = target.with_suffix(f'{target.suffix}.partial')
                digest = hashlib.sha256()
                with partial.open('wb') as stream:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            stream.write(chunk)
                            digest.update(chunk)
                actual_sha256 = digest.hexdigest()
                expected_sha256 = str(item.get('sha256') or '').lower()
                if expected_sha256 and actual_sha256 != expected_sha256:
                    partial.unlink(missing_ok=True)
                    raise RuntimeAgentError(
                        'CHECKSUM_MISMATCH',
                        'Runtime Agent 产物摘要校验失败',
                        retryable=True,
                        details={'expected': expected_sha256, 'actual': actual_sha256},
                    )
                partial.replace(target)
                item.update({
                    'url': f'/{settings.STORAGE_URL.strip("/")}/{relative_dir.as_posix()}/{target.name}',
                    'storage_path': str(target),
                    'sha256': actual_sha256,
                    'file_size': target.stat().st_size,
                    'content_type': content_type,
                })
            items.append(item)
        return items

    @staticmethod
    def _raise_for_error(response) -> None:
        if response.status_code < 400:
            return
        try:
            body = response.json()
        except ValueError:
            body = {}
        error = body.get('error') or {}
        raise RuntimeAgentError(
            error.get('code', 'NODE_UNAVAILABLE'),
            error.get('message', f'Runtime Agent HTTP {response.status_code}'),
            error.get('retryable', response.status_code >= 500),
            error.get('details'),
        )


class RuntimeAgentLLMClient(LLMClient):
    """本地 LLM 执行器；旧流式界面在任务结束后一次性发回文本。"""

    def __init__(self, api_url: str, api_key: str, model_name: str, **kwargs):
        super().__init__(api_url, api_key, model_name, **kwargs)
        self.transport = RuntimeAgentTransport(api_url, api_key, model_name, **kwargs)

    def _generate_text(self, prompt: str, max_tokens: int = 2000, temperature: float = 0.7, **kwargs):
        try:
            job = self.transport.run_job(
                'llm', prompt,
                output_spec={'max_tokens': max_tokens, 'temperature': temperature},
                **kwargs,
            )
            result = job.get('result') or {}
            return AIResponse(success=True, text=result.get('text', ''), metadata=result.get('metadata', {}))
        except RuntimeAgentError as error:
            return AIResponse(success=False, error=f'{error.code}: {error}')

    def generate_stream(self, prompt: str, system_prompt: str = '', max_tokens: int = 2000,
                        temperature: float = 0.7, **kwargs) -> Generator[Dict[str, Any], None, None]:
        if system_prompt:
            kwargs['system_prompt'] = system_prompt
        result = self._generate_text(prompt, max_tokens=max_tokens, temperature=temperature, **kwargs)
        if not result.success:
            yield {'type': 'error', 'error': result.error}
            return
        if result.text:
            yield {'type': 'token', 'content': result.text, 'full_text': result.text}
        yield {'type': 'done', 'full_text': result.text, 'metadata': result.metadata}

    def validate_config(self) -> bool:
        try:
            return bool(self.api_url and self.model_name and self.transport.health())
        except Exception:
            return False


class RuntimeAgentText2ImageClient(Text2ImageClient):
    """Runtime Agent 文生图执行器。"""

    def __init__(self, api_url: str, api_key: str, model_name: str, **kwargs):
        super().__init__(api_url, api_key, model_name, **kwargs)
        self.transport = RuntimeAgentTransport(api_url, api_key, model_name, **kwargs)

    def _generate_image(self, prompt: str, negative_prompt: str = '', width: int = 1024,
                        height: int = 1024, steps: int = 20, **kwargs) -> AIResponse:
        try:
            job = self.transport.run_job(
                'text2image', prompt,
                negative_prompt=negative_prompt,
                output_spec={'width': width, 'height': height, 'steps': steps},
                seed=kwargs.pop('seed', None),
                **kwargs,
            )
            return AIResponse(success=True, data=self.transport.artifact_items(job, 'image'), metadata=job.get('result') or {})
        except RuntimeAgentError as error:
            return AIResponse(success=False, error=f'{error.code}: {error}')

    def generate_from_text2image_request(self, request: Text2ImageRequest) -> AIResponse:
        extra = dict(request.extra or {})
        steps = int(extra.pop('steps', 20))
        return self._generate_image(
            request.prompt,
            request.negative_prompt,
            request.width or 1024,
            request.height or 1024,
            steps,
            seed=request.seed,
            input_artifacts=request.reference_images,
            **extra,
        )

    def validate_config(self) -> bool:
        try:
            return bool(self.api_url and self.model_name and self.transport.health())
        except Exception:
            return False


class RuntimeAgentImageEditClient(ImageEditClient):
    """Runtime Agent 图片编辑与多参考输入执行器。"""

    def __init__(self, api_url: str, api_key: str, model_name: str, **kwargs):
        super().__init__(api_url, api_key, model_name, **kwargs)
        self.transport = RuntimeAgentTransport(api_url, api_key, model_name, **kwargs)

    def _edit_image(self, image_url: str, prompt: str, mask_url: str = '', strength: float = 0.35,
                    width: int = 1024, height: int = 1024, **kwargs) -> AIResponse:
        sources = (
            kwargs.pop('input_artifacts', None)
            or kwargs.pop('source_images', None)
            or [image_url]
        )
        if mask_url:
            sources = [*sources, {'role': 'mask', 'uri': mask_url}]
        try:
            job = self.transport.run_job(
                'image_edit', prompt,
                negative_prompt=kwargs.pop('negative_prompt', ''),
                input_artifacts=sources,
                output_spec={'width': width, 'height': height, 'strength': strength},
                seed=kwargs.pop('seed', None),
                **kwargs,
            )
            return AIResponse(success=True, data=self.transport.artifact_items(job, 'image'), metadata=job.get('result') or {})
        except RuntimeAgentError as error:
            return AIResponse(success=False, error=f'{error.code}: {error}')

    def generate_from_image_edit_request(self, request: ImageEditRequest) -> AIResponse:
        return self._edit_image(
            request.primary_source_image,
            request.prompt,
            request.mask_image,
            request.strength,
            request.width or 1024,
            request.height or 1024,
            source_images=request.source_images,
            negative_prompt=request.negative_prompt,
            **(request.extra or {}),
        )

    def validate_config(self) -> bool:
        try:
            return bool(self.api_url and self.model_name and self.transport.health())
        except Exception:
            return False


class RuntimeAgentImage2VideoClient(Image2VideoClient):
    """Runtime Agent 图生视频执行器，时长分段由 Agent 固定工作流处理。"""

    def __init__(self, api_url: str, api_key: str, model_name: str, **kwargs):
        super().__init__(api_url, api_key, model_name, **kwargs)
        self.transport = RuntimeAgentTransport(api_url, api_key, model_name, **kwargs)

    def _generate_video(self, image_url: str = '', camera_movement: Optional[Dict[str, Any]] = None,
                        duration: float = 5, fps: int = 24, **kwargs) -> AIResponse:
        image_url = kwargs.pop('image_uri', '') or image_url
        image_base64 = kwargs.pop('image_base64', '')
        prompt = kwargs.pop('prompt', '') or kwargs.pop('camera_movement_description', '')
        inputs = list(kwargs.pop('input_artifacts', None) or [])
        if image_url:
            inputs = inputs or [{'role': 'source', 'uri': image_url}]
        if image_base64:
            inputs.append({'role': 'source', 'base64': image_base64})
        try:
            job = self.transport.run_job(
                'image2video', prompt,
                negative_prompt=kwargs.pop('negative_prompt', ''),
                input_artifacts=inputs,
                output_spec={
                    'duration': kwargs.pop('duration_seconds', duration),
                    'fps': fps,
                    'camera_movement': camera_movement or {},
                    'resolution': kwargs.pop('resolution', None),
                    'aspect_ratio': kwargs.pop('aspect_ratio', '16:9'),
                },
                seed=kwargs.pop('seed', None),
                **kwargs,
            )
            return AIResponse(success=True, data=self.transport.artifact_items(job, 'video'), metadata=job.get('result') or {})
        except RuntimeAgentError as error:
            return AIResponse(success=False, error=f'{error.code}: {error}')

    def validate_config(self) -> bool:
        try:
            return bool(self.api_url and self.model_name and self.transport.health())
        except Exception:
            return False


class RuntimeAgentMotionRenderClient(RuntimeAgentImage2VideoClient):
    """Runtime Agent 非生成式运镜执行器，默认只使用 FFmpeg/RIFE。"""

    def _generate_video(self, image_url: str = '', camera_movement: Optional[Dict[str, Any]] = None,
                        duration: float = 10, fps: int = 24, **kwargs) -> AIResponse:
        image_url = kwargs.pop('image_uri', '') or image_url
        image_base64 = kwargs.pop('image_base64', '')
        prompt = kwargs.pop('prompt', '') or kwargs.pop('camera_movement_description', '')
        inputs = list(kwargs.pop('input_artifacts', None) or [])
        if image_url:
            inputs = inputs or [{'role': 'source', 'uri': image_url}]
        if image_base64:
            inputs.append({'role': 'source', 'base64': image_base64})
        try:
            job = self.transport.run_job(
                'motion_render', prompt,
                input_artifacts=inputs,
                output_spec={
                    'duration': kwargs.pop('duration_seconds', duration),
                    'fps': fps,
                    'camera_movement': camera_movement or {},
                    'resolution': kwargs.pop('resolution', '1280x720'),
                    'aspect_ratio': kwargs.pop('aspect_ratio', '16:9'),
                    'interpolate': kwargs.pop('interpolate', False),
                },
                seed=kwargs.pop('seed', None),
                **kwargs,
            )
            return AIResponse(
                success=True,
                data=self.transport.artifact_items(job, 'video'),
                metadata=job.get('result') or {},
            )
        except RuntimeAgentError as error:
            return AIResponse(success=False, error=f'{error.code}: {error}')
