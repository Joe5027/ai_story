"""生成参数分层合并、允许列表过滤与硬限制钳制。"""

from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, Optional, Tuple


PROTECTED_PARAMETER_KEYS = {
    'api_key', 'apikey', 'authorization', 'password', 'secret', 'token',
    'credential_ref', 'endpoint', 'base_url', 'provider_id', 'runtime_node_id',
    'unit_price', 'estimated_cost', 'actual_cost', 'currency',
}


@dataclass(frozen=True)
class ParameterMergeResult:
    """参数合并结果及审计信息。"""

    parameters: Dict[str, Any]
    clamped: Tuple[str, ...] = ()
    dropped: Tuple[str, ...] = ()


def _deep_merge(target: Dict[str, Any], source: Dict[str, Any]) -> Dict[str, Any]:
    for key, value in (source or {}).items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            target[key] = _deep_merge(deepcopy(target[key]), value)
        else:
            target[key] = deepcopy(value)
    return target


def _remove_protected(value: Any, dropped, prefix: str = '') -> Any:
    """递归移除路由、凭据和计价控制字段，并记录完整参数路径。"""

    if isinstance(value, dict):
        safe = {}
        for key, item in value.items():
            key_text = str(key)
            path = f'{prefix}.{key_text}' if prefix else key_text
            if key_text.lower() in PROTECTED_PARAMETER_KEYS:
                dropped.append(path)
                continue
            safe[key] = _remove_protected(item, dropped, path)
        return safe
    if isinstance(value, list):
        return [
            _remove_protected(item, dropped, f'{prefix}[{index}]')
            for index, item in enumerate(value)
        ]
    if isinstance(value, tuple):
        return tuple(
            _remove_protected(item, dropped, f'{prefix}[{index}]')
            for index, item in enumerate(value)
        )
    return deepcopy(value)


def _coerce_number(value: Any) -> Optional[Decimal]:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _restore_numeric_type(original: Any, value: Decimal):
    if isinstance(original, bool):
        return original
    if isinstance(original, int):
        return int(value)
    if isinstance(original, float):
        return float(value)
    if isinstance(original, str):
        return str(value)
    # Django JSONField 的默认编码器不能序列化 Decimal；未知数值类型退回 float。
    return float(value)


def merge_generation_parameters(
    profile=None,
    provider_defaults: Optional[Dict[str, Any]] = None,
    project_overrides: Optional[Dict[str, Any]] = None,
    route_overrides: Optional[Dict[str, Any]] = None,
    target_overrides: Optional[Dict[str, Any]] = None,
    request_overrides: Optional[Dict[str, Any]] = None,
    hard_limits: Optional[Dict[str, Any]] = None,
    allowed_parameters: Optional[Iterable[str]] = None,
) -> ParameterMergeResult:
    """按基线→项目→路由→目标→请求顺序合并并钳制参数。

    只有请求层会被允许列表过滤；任何层出现凭据或计价控制字段都会被丢弃，
    避免用户输入越权改写路由、密钥或成本。
    """

    result: Dict[str, Any] = {}
    dropped = []
    clamped = []
    profile_defaults = getattr(profile, 'default_parameters', None) or {}
    profile_limits = getattr(profile, 'hard_limits', None) or {}
    profile_allowed = getattr(profile, 'allowed_parameters', None)
    allowed_source = allowed_parameters if allowed_parameters is not None else profile_allowed
    enforce_allowlist = allowed_source is not None
    allowed = set(allowed_source or [])

    layers = [
        provider_defaults or {},
        profile_defaults,
        project_overrides or {},
        route_overrides or {},
        target_overrides or {},
    ]
    for layer in layers:
        safe_layer = _remove_protected(layer, dropped)
        _deep_merge(result, safe_layer)

    safe_request = {}
    for key, value in (request_overrides or {}).items():
        normalized_key = str(key).lower()
        if normalized_key in PROTECTED_PARAMETER_KEYS or (
            enforce_allowlist and key not in allowed
        ):
            dropped.append(str(key))
            continue
        safe_request[key] = _remove_protected(value, dropped, str(key))
    _deep_merge(result, safe_request)

    effective_limits = _deep_merge(deepcopy(profile_limits), hard_limits or {})
    for key, spec in effective_limits.items():
        if key not in result or not isinstance(spec, dict):
            continue
        value = result[key]
        choices = spec.get('choices')
        if choices and value not in choices:
            result[key] = deepcopy(spec.get('default', choices[0]))
            clamped.append(key)
            continue
        numeric = _coerce_number(value)
        if numeric is not None:
            minimum = _coerce_number(spec.get('min'))
            maximum = _coerce_number(spec.get('max'))
            adjusted = numeric
            if minimum is not None and adjusted < minimum:
                adjusted = minimum
            if maximum is not None and adjusted > maximum:
                adjusted = maximum
            if adjusted != numeric:
                result[key] = _restore_numeric_type(value, adjusted)
                clamped.append(key)
        if isinstance(result.get(key), str) and spec.get('max_length'):
            try:
                max_length = max(0, int(spec['max_length']))
            except (TypeError, ValueError):
                continue
            if len(result[key]) > max_length:
                result[key] = result[key][:max_length]
                clamped.append(key)

    return ParameterMergeResult(result, tuple(dict.fromkeys(clamped)), tuple(dict.fromkeys(dropped)))
