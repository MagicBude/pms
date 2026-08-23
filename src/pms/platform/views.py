"""平台存活、就绪和工程状态 HTTP 入口。"""

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from pms.platform.errors import ErrorCode, error_payload
from pms.platform.health import check_readiness
from pms.platform.middleware import get_request_id


@require_GET
def index(request: HttpRequest) -> HttpResponse:
    """返回不依赖数据库的工程阶段提示。"""
    del request
    return HttpResponse(
        "PMS 工程基础已启动；业务界面将在 SLICE-001 中提供。",
        content_type="text/plain; charset=utf-8",
    )


@require_GET
@never_cache
def live(request: HttpRequest) -> JsonResponse:
    """证明 Web 进程能够响应；不得访问数据库或其他外部依赖。"""
    del request
    return JsonResponse({"status": "alive"})


@require_GET
@never_cache
def ready(request: HttpRequest) -> JsonResponse:
    """检查当前实例能否安全承接请求。"""
    report = check_readiness()
    if report.ready:
        return JsonResponse({"status": "ready", "checks": report.checks})
    response_data = error_payload(
        code=ErrorCode.SERVICE_NOT_READY,
        message="服务尚未就绪，请稍后重试。",
        request_id=get_request_id(request),
    )
    response_data.update({"status": "not_ready", "checks": report.checks})
    return JsonResponse(response_data, status=503)
