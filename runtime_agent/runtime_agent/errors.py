"""稳定错误协议和内部执行异常。"""

from __future__ import annotations

from typing import Any


class AgentError(Exception):
    """可安全返回给 API 调用方的结构化错误。"""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: Any | None = None,
        *,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        self.retryable = status_code >= 500 if retryable is None else retryable


class CancelledExecution(Exception):
    """适配器协作式响应取消请求。"""


class AdapterExecutionError(Exception):
    """Mock 适配器产生的预期执行失败。"""


class AdapterRuntimeError(Exception):
    """适配器返回给任务状态机的稳定运行错误。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}


class RuntimeNotReady(AdapterRuntimeError):
    """真实模型、工作流或二进制未准备好，禁止回退到 Mock。"""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            "RUNTIME_NOT_READY",
            message,
            retryable=True,
            details=details,
        )
