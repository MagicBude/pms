"""带敏感字段拒绝策略的 Django 审计记录器。"""

from collections.abc import Mapping
from uuid import UUID

from pms.audit.application.recorder import AuditRecorder
from pms.audit.domain.events import AuditEvent
from pms.audit.infrastructure.django.models import AuditLog

SENSITIVE_KEY_FRAGMENTS = frozenset(
    {
        "attachment_body",
        "authorization",
        "cookie",
        "file_content",
        "password",
        "secret",
        "session",
        "token",
    }
)


class SensitiveAuditDataError(ValueError):
    """表示审计摘要包含禁止持久化的敏感字段。"""


def reject_sensitive_fields(value: object, *, path: str = "summary") -> None:
    """递归拒绝敏感键；不检查或回显对应值。"""
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            normalized = str(key).lower().replace("-", "_")
            if any(fragment in normalized for fragment in SENSITIVE_KEY_FRAGMENTS):
                raise SensitiveAuditDataError(f"审计摘要包含禁止字段：{path}.{key}")
            reject_sensitive_fields(nested_value, path=f"{path}.{key}")
    elif isinstance(value, list | tuple):
        for index, nested_value in enumerate(value):
            reject_sensitive_fields(nested_value, path=f"{path}[{index}]")


class DjangoAuditRecorder(AuditRecorder):
    """验证摘要后追加审计，不提供修改或删除接口。"""

    def record(self, event: AuditEvent) -> UUID:
        reject_sensitive_fields(event.summary)
        audit_log = AuditLog.objects.create(
            tenant_id=event.tenant_id,
            actor_id=event.actor_id,
            membership_id=event.membership_id,
            action=event.action,
            object_type=event.object_type,
            object_id=event.object_id,
            result=event.result,
            summary=event.summary,
        )
        return audit_log.id
