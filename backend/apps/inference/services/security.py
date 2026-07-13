"""日志与审计数据的敏感字段遮罩。"""

import re
from typing import Any


SENSITIVE_EXACT_KEYS = {
    'api_key', 'apikey', 'token', 'password', 'passwd', 'secret',
    'authorization', 'cookie', 'credential', 'private_key', 'access_token',
    'auth_token', 'bearer_token',
}
SENSITIVE_KEY_SUFFIXES = (
    '_api_key', '_apikey', '_access_token', '_auth_token', '_bearer_token',
    '_password', '_passwd', '_secret', '_private_key', '_credential',
)
REDACTED = '[REDACTED]'


def _mask_text(value: str) -> str:
    value = re.sub(r'(?i)bearer\s+[a-z0-9._~+\-/=]+', f'Bearer {REDACTED}', value)
    value = re.sub(r'(?i)([?&](?:token|api_key|key|secret)=)[^&#\s]+', rf'\1{REDACTED}', value)
    value = re.sub(
        r'(?i)\b(api[_-]?key|access[_-]?token|token|password|secret)'
        r'(\s*[=:]\s*)["\']?[^"\'\s,;&]+["\']?',
        rf'\1\2{REDACTED}',
        value,
    )
    value = re.sub(r'\bsk-[A-Za-z0-9_-]{8,}\b', REDACTED, value)
    return value


def mask_sensitive_data(value: Any, key: str = '') -> Any:
    """递归遮罩字典、列表及常见字符串凭据，不修改原对象。"""

    normalized_key = str(key).strip().lower().replace('-', '_')
    # Token 计量字段（max_tokens/input_tokens/output_tokens）不是凭据，不能因
    # 包含 ``token`` 子串而被遮罩，否则工作项执行和价格估算都会被破坏。
    if (
        normalized_key in SENSITIVE_EXACT_KEYS
        or normalized_key.endswith(SENSITIVE_KEY_SUFFIXES)
    ):
        return REDACTED
    if isinstance(value, dict):
        return {item_key: mask_sensitive_data(item, str(item_key)) for item_key, item in value.items()}
    if isinstance(value, list):
        return [mask_sensitive_data(item) for item in value]
    if isinstance(value, tuple):
        return tuple(mask_sensitive_data(item) for item in value)
    if isinstance(value, str):
        return _mask_text(value)
    return value
