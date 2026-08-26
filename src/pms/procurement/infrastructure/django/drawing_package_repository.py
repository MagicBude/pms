"""订单当前图纸选择、包版本和冻结清单的 ORM 适配器。"""

from typing import cast
from uuid import UUID

from django.db.models import Max

from pms.master_data.infrastructure.django.models import MaterialDrawing
from pms.procurement.application.drawing_packages import DrawingPackageSnapshot
from pms.procurement.infrastructure.django.models import (
    PurchaseOrder,
    PurchaseOrderDrawingPackage,
    PurchaseOrderDrawingPackageItem,
    PurchaseOrderDrawingPackageMissing,
)
from pms.procurement.infrastructure.drawing_package import (
    DrawingPackageData,
    DrawingPackageFile,
    MissingDrawingMaterial,
    RenderedDrawingPackage,
)


class DjangoDrawingPackageRepository:
    """订单锁保护包版本；图纸查询只选择 AVAILABLE 当前版本。"""

    def package_access(
        self, *, tenant_id: UUID, order_id: UUID, membership_id: UUID
    ) -> tuple[DrawingPackageData, bool] | None:
        order = (
            PurchaseOrder.objects.select_for_update()
            .filter(id=order_id, tenant_id=tenant_id)
            .first()
        )
        if order is None:
            return None
        materials = {
            line.request_line.material_id: (
                line.material_code_snapshot,
                line.material_name_snapshot,
            )
            for line in order.lines.select_related("request_line")
        }
        drawings = list(
            MaterialDrawing.objects.filter(
                tenant_id=tenant_id,
                material_id__in=materials,
                is_current=True,
                attachment__status="available",
            ).select_related("attachment")
        )
        with_drawings = {row.material_id for row in drawings}
        files = tuple(
            DrawingPackageFile(
                drawing_id=row.id,
                attachment_id=row.attachment_id,
                material_id=row.material_id,
                material_code=materials[row.material_id][0],
                material_name=materials[row.material_id][1],
                document_format=row.document_format,
                drawing_version=row.version,
                revision_label=row.revision_label,
                original_filename=row.attachment.original_filename,
                size_bytes=cast(int, row.attachment.size_bytes),
                sha256_hex=cast(str, row.attachment.sha256_hex),
            )
            for row in drawings
        )
        missing = tuple(
            MissingDrawingMaterial(
                material_id=material_id,
                material_code=snapshots[0],
                material_name=snapshots[1],
            )
            for material_id, snapshots in materials.items()
            if material_id not in with_drawings
        )
        version = (order.drawing_packages.aggregate(value=Max("version"))["value"] or 0) + 1
        is_related = order.lines.filter(
            request_line__purchase_request__project__owner_membership_id=membership_id
        ).exists()
        return (
            DrawingPackageData(
                order_id=order.id,
                order_number=order.order_number or "",
                order_status=order.status,
                package_version=version,
                files=files,
                missing=missing,
            ),
            is_related,
        )

    def link_package(
        self,
        *,
        tenant_id: UUID,
        membership_id: UUID,
        data: DrawingPackageData,
        rendered: RenderedDrawingPackage,
        attachment_id: UUID,
    ) -> DrawingPackageSnapshot:
        package = PurchaseOrderDrawingPackage.objects.create(
            tenant_id=tenant_id,
            order_id=data.order_id,
            attachment_id=attachment_id,
            version=data.package_version,
            included_file_count=len(data.files),
            missing_material_count=len(data.missing),
            created_by_membership_id=membership_id,
        )
        PurchaseOrderDrawingPackageItem.objects.bulk_create(
            [
                PurchaseOrderDrawingPackageItem(
                    package=package,
                    drawing_id=item.drawing_id,
                    material_code_snapshot=item.material_code,
                    material_name_snapshot=item.material_name,
                    document_format=item.document_format,
                    drawing_version=item.drawing_version,
                    revision_label=item.revision_label,
                    archive_path=rendered.archive_paths[item.drawing_id],
                    size_bytes=item.size_bytes,
                    sha256_hex=item.sha256_hex,
                )
                for item in data.files
            ]
        )
        PurchaseOrderDrawingPackageMissing.objects.bulk_create(
            [
                PurchaseOrderDrawingPackageMissing(
                    package=package,
                    material_id=item.material_id,
                    material_code_snapshot=item.material_code,
                    material_name_snapshot=item.material_name,
                )
                for item in data.missing
            ]
        )
        return DrawingPackageSnapshot(
            id=package.id,
            order_id=package.order_id,
            attachment_id=package.attachment_id,
            version=package.version,
            included_file_count=package.included_file_count,
            missing_material_count=package.missing_material_count,
        )
