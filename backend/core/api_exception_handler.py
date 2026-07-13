"""DRF 异常响应的最后一道敏感字段过滤。"""

from rest_framework.views import exception_handler

from apps.inference.services.security import mask_sensitive_data


def safe_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is not None:
        response.data = mask_sensitive_data(response.data)
    return response
