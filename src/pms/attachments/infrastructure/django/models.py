"""附件元数据 ORM；二进制内容始终位于可替换存储。"""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q

from pms.attachments.domain.attachments import AttachmentStatus, StorageBackend
from pms.tenancy.infrastructure.django.models import Tenant


class Attachment(models.Model):
    """租户拥有的附件元数据及数据库/文件一致性状态。"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="attachments")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_attachments",
    )
    original_filename = models.CharField(max_length=255)
    display_filename = models.CharField(max_length=255)
    detected_media_type = models.CharField(max_length=127)
    detected_extension = models.CharField(max_length=16, blank=True)
    size_bytes = models.PositiveBigIntegerField(null=True)
    sha256_hex = models.CharField(max_length=64, null=True)
    storage_key = models.CharField(max_length=512, unique=True)
    storage_backend = models.CharField(
        max_length=16,
        choices=[(backend.value, backend.value) for backend in StorageBackend],
    )
    storage_version = models.PositiveIntegerField(default=1)
    status = models.CharField(
        max_length=16,
        choices=[(status.value, status.value) for status in AttachmentStatus],
    )
    source = models.CharField(max_length=64)
    failure_code = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "attachments_attachment"
        ordering = ("created_at", "id")
        constraints = [
            models.CheckConstraint(
                condition=(
                    ~Q(status=AttachmentStatus.AVAILABLE.value)
                    | (Q(size_bytes__isnull=False) & Q(sha256_hex__isnull=False))
                ),
                name="ck_attachment_available_integrity",
            )
        ]
        indexes = [
            models.Index(
                fields=("tenant", "status", "created_at"),
                name="ix_attachment_tenant_status",
            )
        ]
