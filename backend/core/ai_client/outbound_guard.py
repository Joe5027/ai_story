"""付费 Provider 客户端的进程内 fail-closed 授权闸门。"""

from contextlib import contextmanager
from contextvars import ContextVar


class PaidProviderGuardDenied(RuntimeError):
    """代码路径未经过隐私、价目表和预算检查。"""

    code = 'PAID_GATE_REQUIRED'


_paid_provider_context = ContextVar('paid_provider_context', default=None)


@contextmanager
def allow_paid_provider_client(*, reason: str, reservation_id: str = ''):
    """仅在已完成安全校验的最小调用范围内允许创建 API 客户端。

    这是纵深防御，不替代业务层三重门。旧 Processor、调试工具或未来新增代码
    如果直接 ``create_ai_client(api_provider)``，会在任何外部网络请求前失败；
    HybridInferenceService 只有取得预算预留后才进入此上下文。
    """

    token = _paid_provider_context.set({
        'reason': str(reason),
        'reservation_id': str(reservation_id or ''),
    })
    try:
        yield
    finally:
        _paid_provider_context.reset(token)


def require_paid_provider_authorization(provider) -> None:
    """拒绝未经授权上下文创建付费 API Provider 客户端。"""

    from django.conf import settings

    if getattr(provider, 'deployment_mode', 'api') != 'api':
        return
    if not getattr(settings, 'AI_ENFORCE_PAID_PROVIDER_GUARD', True):
        return
    if _paid_provider_context.get() is None:
        raise PaidProviderGuardDenied(
            '付费 Provider 调用未经过云授权、有效价目表和预算预留，已在发出请求前阻断。'
        )
