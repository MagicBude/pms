"""稳定错误代码与不泄露内部细节的 HTTP 响应。"""

from enum import StrEnum

from django.http import JsonResponse


class ErrorCode(StrEnum):
    """HTTP 边界首批稳定机器错误代码。"""

    INVALID_REQUEST = "INVALID_REQUEST"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    SERVICE_NOT_READY = "SERVICE_NOT_READY"
    INTERNAL_ERROR = "INTERNAL_ERROR"


def error_payload(*, code: ErrorCode, message: str, request_id: str) -> dict[str, object]:
    """创建与传输框架无关的安全错误正文。"""
    return {
        "error": {
            "code": code.value,
            "message": message,
            "request_id": request_id,
        }
    }


def error_response(
    *,
    code: ErrorCode,
    message: str,
    request_id: str,
    status: int,
) -> JsonResponse:
    """创建只包含稳定代码、可行动提示和请求编号的安全错误响应。"""
    return JsonResponse(
        error_payload(code=code, message=message, request_id=request_id), status=status
    )
