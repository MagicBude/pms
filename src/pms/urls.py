"""平台级 URL。

F-002 仅用根路径证明浏览器、ASGI 和 Django 的链路可用；业务 URL
将在对应纵向切片中由各模块自行拥有。
"""

from django.http import HttpRequest, HttpResponse
from django.urls import path


def index(request: HttpRequest) -> HttpResponse:
    """返回不依赖数据库的工程就绪提示。"""
    del request
    return HttpResponse(
        "PMS 工程基础已启动；业务界面将在 SLICE-001 中提供。",
        content_type="text/plain; charset=utf-8",
    )


urlpatterns = [path("", index, name="platform-index")]
