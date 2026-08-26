"""物料图纸版本的 Django ORM 持久化适配器。"""

from uuid import UUID

from django.db.models import Max
from django.utils import timezone

from pms.master_data.application.drawings import DrawingSnapshot
from pms.master_data.domain.drawings import DrawingFormat
from pms.master_data.infrastructure.django.models import Material, MaterialDrawing


class DjangoDrawingRepository:
    """锁定物料后递增格式版本，避免并发上传产生两个当前版本。"""

    def material_exists(self, *, tenant_id: UUID, material_id: UUID) -> bool:
        return Material.objects.filter(id=material_id, tenant_id=tenant_id).exists()

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
    ) -> DrawingSnapshot:
        material = Material.objects.select_for_update().get(id=material_id, tenant_id=tenant_id)
        rows = MaterialDrawing.objects.filter(
            tenant_id=tenant_id,
            material=material,
            document_format=document_format.value,
        )
        maximum = rows.aggregate(value=Max("version"))["value"] or 0
        rows.filter(is_current=True).update(is_current=False, superseded_at=timezone.now())
        drawing = MaterialDrawing.objects.create(
            tenant_id=tenant_id,
            material=material,
            attachment_id=attachment_id,
            document_format=document_format.value,
            version=maximum + 1,
            revision_label=revision_label,
            note=note,
            material_code_snapshot=material.code,
            material_name_snapshot=material.name,
            created_by_membership_id=membership_id,
        )
        return DrawingSnapshot(
            id=drawing.id,
            material_id=drawing.material_id,
            attachment_id=drawing.attachment_id,
            document_format=document_format,
            version=drawing.version,
            revision_label=drawing.revision_label,
        )
