"""租户限定且使用条件更新保护状态迁移的 Django 附件仓储。"""

from django.utils import timezone

from pms.attachments.application.ports import StoredObject
from pms.attachments.domain.attachments import (
    AttachmentId,
    AttachmentRecord,
    AttachmentStatus,
    StorageBackend,
)
from pms.attachments.infrastructure.django.models import Attachment
from pms.tenancy.domain.context import TenantId, UserId


class AttachmentStateConflictError(RuntimeError):
    """表示附件已不在调用方预期的 PENDING 状态。"""


class DjangoAttachmentRepository:
    """通过 tenant 条件约束每次附件读取和更新。"""

    def create_pending(self, record: AttachmentRecord) -> AttachmentRecord:
        """持久化 PENDING 快照；二进制写入必须发生在本方法成功返回后。"""
        if record.status is not AttachmentStatus.PENDING:
            raise ValueError("新附件元数据必须处于 PENDING。")
        model = Attachment.objects.create(
            id=record.id,
            tenant_id=record.tenant_id,
            created_by_id=record.created_by_id,
            original_filename=record.original_filename,
            display_filename=record.display_filename,
            detected_media_type=record.detected_media_type,
            detected_extension=record.detected_extension,
            size_bytes=None,
            sha256_hex=None,
            storage_key=record.storage_key,
            storage_backend=record.storage_backend,
            storage_version=record.storage_version,
            status=AttachmentStatus.PENDING,
            source=record.source,
            failure_code="",
        )
        return self._to_record(model)

    def mark_available(
        self,
        *,
        tenant_id: TenantId,
        attachment_id: AttachmentId,
        stored_object: StoredObject,
    ) -> AttachmentRecord:
        """用单条条件 UPDATE 防止并发最终状态互相覆盖。"""
        updated = Attachment.objects.filter(
            id=attachment_id,
            tenant_id=tenant_id,
            status=AttachmentStatus.PENDING,
            storage_key=stored_object.storage_key,
        ).update(
            size_bytes=stored_object.size_bytes,
            sha256_hex=stored_object.sha256_hex,
            status=AttachmentStatus.AVAILABLE,
            failure_code="",
            updated_at=timezone.now(),
        )
        if updated != 1:
            raise AttachmentStateConflictError("附件状态已经变化，不能标记为可用。")
        return self._to_record(Attachment.objects.get(id=attachment_id, tenant_id=tenant_id))

    def mark_failed(
        self,
        *,
        tenant_id: TenantId,
        attachment_id: AttachmentId,
        failure_code: str,
    ) -> None:
        """只改变 PENDING，避免失败补偿覆盖已完成或已隔离状态。"""
        Attachment.objects.filter(
            id=attachment_id,
            tenant_id=tenant_id,
            status=AttachmentStatus.PENDING,
        ).update(
            status=AttachmentStatus.FAILED,
            failure_code=failure_code[:64],
            updated_at=timezone.now(),
        )

    def get_available(
        self, *, tenant_id: TenantId, attachment_id: AttachmentId
    ) -> AttachmentRecord | None:
        """把跨租户、未知 ID 和半成品统一折叠为 None。"""
        model = Attachment.objects.filter(
            id=attachment_id,
            tenant_id=tenant_id,
            status=AttachmentStatus.AVAILABLE,
        ).first()
        return None if model is None else self._to_record(model)

    def list_for_reconciliation(self, *, tenant_id: TenantId) -> list[AttachmentRecord]:
        """只读取一个租户并采用模型稳定顺序，避免跨租户对账泄露。"""
        return [
            self._to_record(model)
            for model in Attachment.objects.filter(tenant_id=tenant_id).order_by("created_at", "id")
        ]

    @staticmethod
    def _to_record(model: Attachment) -> AttachmentRecord:
        """把 ORM 值转换为应用层稳定枚举和强类型 ID。"""
        return AttachmentRecord(
            id=AttachmentId(model.id),
            tenant_id=TenantId(model.tenant_id),
            created_by_id=UserId(model.created_by_id),
            original_filename=model.original_filename,
            display_filename=model.display_filename,
            detected_media_type=model.detected_media_type,
            detected_extension=model.detected_extension,
            size_bytes=model.size_bytes,
            sha256_hex=model.sha256_hex,
            storage_key=model.storage_key,
            storage_backend=StorageBackend(model.storage_backend),
            storage_version=model.storage_version,
            status=AttachmentStatus(model.status),
            source=model.source,
            failure_code=model.failure_code,
        )
