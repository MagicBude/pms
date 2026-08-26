"""物料图纸上传、内容校验、版本替代和审计用例。"""

from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from pms.attachments.application.service import (
    DEFAULT_MAX_ATTACHMENT_SIZE_BYTES,
    AttachmentService,
    UploadAttachmentCommand,
)
from pms.audit.application.recorder import AuditRecorder
from pms.audit.domain.events import AuditEvent, AuditResult
from pms.authorization.application.authorize import PermissionGrantLookup, authorize
from pms.authorization.domain.permissions import PermissionCode
from pms.master_data.domain.drawings import DrawingFormat, detect_drawing_format
from pms.tenancy.domain.context import TenantContext


class DrawingNotFoundError(LookupError):
    """当前租户内找不到指定物料或图纸。"""


@dataclass(frozen=True, slots=True)
class UploadDrawingCommand:
    material_id: UUID
    filename: str
    content: bytes
    revision_label: str = ""
    note: str = ""


@dataclass(frozen=True, slots=True)
class DrawingSnapshot:
    id: UUID
    material_id: UUID
    attachment_id: UUID
    document_format: DrawingFormat
    version: int
    revision_label: str


class DrawingTransactions(Protocol):
    def atomic(self) -> AbstractContextManager[None]: ...


class DrawingRepository(Protocol):
    def material_exists(self, *, tenant_id: UUID, material_id: UUID) -> bool: ...

    def create_version(
        self,
        *,
        tenant_id: UUID,
        material_id: UUID,
        attachment_id: UUID,
        membership_id: UUID,
        document_format: DrawingFormat,
        revision_label: str,
        note: str,
    ) -> DrawingSnapshot: ...


class DrawingService:
    """保存经过签名校验的 PDF/DWG，并追加格式内版本。"""

    def __init__(
        self,
        *,
        repository: DrawingRepository,
        attachments: AttachmentService,
        grants: PermissionGrantLookup,
        audit: AuditRecorder,
        transactions: DrawingTransactions,
    ) -> None:
        self._repository = repository
        self._attachments = attachments
        self._grants = grants
        self._audit = audit
        self._transactions = transactions

    def upload(self, *, context: TenantContext, command: UploadDrawingCommand) -> DrawingSnapshot:
        """上传新版本；失败不会把旧当前版本标记为已替代。"""
        if not command.content:
            raise ValueError("图纸文件不能为空。")
        if len(command.content) > DEFAULT_MAX_ATTACHMENT_SIZE_BYTES:
            raise ValueError("单个图纸不能超过 25 MiB。")
        drawing_format = detect_drawing_format(filename=command.filename, content=command.content)
        revision = " ".join(command.revision_label.split())
        note = " ".join(command.note.split())
        if len(revision) > 64 or len(note) > 500:
            raise ValueError("设计修订号或图纸说明超过长度限制。")
        if not self._repository.material_exists(
            tenant_id=context.tenant_id, material_id=command.material_id
        ):
            raise DrawingNotFoundError("物料不存在。")
        authorize(
            context=context,
            resource_tenant_id=context.tenant_id,
            permission=PermissionCode.DRAWING_MANAGE,
            is_related=True,
            lookup=self._grants,
        )
        with self._transactions.atomic():
            attachment = self._attachments.upload(
                UploadAttachmentCommand(
                    context=context,
                    original_filename=command.filename,
                    detected_media_type={
                        DrawingFormat.PDF: "application/pdf",
                        DrawingFormat.DWG: "image/vnd.dwg",
                    }[drawing_format],
                    source="material_drawing",
                    chunks=(command.content,),
                )
            )
            drawing = self._repository.create_version(
                tenant_id=context.tenant_id,
                material_id=command.material_id,
                attachment_id=UUID(str(attachment.id)),
                membership_id=context.membership_id,
                document_format=drawing_format,
                revision_label=revision,
                note=note,
            )
            self._audit.record(
                AuditEvent(
                    tenant_id=context.tenant_id,
                    actor_id=context.user_id,
                    membership_id=context.membership_id,
                    action="material_drawing.uploaded",
                    object_type="material_drawing",
                    object_id=str(drawing.id),
                    result=AuditResult.SUCCESS,
                    summary={
                        "format": drawing.document_format.value,
                        "version": drawing.version,
                        "material_id": str(drawing.material_id),
                    },
                )
            )
        return drawing
