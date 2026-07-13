"""Bearer 鉴权与回环地址策略。"""

from __future__ import annotations

import hmac
import ipaddress

from fastapi import Request

from .config import RuntimeSettings
from .errors import AgentError


def is_loopback_host(host: str | None) -> bool:
    """识别 IPv4、IPv6 及 IPv4-mapped IPv6 回环地址。"""

    if not host:
        return False
    normalized = host.strip().strip("[]")
    if normalized.lower() == "localhost":
        return True
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    if address.is_loopback:
        return True
    mapped = getattr(address, "ipv4_mapped", None)
    return bool(mapped and mapped.is_loopback)


class BearerAuthenticator:
    """根据客户端来源和配置执行不透明 Bearer token 校验。"""

    def __init__(self, settings: RuntimeSettings) -> None:
        self.settings = settings

    async def __call__(self, request: Request) -> None:
        host = request.client.host if request.client else None
        authorization = request.headers.get("authorization", "")
        scheme, _, supplied_token = authorization.partition(" ")
        valid_token = (
            scheme.lower() == "bearer"
            and bool(supplied_token)
            and hmac.compare_digest(supplied_token, self.settings.bearer_token)
        )
        if valid_token:
            return

        if is_loopback_host(host) and self.settings.allow_unauthenticated_loopback:
            return
        if not is_loopback_host(host) and not self.settings.require_auth_non_loopback:
            return

        raise AgentError(
            status_code=401,
            code="AUTHENTICATION_REQUIRED",
            message="需要有效的 Bearer token",
        )
