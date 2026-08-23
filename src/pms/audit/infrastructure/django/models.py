"""不可通过正常 ORM API 修改的追加式审计映射。"""

import uuid
from typing import Any

from django.conf import settings
from django.db import models

from pms.audit.domain.events import AuditResult
from pms.tenancy.infrastructure.django.models import Membership, Tenant


class AuditLogImmutableError(RuntimeError):
    """表示代码尝试修改或删除既有审计证据。"""


class AuditLogQuerySet(models.QuerySet["AuditLog"]):
    """阻止 QuerySet 绕过模型方法批量修改审计。"""

    def update(self, **kwargs: object) -> int:
        raise AuditLogImmutableError("审计记录只能追加，不能更新。")

    def delete(self) -> tuple[int, dict[str, int]]:
        raise AuditLogImmutableError("审计记录只能追加，不能删除。")


class AuditLog(models.Model):
    """重要动作的追加式安全证据，不保存秘密或附件正文。"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="audit_logs")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="audit_logs",
    )
    membership = models.ForeignKey(
        Membership,
        on_delete=models.SET_NULL,
        null=True,
        related_name="audit_logs",
    )
    action = models.CharField(max_length=100)
    object_type = models.CharField(max_length=100)
    object_id = models.CharField(max_length=100)
    result = models.CharField(
        max_length=16,
        choices=[(result.value, result.value) for result in AuditResult],
    )
    summary = models.JSONField(default=dict)
    occurred_at = models.DateTimeField(auto_now_add=True)

    objects = AuditLogQuerySet.as_manager()

    class Meta:
        db_table = "audit_log"
        ordering = ("-occurred_at", "-id")

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise AuditLogImmutableError("审计记录只能追加，不能更新。")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise AuditLogImmutableError("审计记录只能追加，不能删除。")
