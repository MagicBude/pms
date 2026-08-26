"""从已签发订单冻结当前图纸并生成版本化 ZIP 的应用用例。"""

from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from pms.attachments.application.service import AttachmentService, UploadAttachmentCommand
from pms.attachments.domain.attachments import AttachmentId
from pms.audit.application.recorder import AuditRecorder
from pms.audit.domain.events import AuditEvent, AuditResult
from pms.authorization.application.authorize import PermissionGrantLookup, authorize
from pms.authorization.domain.permissions import PermissionCode
from pms.procurement.application.orders import (
    PurchaseOrderConflictError,
    PurchaseOrderNotFoundError,
)
from pms.procurement.infrastructure.drawing_package import (
    DrawingPackageData,
    RenderedDrawingPackage,
)
from pms.tenancy.domain.context import TenantContext

MAX_DRAWING_PACKAGE_SIZE_BYTES = 250 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class DrawingPackageSnapshot:
    id: UUID
    order_id: UUID
    attachment_id: UUID
    version: int
    included_file_count: int
    missing_material_count: int


class DrawingPackageTransactions(Protocol):
    def atomic(self) -> AbstractContextManager[None]: ...


class DrawingPackageRepository(Protocol):
    def package_access(
        self, *, tenant_id: UUID, order_id: UUID, membership_id: UUID
    ) -> tuple[DrawingPackageData, bool] | None: ...

    def link_package(
        self,
        *,
        tenant_id: UUID,
        membership_id: UUID,
        data: DrawingPackageData,
        rendered: RenderedDrawingPackage,
        attachment_id: UUID,
    ) -> DrawingPackageSnapshot: ...


class DrawingPackageRenderer(Protocol):
    def __call__(
        self, data: DrawingPackageData, contents: dict[UUID, bytes]
    ) -> RenderedDrawingPackage: ...


class DrawingPackageService:
    """生成包时复核每个附件摘要，并冻结所选图纸版本。"""

    def __init__(
        self,
        *,
        repository: DrawingPackageRepository,
        renderer: DrawingPackageRenderer,
        attachments: AttachmentService,
        grants: PermissionGrantLookup,
        audit: AuditRecorder,
        transactions: DrawingPackageTransactions,
    ) -> None:
        self._repository = repository
        self._renderer = renderer
        self._attachments = attachments
        self._grants = grants
        self._audit = audit
        self._transactions = transactions

    def generate(self, *, context: TenantContext, order_id: UUID) -> DrawingPackageSnapshot:
        """为已签发订单追加一个可复现 ZIP；全部缺图时拒绝。"""
        with self._transactions.atomic():
            found = self._repository.package_access(
                tenant_id=context.tenant_id,
                order_id=order_id,
                membership_id=context.membership_id,
            )
            if found is None:
                raise PurchaseOrderNotFoundError("正式订单不存在。")
            data, is_related = found
            authorize(
                context=context,
                resource_tenant_id=context.tenant_id,
                permission=PermissionCode.DRAWING_PACKAGE_GENERATE,
                is_related=is_related,
                lookup=self._grants,
            )
            if data.order_status != "ISSUED":
                raise PurchaseOrderConflictError("只有已签发订单可以生成图纸包。")
            if not data.files:
                raise PurchaseOrderConflictError("订单全部物料都缺少当前图纸，不能生成空图纸包。")
            contents: dict[UUID, bytes] = {}
            for item in data.files:
                with self._attachments.open_available(
                    context=context, attachment_id=AttachmentId(item.attachment_id)
                ) as stream:
                    contents[item.attachment_id] = stream.read()
            rendered = self._renderer(data, contents)
            attachment = self._attachments.upload(
                UploadAttachmentCommand(
                    context=context,
                    original_filename=(f"{data.order_number}-drawings-V{data.package_version}.zip"),
                    detected_media_type="application/zip",
                    source="purchase_order_drawing_package",
                    chunks=(rendered.content,),
                    max_size_bytes=MAX_DRAWING_PACKAGE_SIZE_BYTES,
                )
            )
            package = self._repository.link_package(
                tenant_id=context.tenant_id,
                membership_id=context.membership_id,
                data=data,
                rendered=rendered,
                attachment_id=UUID(str(attachment.id)),
            )
            self._audit.record(
                AuditEvent(
                    tenant_id=context.tenant_id,
                    actor_id=context.user_id,
                    membership_id=context.membership_id,
                    action="purchase_order.drawing_package_generated",
                    object_type="purchase_order_drawing_package",
                    object_id=str(package.id),
                    result=AuditResult.SUCCESS,
                    summary={
                        "order_number": data.order_number,
                        "version": package.version,
                        "included_file_count": package.included_file_count,
                        "missing_material_count": package.missing_material_count,
                    },
                )
            )
        return package
