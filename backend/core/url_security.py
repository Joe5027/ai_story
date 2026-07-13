"""服务端点 URL 的统一安全校验。

该模块只做字面 URL 校验，不进行 DNS 解析。这样既能阻止凭据嵌入 URL，
也不会在校验阶段引入 DNS 重绑定或额外网络访问。
"""

import ipaddress
from urllib.parse import urlsplit


def _is_literal_loopback(hostname):
    """判断显式主机名/IP 是否为回环地址，不把私网地址误当作回环。"""

    normalized = (hostname or '').rstrip('.').lower()
    if normalized == 'localhost':
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def validate_service_url(
    value,
    *,
    require_https_for_non_loopback=False,
    field_label='服务 URL',
):
    """校验下游服务 URL，并返回去除首尾空白后的值。

    非回环目标在携带 Agent Token 或 API Key 时必须使用 TLS。这里仅认可
    ``localhost`` 和显式 loopback IP，私网 IP/主机名也属于非回环目标。
    """

    normalized = str(value or '').strip()
    if not normalized:
        return ''

    try:
        parsed = urlsplit(normalized)
        # 主动读取 port，确保 ``https://host:bad`` 这类值也在此处失败。
        parsed.port
    except ValueError as error:
        raise ValueError(f'{field_label} 格式无效。') from error

    if parsed.scheme.lower() not in {'http', 'https'}:
        raise ValueError(f'{field_label} 只允许 http 或 https。')
    if not parsed.hostname:
        raise ValueError(f'{field_label} 必须包含主机名。')
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f'{field_label} 禁止在 URL 中嵌入用户名或密码。')
    if (
        require_https_for_non_loopback
        and not _is_literal_loopback(parsed.hostname)
        and parsed.scheme.lower() != 'https'
    ):
        raise ValueError(f'{field_label} 的非回环地址必须使用 https。')

    return normalized
