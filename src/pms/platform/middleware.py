"""请求编号、访问日志和安全异常映射中间件。"""

import logging
import re
import time
import uuid
from collections.abc import Callable

from django.core.exceptions import PermissionDenied, SuspiciousOperation
from django.http import Http404, HttpRequest, HttpResponse

from pms.platform.errors import ErrorCode, error_response

REQUEST_ID_HEADER = "X-Request-ID"
REQUEST_ID_META_KEY = "pms.request_id"
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
logger = logging.getLogger("pms.http")


def choose_request_id(candidate: str | None) -> str:
    """保留安全代理请求编号，否则生成按时间排序的 UUIDv7。"""
    if candidate is not None and REQUEST_ID_PATTERN.fullmatch(candidate):
        return candidate
    return str(uuid.uuid7())


def get_request_id(request: HttpRequest) -> str:
    """读取中间件建立的请求编号；测试或特殊入口缺失时安全补建。"""
    value = request.META.get(REQUEST_ID_META_KEY)
    return value if isinstance(value, str) else choose_request_id(None)


class RequestContextMiddleware:
    """为所有 HTTP 响应提供请求编号、结构化结果和安全错误边界。"""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        request_id = choose_request_id(request.headers.get(REQUEST_ID_HEADER))
        request.META[REQUEST_ID_META_KEY] = request_id
        started_at = time.perf_counter()
        response = self.get_response(request)
        response[REQUEST_ID_HEADER] = request_id
        logger.info(
            "request_completed",
            extra={
                "event": "request_completed",
                "request_id": request_id,
                "operation": request.method,
                "status_code": response.status_code,
                "result": "success" if response.status_code < 400 else "failure",
                "duration_ms": round((time.perf_counter() - started_at) * 1000, 3),
            },
        )
        return response

    def process_exception(self, request: HttpRequest, exception: Exception) -> HttpResponse:
        """把框架异常和未预期异常映射为不泄露实现细节的 JSON。"""
        request_id = get_request_id(request)
        if isinstance(exception, Http404):
            return error_response(
                code=ErrorCode.RESOURCE_NOT_FOUND,
                message="请求的资源不存在。",
                request_id=request_id,
                status=404,
            )
        if isinstance(exception, PermissionDenied):
            return error_response(
                code=ErrorCode.PERMISSION_DENIED,
                message="当前用户无权执行该操作。",
                request_id=request_id,
                status=403,
            )
        if isinstance(exception, SuspiciousOperation):
            return error_response(
                code=ErrorCode.INVALID_REQUEST,
                message="请求格式或内容无效，请检查后重试。",
                request_id=request_id,
                status=400,
            )

        logger.error(
            "unhandled_request_error",
            extra={
                "event": "unhandled_request_error",
                "request_id": request_id,
                "operation": request.method,
                "error_code": ErrorCode.INTERNAL_ERROR.value,
                "result": "failure",
            },
            exc_info=(type(exception), exception, exception.__traceback__),
        )
        return error_response(
            code=ErrorCode.INTERNAL_ERROR,
            message="系统暂时无法完成请求，请稍后重试并向支持人员提供请求编号。",
            request_id=request_id,
            status=500,
        )
