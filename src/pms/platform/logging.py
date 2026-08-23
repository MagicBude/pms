"""使用字段允许列表的安全 JSON 运行日志。"""

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

SAFE_LOG_FIELDS = (
    "request_id",
    "correlation_id",
    "tenant_id",
    "actor_id",
    "operation",
    "entity_type",
    "entity_id",
    "result",
    "duration_ms",
    "error_code",
    "status_code",
)
STABLE_EVENT_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,99}$")


class SafeJsonFormatter(logging.Formatter):
    """只序列化经过允许的诊断字段，忽略任意 extra 和异常正文。

    日志调用方应把 ``event`` 设为稳定事件名。异常仅记录类型，避免异常消息、
    堆栈绝对路径、SQL 或秘密输入意外进入持久化运行日志。
    """

    def format(self, record: logging.LogRecord) -> str:
        event = getattr(record, "event", None)
        safe_event = (
            event
            if isinstance(event, str) and STABLE_EVENT_PATTERN.fullmatch(event)
            else "unstructured_log"
        )
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "event": safe_event,
            "logger": record.name,
        }
        for field_name in SAFE_LOG_FIELDS:
            value = getattr(record, field_name, None)
            if isinstance(value, str | int | float | bool):
                payload[field_name] = value
            elif isinstance(value, UUID):
                payload[field_name] = str(value)
        if record.exc_info is not None and record.exc_info[0] is not None:
            payload["error_type"] = record.exc_info[0].__name__
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def build_logging_config(*, app_level: str) -> dict[str, Any]:
    """生成部署档案共享的日志配置，具体档案只选择 PMS 日志级别。"""
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"safe_json": {"()": "pms.platform.logging.SafeJsonFormatter"}},
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "safe_json",
            }
        },
        "root": {"handlers": ["console"], "level": "WARNING"},
        "loggers": {
            "pms": {
                "handlers": ["console"],
                "level": app_level,
                "propagate": False,
            }
        },
    }
