"""生成版本化正式订单 Excel 并关联受控附件。"""

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from pms.attachments.application.service import AttachmentService, UploadAttachmentCommand
from pms.audit.application.recorder import AuditRecorder
from pms.audit.domain.events import AuditEvent, AuditResult
from pms.authorization.application.authorize import PermissionGrantLookup, authorize
from pms.authorization.domain.permissions import PermissionCode
from pms.procurement.application.orders import (
    PurchaseOrderConflictError,
    PurchaseOrderNotFoundError,
)
from pms.procurement.infrastructure.spreadsheet import OrderExportData
from pms.tenancy.domain.context import TenantContext


@dataclass(frozen=True, slots=True)
class OrderDocumentSnapshot:
    id: UUID
    order_id: UUID
    attachment_id: UUID
    version: int
    filename: str


class OrderDocumentTransactions(Protocol):
    def atomic(self) -> AbstractContextManager[None]: ...


class OrderDocumentRepository(Protocol):
    def export_access(
        self, *, tenant_id: UUID, order_id: UUID, membership_id: UUID
    ) -> tuple[OrderExportData, bool, int] | None: ...

    def link_document(
        self,
        *,
        tenant_id: UUID,
        order_id: UUID,
        attachment_id: UUID,
        membership_id: UUID,
        version: int,
    ) -> OrderDocumentSnapshot: ...


class OrderDocumentService:
    """从已签发事实生成追加版本，文件本身永远不驱动订单状态。"""

    def __init__(
        self,
        *,
        repository: OrderDocumentRepository,
        renderer: Callable[[OrderExportData], bytes],
        attachments: AttachmentService,
        grants: PermissionGrantLookup,
        audit: AuditRecorder,
        transactions: OrderDocumentTransactions,
    ) -> None:
        self._repository = repository
        self._renderer = renderer
        self._attachments = attachments
        self._grants = grants
        self._audit = audit
        self._transactions = transactions

    def generate(self, *, context: TenantContext, order_id: UUID) -> OrderDocumentSnapshot:
        """锁定订单、生成 xlsx、保存附件并创建下一文档版本。"""
        with self._transactions.atomic():
            found = self._repository.export_access(
                tenant_id=context.tenant_id,
                order_id=order_id,
                membership_id=context.membership_id,
            )
            if found is None:
                raise PurchaseOrderNotFoundError("正式订单不存在。")
            data, is_related, version = found
            authorize(
                context=context,
                resource_tenant_id=context.tenant_id,
                permission=PermissionCode.PURCHASE_ORDER_MANAGE,
                is_related=is_related,
                lookup=self._grants,
            )
            if data.status != "ISSUED" or not data.order_number:
                raise PurchaseOrderConflictError("只有已签发订单可以生成 Excel。")
            filename = f"{data.order_number}-V{version}.xlsx"
            content = self._renderer(data)
            attachment = self._attachments.upload(
                UploadAttachmentCommand(
                    context=context,
                    original_filename=filename,
                    detected_media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    source="purchase_order_document",
                    chunks=(content,),
                )
            )
            document = self._repository.link_document(
                tenant_id=context.tenant_id,
                order_id=order_id,
                attachment_id=UUID(str(attachment.id)),
                membership_id=context.membership_id,
                version=version,
            )
            self._audit.record(
                AuditEvent(
                    tenant_id=context.tenant_id,
                    actor_id=context.user_id,
                    membership_id=context.membership_id,
                    action="purchase_order.document_generated",
                    object_type="purchase_order_document",
                    object_id=str(document.id),
                    result=AuditResult.SUCCESS,
                    summary={"order_number": data.order_number, "version": version},
                )
            )
        return document
