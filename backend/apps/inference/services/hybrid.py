"""本地优先、受控云回退的统一执行入口。"""

import hashlib
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.utils import timezone

from apps.models.models import ModelUsageLog
from core.ai_client.outbound_guard import allow_paid_provider_client

from ..models import GenerationProfile, ProjectAISettings, ProviderPriceRate
from .errors import InferenceErrorClassifier
from .events import publish_work_item_event
from .gates import PaidCallDenied, PaidCallGate
from .parameters import merge_generation_parameters
from .pricing import PricingService
from .routing import NoRouteAvailable, RoutingService
from .security import mask_sensitive_data


@dataclass(frozen=True)
class HybridExecutionResult:
    value: object
    provider: object
    target: object
    usage_log: ModelUsageLog


class HybridExecutionFailed(RuntimeError):
    def __init__(self, code, message, attempts=None):
        super().__init__(message)
        self.code = code
        self.attempts = attempts or []


class _ExplicitTarget:
    def __init__(self, provider):
        self.provider = provider
        self.runtime_node = getattr(provider, 'runtime_node', None)
        self.role = 'paid_fallback' if provider.deployment_mode == 'api' else 'local_primary'
        self.max_attempts = 1
        self.id = f'explicit:{provider.id}'


class HybridInferenceService:
    """执行固定失败策略，并把每次尝试写入统一调用账本。

    自动回退只接受技术/契约错误码。主观质量不满意不会调用本服务的下一目标；
    用户必须在 UI 选择“使用 API 重生成”，届时仍会经过相同的云授权、价格和
    预算门。队列等待本身不是失败，因此也不会因为等待时间长而自动付费。
    """

    @classmethod
    def execute(
        cls,
        *,
        project,
        capability,
        stage_type,
        invoke,
        request_parameters,
        usage_estimate,
        work_item=None,
        explicit_provider=None,
        manual_api=False,
        repair_invoke=None,
    ):
        # 直接指定 API Provider 表示用户主动选择付费模型，不是“本地技术失败后的
        # 自动回退”。旧 Processor 也会传 explicit_provider，因此必须在统一入口
        # 强制区分：没有单次确认的一律拒绝，避免配置漂移或内部调用绕过前端。
        is_explicit_paid = bool(
            explicit_provider is not None
            and getattr(explicit_provider, 'deployment_mode', 'api') == 'api'
        )
        if is_explicit_paid and not manual_api:
            raise HybridExecutionFailed(
                'PAID_CONFIRMATION_REQUIRED',
                '直接选择 API Provider 必须先完成本次费用确认；自动回退只能由 V2 路由触发。',
            )
        if is_explicit_paid:
            confirmed_max = (request_parameters or {}).get('confirmed_max_cost_cny')
            try:
                confirmed_max = Decimal(str(confirmed_max))
            except (InvalidOperation, TypeError, ValueError):
                raise HybridExecutionFailed(
                    'BUDGET_DENIED', '直接 API 调用必须提交本次确认的最大费用。'
                )
            if confirmed_max < 0:
                raise HybridExecutionFailed('BUDGET_DENIED', '本次确认的最大费用不能小于 0。')

        project_settings = ProjectAISettings.objects.filter(project=project, is_active=True).first()
        shadow_decision = None
        if explicit_provider is not None:
            targets = [_ExplicitTarget(explicit_provider)]
            route = None
            if getattr(settings, 'AI_ROUTER_V2_SHADOW_MODE', False):
                shadow_decision = cls._shadow_decision(
                    project=project,
                    project_settings=project_settings,
                    capability=capability,
                    stage_type=stage_type,
                    explicit_provider=explicit_provider,
                )
        else:
            if not getattr(settings, 'AI_ROUTER_V2_ENABLED', False):
                raise HybridExecutionFailed('ROUTER_DISABLED', 'AI_ROUTER_V2_ENABLED 尚未开启')
            try:
                selection = RoutingService.select(
                    capability,
                    context={
                        'project_id': str(project.id),
                        'stage_type': stage_type,
                        'profile_code': getattr(project_settings, 'default_profile_code', 'balanced'),
                    },
                    project_settings=project_settings,
                )
            except NoRouteAvailable as error:
                raise HybridExecutionFailed('NO_ROUTE_AVAILABLE', str(error))
            route = selection.route
            targets = list(selection.targets)

        attempts = []
        previous_provider = None
        paid_target_count = 0
        for target in targets:
            provider = target.provider
            if provider is None:
                attempts.append({'target': str(target.id), 'code': 'RUNTIME_NOT_READY'})
                continue
            is_paid = provider.deployment_mode == 'api'
            if is_paid:
                paid_target_count += 1
                if paid_target_count > 2:
                    break

            max_attempts = max(1, int(getattr(target, 'max_attempts', 1)))
            # 本地技术故障最多在同一目标额外重试一次；付费目标不隐式重放。
            max_attempts = min(max_attempts, 2) if not is_paid else 1
            current_attempt = 0
            repaired_structure = False
            effective_parameters = cls._effective_parameters(
                capability=capability,
                provider=provider,
                target=target,
                route=route,
                project_settings=project_settings,
                request_parameters=request_parameters,
            )
            if work_item is not None:
                type(work_item).objects.filter(pk=work_item.pk).update(
                    effective_parameters=mask_sensitive_data(effective_parameters),
                )
            while current_attempt < max_attempts:
                current_attempt += 1
                reservation = None
                idempotency_key = (
                    getattr(work_item, 'idempotency_key', '')
                    or hashlib.sha256(
                        f'{project.id}:{stage_type}:{provider.id}:{uuid.uuid4()}'.encode('utf-8')
                    ).hexdigest()
                )
                if is_paid:
                    try:
                        reservation = PaidCallGate.reserve(
                            project,
                            provider,
                            capability,
                            usage_estimate,
                            f'{idempotency_key}:paid:{paid_target_count}',
                            work_item=work_item,
                            automatic_fallback=not manual_api,
                            confirmed_max_cost_cny=(request_parameters or {}).get(
                                'confirmed_max_cost_cny'
                            ),
                        )
                    except PaidCallDenied as denied:
                        attempts.append({'provider': str(provider.id), 'code': denied.code})
                        if work_item is not None:
                            publish_work_item_event(
                                work_item,
                                'budget_denied',
                                provider_id=str(provider.pk),
                                error_code=denied.code,
                            )
                        raise HybridExecutionFailed(denied.code, str(denied), attempts)

                started = timezone.now()
                started_clock = time.monotonic()
                log = ModelUsageLog.objects.create(
                    model_provider=provider,
                    work_item=work_item,
                    project_id=project.id,
                    stage_type=stage_type,
                    deployment_mode=provider.deployment_mode,
                    runtime_node=getattr(provider, 'runtime_node', None),
                    attempt_number=current_attempt,
                    idempotency_key=str(idempotency_key),
                    fallback_from=previous_provider,
                    fallback_reason=attempts[-1]['code'] if attempts else '',
                    request_data={},
                    response_data={},
                    request_summary={
                        **cls._request_summary(effective_parameters),
                        **({'shadow_route': shadow_decision} if shadow_decision else {}),
                    },
                    estimated_cost=reservation.reserved_amount if reservation else 0,
                    currency='CNY',
                    price_rate=cls._primary_price_rate(reservation),
                    status='running',
                    started_at=started,
                )
                try:
                    if is_paid:
                        with allow_paid_provider_client(
                            reason='hybrid_inference',
                            reservation_id=str(getattr(reservation, 'pk', '') or ''),
                        ):
                            value = invoke(provider, effective_parameters, repaired_structure)
                    else:
                        value = invoke(provider, effective_parameters, repaired_structure)
                    cls._assert_success(value)
                    cls._complete_success(
                        log=log,
                        value=value,
                        usage_estimate=usage_estimate,
                        reservation=reservation,
                        capability=capability,
                        provider=provider,
                        started_clock=started_clock,
                    )
                    return HybridExecutionResult(value, provider, target, log)
                except Exception as error:
                    classification = InferenceErrorClassifier.classify(error=error)
                    code = classification.code or 'RUNTIME_CRASH'
                    attempts.append({'provider': str(provider.id), 'code': code})
                    if work_item is not None:
                        same_target_retry = (
                            InferenceErrorClassifier.can_retry_same_target(classification)
                            and current_attempt < max_attempts
                        )
                        can_fallback = InferenceErrorClassifier.can_auto_fallback(classification)
                        publish_work_item_event(
                            work_item,
                            (
                                'local_retry' if same_target_retry
                                else 'fallback' if can_fallback
                                else 'attempt_failed'
                            ),
                            provider_id=str(provider.pk),
                            error_code=code,
                        )

                    if (
                        capability == 'llm'
                        and code == 'OUTPUT_SCHEMA_INVALID'
                        and repair_invoke is not None
                        and not repaired_structure
                    ):
                        repaired_structure = True
                        try:
                            if is_paid:
                                with allow_paid_provider_client(
                                    reason='hybrid_structure_repair',
                                    reservation_id=str(getattr(reservation, 'pk', '') or ''),
                                ):
                                    value = repair_invoke(provider, effective_parameters)
                            else:
                                value = repair_invoke(provider, effective_parameters)
                            cls._assert_success(value)
                            # 结构修复属于当前尝试的一部分。只有修复也失败时才把付费
                            # 预留标为“计费状态不明”；修复成功必须结算并修正原账本，
                            # 否则会留下 failed 日志和永不释放的 ambiguous 预算。
                            cls._complete_success(
                                log=log,
                                value=value,
                                usage_estimate=usage_estimate,
                                reservation=reservation,
                                capability=capability,
                                provider=provider,
                                started_clock=started_clock,
                            )
                            return HybridExecutionResult(value, provider, target, log)
                        except Exception as repair_error:
                            attempts.append({
                                'provider': str(provider.id),
                                'code': getattr(repair_error, 'code', 'OUTPUT_SCHEMA_INVALID'),
                            })
                            error = repair_error

                    log.status = 'failed'
                    log.error_code = code
                    # 厂商异常偶尔会回显请求头或带凭据 URL；账本只保存脱敏后的
                    # 可诊断文本，避免 API、CSV 和日志链路二次泄露明文 Key。
                    log.error_message = mask_sensitive_data(str(error))
                    log.latency_ms = int((time.monotonic() - started_clock) * 1000)
                    log.finished_at = timezone.now()
                    log.save()
                    if reservation:
                        PaidCallGate.mark_ambiguous(reservation, f'{code}: {error}')

                    if not InferenceErrorClassifier.can_auto_fallback(classification):
                        raise HybridExecutionFailed(
                            code, mask_sensitive_data(str(error)), attempts
                        )
                    if not (
                        InferenceErrorClassifier.can_retry_same_target(classification)
                        and current_attempt < max_attempts
                    ):
                        break
            previous_provider = provider

        last = attempts[-1] if attempts else {'code': 'NO_ROUTE_AVAILABLE'}
        raise HybridExecutionFailed(last['code'], '所有允许的本地目标均失败，且没有可执行回退', attempts)

    @staticmethod
    def _assert_success(value):
        if value is None:
            raise HybridExecutionFailed('EMPTY_OUTPUT', '模型返回空结果')
        if isinstance(value, dict):
            success = value.get('success')
            error = value.get('error', '')
            data = value.get('data')
            text = value.get('text', '')
        else:
            success = getattr(value, 'success', None)
            error = getattr(value, 'error', '')
            data = getattr(value, 'data', None)
            text = getattr(value, 'text', None)
        if success is False:
            error = error or '模型执行失败'
            code = str(error).split(':', 1)[0] if ':' in str(error) else 'RUNTIME_CRASH'
            failure = RuntimeError(str(error))
            failure.code = code
            raise failure
        if success is True and data in (None, {}, []) and not text:
            failure = RuntimeError('EMPTY_OUTPUT: 模型返回空产物')
            failure.code = 'EMPTY_OUTPUT'
            raise failure

    @staticmethod
    def _extract_usage(value, fallback):
        metadata = (
            value.get('metadata', {}) if isinstance(value, dict)
            else getattr(value, 'metadata', {})
        ) or {}
        usage = metadata.get('usage') or {}
        return {**(fallback or {}), **usage}

    @classmethod
    def _complete_success(
        cls,
        *,
        log,
        value,
        usage_estimate,
        reservation,
        capability,
        provider,
        started_clock,
    ):
        """统一成功结算，供首次输出和结构修复输出共用。"""

        usage = cls._extract_usage(value, usage_estimate)
        settled_cost = 0
        if reservation:
            actual = PricingService.estimate(
                capability,
                provider.model_name,
                usage,
                provider=provider,
            )
            actual_amount = actual.amount_cny if actual.is_complete else None
            PaidCallGate.settle(reservation, actual_amount, {'usage': usage})
            settled_cost = actual_amount if actual_amount is not None else reservation.reserved_amount
        log.status = 'success'
        log.error_code = ''
        log.error_message = ''
        log.input_tokens = int(usage.get('input_tokens', 0) or 0)
        log.output_tokens = int(usage.get('output_tokens', 0) or 0)
        log.tokens_used = log.input_tokens + log.output_tokens
        log.image_count = int(usage.get('image_count', 0) or 0)
        log.video_seconds = usage.get('video_seconds', 0) or 0
        log.settled_cost = settled_cost
        log.latency_ms = int((time.monotonic() - started_clock) * 1000)
        log.finished_at = timezone.now()
        log.response_data = cls._response_summary(value)
        log.save()
        return usage

    @staticmethod
    def _request_summary(parameters):
        prompt = str((parameters or {}).get('prompt', ''))
        return mask_sensitive_data({
            'prompt_sha256': hashlib.sha256(prompt.encode('utf-8')).hexdigest() if prompt else '',
            'prompt_length': len(prompt),
            'input_artifact_count': len((parameters or {}).get('input_artifacts', []) or []),
            'output_spec': (parameters or {}).get('output_spec', {}),
        })

    @staticmethod
    def _response_summary(value):
        if isinstance(value, dict):
            data = value.get('data')
            text = value.get('text', '')
        else:
            data = getattr(value, 'data', None)
            text = getattr(value, 'text', '')
        return {
            'has_text': bool(text),
            'artifact_count': len(data) if isinstance(data, list) else int(bool(data)),
        }

    @staticmethod
    def _primary_price_rate(reservation):
        """兼容账本单 FK：多计费行时保存首个版本，完整列表留在预留快照。"""

        if reservation is None:
            return None
        rate_ids = (reservation.details or {}).get('price_rate_ids') or []
        if not rate_ids:
            return None
        return ProviderPriceRate.objects.filter(pk=rate_ids[0]).first()

    @staticmethod
    def _shadow_decision(
        *, project, project_settings, capability, stage_type, explicit_provider
    ):
        """只计算 V2 路由差异，不改变旧 Provider 的实际执行目标。"""

        try:
            selection = RoutingService.select(
                capability,
                context={
                    'project_id': str(project.pk),
                    'stage_type': stage_type,
                    'profile_code': getattr(
                        project_settings, 'default_profile_code', 'balanced'
                    ),
                },
                project_settings=project_settings,
            )
        except NoRouteAvailable as error:
            return {
                'status': 'no_route',
                'legacy_provider_id': str(explicit_provider.pk),
                'reason': str(error),
            }
        provider_ids = [
            str(target.provider_id) for target in selection.targets if target.provider_id
        ]
        return {
            'status': 'matched' if str(explicit_provider.pk) in provider_ids else 'different',
            'route_id': str(selection.route.pk),
            'legacy_provider_id': str(explicit_provider.pk),
            'candidate_provider_ids': provider_ids,
        }

    @staticmethod
    def _effective_parameters(
        *, capability, provider, target, route, project_settings, request_parameters
    ):
        """执行前固定参数快照，并按质量档硬限制做最终钳制。"""

        profile = (
            getattr(target, 'profile', None)
            or getattr(route, 'profile', None)
            or getattr(project_settings, 'default_profile', None)
        )
        if profile is None:
            profile_code = getattr(project_settings, 'default_profile_code', 'balanced')
            profile = GenerationProfile.objects.filter(
                capability=capability, key=profile_code, is_active=True
            ).first()
        allowed = set(getattr(profile, 'allowed_parameters', None) or [])
        provider_defaults = {
            **{
                key: value for key, value in (getattr(provider, 'extra_config', None) or {}).items()
                if not allowed or key in allowed
            },
            'max_tokens': getattr(provider, 'max_tokens', None),
            'temperature': getattr(provider, 'temperature', None),
            'top_p': getattr(provider, 'top_p', None),
        }
        provider_defaults = {
            key: value for key, value in provider_defaults.items()
            if value is not None and (not allowed or key in allowed)
        }
        requested_generation = {
            key: value for key, value in (request_parameters or {}).items()
            if not allowed or key in allowed
        }
        merged = merge_generation_parameters(
            profile=profile,
            provider_defaults=provider_defaults,
            project_overrides=getattr(project_settings, 'parameter_overrides', None) or {},
            target_overrides=getattr(target, 'parameter_overrides', None) or {},
            request_overrides=requested_generation,
        )
        # prompt、输入产物、幂等键等契约字段不属于可调生成参数，原样保留；
        # 合并后的质量参数覆盖同名请求值，系统硬限制因此具有最终优先级。
        return {**(request_parameters or {}), **merged.parameters}
