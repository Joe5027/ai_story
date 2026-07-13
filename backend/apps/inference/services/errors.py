"""将厂商差异化错误归一为可用于回退和重试的稳定分类。"""

from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class ErrorClassification:
    """归一化错误分类。"""

    category: str
    retryable: bool
    code: str = ''
    message: str = ''


class InferenceErrorClassifier:
    """基于 HTTP 状态、异常类型和消息的保守错误分类器。"""

    RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
    AUTO_FALLBACK_CODES = {
        'NODE_UNAVAILABLE', 'RUNTIME_NOT_READY', 'EXECUTION_TIMEOUT', 'GPU_OOM',
        'RUNTIME_CRASH', 'EMPTY_OUTPUT', 'OUTPUT_SCHEMA_INVALID',
        'ARTIFACT_DOWNLOAD_FAILED', 'CHECKSUM_MISMATCH',
    }
    NEVER_AUTO_PAY_CODES = {
        'INVALID_REQUEST', 'INPUT_MISSING', 'UNSUPPORTED_PROFILE', 'CONTENT_POLICY',
        'USER_CANCELLED', 'CLOUD_NOT_AUTHORIZED', 'BUDGET_DENIED', 'PRICE_MISSING',
        'RESOURCE_BUSY',
    }
    SAME_TARGET_RETRY_CODES = {
        'EXECUTION_TIMEOUT', 'GPU_OOM', 'RUNTIME_CRASH', 'EMPTY_OUTPUT',
        'ARTIFACT_DOWNLOAD_FAILED', 'CHECKSUM_MISMATCH',
    }

    @classmethod
    def classify(
        cls,
        error: Any = None,
        status_code: Optional[int] = None,
        code: str = '',
        message: str = '',
    ) -> ErrorClassification:
        resolved_status = status_code or getattr(error, 'status_code', None)
        resolved_code = str(code or getattr(error, 'code', '') or '')
        resolved_message = str(message or error or '')
        haystack = f'{resolved_code} {resolved_message}'.lower()

        if resolved_code in cls.AUTO_FALLBACK_CODES:
            return ErrorClassification('technical_failure', True, resolved_code, resolved_message)
        if resolved_code in cls.NEVER_AUTO_PAY_CODES:
            return ErrorClassification('blocked', False, resolved_code, resolved_message)
        if 'out of memory' in haystack or 'cuda oom' in haystack or '显存不足' in haystack:
            return ErrorClassification('technical_failure', True, 'GPU_OOM', resolved_message)
        if 'empty output' in haystack or '空产物' in haystack:
            return ErrorClassification('technical_failure', True, 'EMPTY_OUTPUT', resolved_message)

        if resolved_status in {401, 403} or any(
            token in haystack for token in ('unauthorized', 'forbidden', 'invalid api key', '认证失败')
        ):
            return ErrorClassification('authentication', False, resolved_code, resolved_message)
        # 余额/额度耗尽常被厂商也编码为 429，必须先于普通限流识别，避免昂贵重试。
        if resolved_status == 402 or any(
            token in haystack for token in ('quota', 'insufficient balance', '余额不足', '额度不足')
        ):
            return ErrorClassification('quota_exhausted', False, resolved_code, resolved_message)
        if resolved_status == 429 or any(
            token in haystack for token in ('rate limit', 'too many requests', '限流')
        ):
            return ErrorClassification('rate_limit', True, resolved_code, resolved_message)
        if any(token in haystack for token in ('content policy', 'safety', 'moderation', '敏感内容')):
            return ErrorClassification('content_policy', False, resolved_code, resolved_message)
        if resolved_status in {400, 404, 405, 422} or any(
            token in haystack for token in ('invalid request', 'validation', '参数错误')
        ):
            return ErrorClassification('invalid_request', False, resolved_code, resolved_message)
        if resolved_status in {408, 504} or isinstance(error, TimeoutError) or 'timeout' in haystack or '超时' in haystack:
            return ErrorClassification('timeout', True, resolved_code, resolved_message)
        if resolved_status in {409, 425, 500, 502, 503}:
            return ErrorClassification('provider_transient', True, resolved_code, resolved_message)
        if any(token in haystack for token in ('connection', 'network', 'dns', '网络', '连接')):
            return ErrorClassification('network', True, resolved_code, resolved_message)
        if resolved_status is not None:
            return ErrorClassification(
                'provider_error', resolved_status in cls.RETRYABLE_STATUS, resolved_code, resolved_message
            )
        return ErrorClassification('internal', False, resolved_code, resolved_message)

    @classmethod
    def can_auto_fallback(cls, classification: ErrorClassification) -> bool:
        """审美和角色一致性等主观问题永远不会进入自动付费回退。"""
        return classification.code in cls.AUTO_FALLBACK_CODES

    @classmethod
    def can_retry_same_target(cls, classification: ErrorClassification) -> bool:
        return classification.code in cls.SAME_TARGET_RETRY_CODES


def classify_inference_error(*args, **kwargs):
    """函数式错误分类入口。"""

    return InferenceErrorClassifier.classify(*args, **kwargs)
