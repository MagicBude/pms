"""健康端点、请求编号和安全异常响应集成测试。"""

from unittest.mock import patch

import pytest
from django.core.exceptions import PermissionDenied, SuspiciousOperation
from django.http import Http404, HttpRequest, HttpResponse
from django.test import Client, override_settings
from django.urls import include, path

from pms.platform.health import ReadinessReport


def raise_unexpected_error(request: HttpRequest) -> HttpResponse:
    del request
    raise RuntimeError("password=must-not-leak C:/private/source.py")


def raise_not_found(request: HttpRequest) -> HttpResponse:
    del request
    raise Http404("private object detail")


def raise_permission_denied(request: HttpRequest) -> HttpResponse:
    del request
    raise PermissionDenied("private permission detail")


def raise_suspicious_operation(request: HttpRequest) -> HttpResponse:
    del request
    raise SuspiciousOperation("private request detail")


urlpatterns = [
    path("", include("pms.urls")),
    path("test/boom", raise_unexpected_error),
    path("test/missing", raise_not_found),
    path("test/forbidden", raise_permission_denied),
    path("test/bad-request", raise_suspicious_operation),
]


@override_settings(ROOT_URLCONF=__name__)
def test_liveness_does_not_require_database_access() -> None:
    response = Client().get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "alive"}
    assert response.headers["X-Request-ID"]
    assert "no-cache" in response.headers["Cache-Control"]


@pytest.mark.django_db
@override_settings(ROOT_URLCONF=__name__)
def test_readiness_checks_database_and_applied_migrations() -> None:
    response = Client().get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {"database": "ok", "migrations": "ok"},
    }
    assert "no-cache" in response.headers["Cache-Control"]


@override_settings(ROOT_URLCONF=__name__)
def test_not_ready_response_is_stable_and_hides_dependency_details() -> None:
    report = ReadinessReport(
        ready=False,
        checks={"database": "unavailable", "migrations": "unknown"},
    )
    with patch("pms.platform.views.check_readiness", return_value=report):
        response = Client().get("/health/ready")

    payload = response.json()
    assert response.status_code == 503
    assert payload["error"]["code"] == "SERVICE_NOT_READY"
    assert payload["error"]["request_id"] == response.headers["X-Request-ID"]
    assert "database" not in payload["error"]["message"].lower()


@override_settings(ROOT_URLCONF=__name__)
def test_safe_inbound_request_id_is_preserved_and_invalid_value_is_replaced() -> None:
    client = Client()

    preserved = client.get("/health/live", headers={"X-Request-ID": "proxy-request_001"})
    replaced = client.get("/health/live", headers={"X-Request-ID": "unsafe\r\nvalue"})

    assert preserved.headers["X-Request-ID"] == "proxy-request_001"
    assert replaced.headers["X-Request-ID"] != "unsafe\r\nvalue"


@pytest.mark.parametrize(
    ("path_value", "status", "code"),
    [
        ("/test/missing", 404, "RESOURCE_NOT_FOUND"),
        ("/test/forbidden", 403, "PERMISSION_DENIED"),
        ("/test/bad-request", 400, "INVALID_REQUEST"),
    ],
)
@override_settings(ROOT_URLCONF=__name__)
def test_expected_framework_errors_have_stable_safe_responses(
    path_value: str, status: int, code: str
) -> None:
    response = Client().get(path_value)
    rendered = response.content.decode()

    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]
    assert "private" not in rendered


@override_settings(ROOT_URLCONF=__name__)
def test_unexpected_error_is_logged_with_request_id_and_safe_response() -> None:
    with patch("pms.platform.middleware.logger.error") as log_error:
        response = Client().get("/test/boom")

    rendered = response.content.decode()
    request_id = response.headers["X-Request-ID"]
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert response.json()["error"]["request_id"] == request_id
    assert "must-not-leak" not in rendered
    assert "C:/private" not in rendered
    assert log_error.call_args.kwargs["extra"]["request_id"] == request_id
