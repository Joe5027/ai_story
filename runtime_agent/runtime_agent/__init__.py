"""AI Story 独立运行时代理。"""

from typing import Any

from .config import RuntimeSettings


def create_app(*args: Any, **kwargs: Any):
    """延迟导入 FastAPI，使 journal 等核心模块可独立离线检查。"""

    from .main import create_app as factory

    return factory(*args, **kwargs)


__all__ = ["RuntimeSettings", "create_app"]
