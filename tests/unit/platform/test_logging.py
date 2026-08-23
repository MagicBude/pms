"""结构化日志只允许安全诊断字段。"""

import json
import logging
import sys
import uuid

import pytest

from pms.platform.logging import SafeJsonFormatter, build_logging_config


def build_record() -> logging.LogRecord:
    return logging.LogRecord(
        name="pms.test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="password=message-secret",
        args=(),
        exc_info=None,
    )


@pytest.mark.unit
def test_formatter_keeps_only_allowlisted_fields() -> None:
    record = build_record()
    record.__dict__.update(
        {
            "event": "stable_event",
            "request_id": "request-001",
            "password": "field-secret",
            "authorization": "Bearer token-secret",
        }
    )

    rendered = SafeJsonFormatter().format(record)
    payload = json.loads(rendered)

    assert payload["event"] == "stable_event"
    assert payload["request_id"] == "request-001"
    assert "message-secret" not in rendered
    assert "field-secret" not in rendered
    assert "token-secret" not in rendered
    assert "password" not in payload
    assert "authorization" not in payload


@pytest.mark.unit
def test_formatter_rejects_dynamic_event_and_arbitrary_field_objects() -> None:
    class SecretValue:
        def __str__(self) -> str:
            return "object-secret"

    record = build_record()
    record.__dict__["event"] = "password=event-secret"
    record.__dict__["entity_id"] = SecretValue()

    rendered = SafeJsonFormatter().format(record)

    assert json.loads(rendered)["event"] == "unstructured_log"
    assert "event-secret" not in rendered
    assert "object-secret" not in rendered


@pytest.mark.unit
def test_formatter_serializes_uuid_identifiers_without_arbitrary_conversion() -> None:
    tenant_id = uuid.uuid7()
    record = build_record()
    record.__dict__.update({"event": "tenant_event", "tenant_id": tenant_id})

    payload = json.loads(SafeJsonFormatter().format(record))

    assert payload["tenant_id"] == str(tenant_id)


@pytest.mark.unit
def test_formatter_records_exception_type_without_message_or_traceback_path() -> None:
    record = build_record()
    record.__dict__["event"] = "failed_event"
    try:
        raise RuntimeError("token=exception-secret C:/private/system.py")
    except RuntimeError:
        record.exc_info = sys.exc_info()

    rendered = SafeJsonFormatter().format(record)
    payload = json.loads(rendered)

    assert payload["error_type"] == "RuntimeError"
    assert "exception-secret" not in rendered
    assert "C:/private" not in rendered


@pytest.mark.unit
def test_logging_config_separates_application_and_dependency_levels() -> None:
    config = build_logging_config(app_level="DEBUG")

    assert config["loggers"]["pms"]["level"] == "DEBUG"
    assert config["root"]["level"] == "WARNING"
