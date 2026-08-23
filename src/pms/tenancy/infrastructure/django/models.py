"""tenant 与 membership 的 Django ORM 映射。"""

import uuid

from django.conf import settings
from django.db import models


class Tenant(models.Model):
    """可以独立拥有业务数据和成员权限边界的企业租户。"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    code = models.SlugField(max_length=64, unique=True)
    name = models.CharField(max_length=200)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "tenancy_tenant"
        verbose_name = "租户"
        verbose_name_plural = "租户"


class Membership(models.Model):
    """用户加入租户的关系；租户内角色将在 F-006 独立建模。"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="memberships")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="memberships"
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "tenancy_membership"
        verbose_name = "租户成员"
        verbose_name_plural = "租户成员"
        constraints = [
            models.UniqueConstraint(
                fields=("tenant", "user"),
                name="uq_tenancy_membership_tenant_user",
            )
        ]
        indexes = [
            models.Index(
                fields=("user", "is_active"),
                name="ix_membership_user_active",
            )
        ]
