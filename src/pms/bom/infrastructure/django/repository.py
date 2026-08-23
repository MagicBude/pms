"""BOM 应用端口的 Django ORM 实现。"""

from collections.abc import Mapping
from contextlib import AbstractContextManager
from decimal import Decimal
from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import Count
from django.utils import timezone

from pms.attachments.domain.attachments import AttachmentId, AttachmentStatus
from pms.attachments.infrastructure.django.models import Attachment
from pms.bom.application.service import (
    BomDiff,
    BomImportError,
    BomNotFoundError,
    BomSnapshot,
    DraftBomLine,
    MasterReference,
    ProjectAccess,
)
from pms.bom.domain.lifecycle import BomStatus
from pms.bom.domain.validation import BomLineErrorCode
from pms.bom.infrastructure.django.models import BomLine, BomVersion
from pms.master_data.infrastructure.django.models import Material, Unit
from pms.projects.domain.lifecycle import ProjectStatus
from pms.projects.infrastructure.django.models import Project
from pms.tenancy.infrastructure.django.models import Membership


class DjangoBomTransactionManager:
    """把 Django 原子事务适配为 BOM 应用端口。"""

    def atomic(self) -> AbstractContextManager[None]:
        return transaction.atomic()


class DjangoBomRepository:
    """BOM 查询以 tenant 为第一边界，并验证所有跨模块外键归属。"""

    def get_project_access(
        self, *, tenant_id: UUID, project_id: UUID, membership_id: UUID
    ) -> ProjectAccess | None:
        row = (
            Project.objects.filter(id=project_id, tenant_id=tenant_id)
            .values("id", "tenant_id", "status", "owner_membership_id")
            .first()
        )
        if row is None:
            return None
        return ProjectAccess(
            id=row["id"],
            tenant_id=row["tenant_id"],
            status=ProjectStatus(row["status"]),
            is_related=row["owner_membership_id"] == membership_id,
        )

    def find_units(self, *, tenant_id: UUID, keys: set[str]) -> dict[str, MasterReference]:
        references: dict[str, MasterReference] = {}
        for unit in Unit.objects.filter(tenant_id=tenant_id, is_active=True):
            for key in (unit.code.casefold(), unit.name.casefold(), unit.normalized_name):
                if key in keys:
                    references[key] = MasterReference(id=unit.id, code=unit.code)
        return references

    def find_materials(self, *, tenant_id: UUID, codes: set[str]) -> dict[str, MasterReference]:
        return {
            material.code: MasterReference(
                id=material.id,
                code=material.code,
                unit_id=material.unit_id,
                procurement_required=material.procurement_required,
            )
            for material in Material.objects.filter(
                tenant_id=tenant_id, code__in=codes, is_active=True
            )
        }

    def create_draft(
        self,
        *,
        tenant_id: UUID,
        project_id: UUID,
        version_number: int,
        source_attachment_id: AttachmentId,
        mapping: Mapping[str, str],
        created_by_membership_id: UUID,
        lines: list[DraftBomLine],
    ) -> BomSnapshot:
        project_exists = Project.objects.filter(
            id=project_id, tenant_id=tenant_id, status=ProjectStatus.ACTIVE
        ).exists()
        attachment_exists = Attachment.objects.filter(
            id=source_attachment_id,
            tenant_id=tenant_id,
            status=AttachmentStatus.AVAILABLE,
        ).exists()
        membership_exists = Membership.objects.filter(
            id=created_by_membership_id, tenant_id=tenant_id, is_active=True
        ).exists()
        if not project_exists or not attachment_exists or not membership_exists:
            raise BomImportError("项目、来源附件或创建成员不可用。")
        try:
            bom = BomVersion.objects.create(
                tenant_id=tenant_id,
                project_id=project_id,
                version_number=version_number,
                source_attachment_id=source_attachment_id,
                source_mapping=dict(mapping),
                created_by_membership_id=created_by_membership_id,
                status=BomStatus.DRAFT,
            )
        except IntegrityError as error:
            raise BomImportError("当前项目已存在相同 BOM 版本号。") from error
        BomLine.objects.bulk_create(
            [
                BomLine(
                    tenant_id=tenant_id,
                    bom_version=bom,
                    source_row_number=line.source_row_number,
                    level_path=line.level_path[:100],
                    assembly_code=line.assembly_code[:100],
                    assembly_name=line.assembly_name[:200],
                    material_id=line.material_id,
                    material_code=line.material_code[:64],
                    material_name=line.material_name[:200],
                    specification=line.specification[:200],
                    brand=line.brand[:100],
                    quantity_per_unit=line.quantity_per_unit,
                    unit_id=line.unit_id,
                    unit_text=line.unit_text[:64],
                    procurement_required=line.procurement_required,
                    remark=line.remark[:500],
                    validation_errors=list(line.validation_errors),
                    duplicate_key=line.duplicate_key[:300],
                )
                for line in lines
            ]
        )
        return self._snapshot(bom)

    def get_for_update(self, *, tenant_id: UUID, bom_id: UUID) -> BomSnapshot | None:
        bom = BomVersion.objects.select_for_update().filter(id=bom_id, tenant_id=tenant_id).first()
        return None if bom is None else self._snapshot(bom)

    def assign_line_material(
        self,
        *,
        tenant_id: UUID,
        bom_id: UUID,
        line_id: UUID,
        material_id: UUID,
    ) -> BomSnapshot:
        line = (
            BomLine.objects.select_for_update()
            .filter(
                id=line_id,
                bom_version_id=bom_id,
                tenant_id=tenant_id,
                bom_version__status=BomStatus.DRAFT,
            )
            .first()
        )
        material = Material.objects.filter(
            id=material_id, tenant_id=tenant_id, is_active=True
        ).first()
        if line is None or material is None:
            raise BomNotFoundError("BOM 行或物料不存在。")
        errors = set(map(str, line.validation_errors))
        errors.discard(BomLineErrorCode.MATERIAL_CONFIRMATION_REQUIRED.value)
        errors.discard(BomLineErrorCode.UNKNOWN_MATERIAL.value)
        if line.unit_id != material.unit_id:
            errors.add(BomLineErrorCode.UNIT_MISMATCH.value)
        else:
            errors.discard(BomLineErrorCode.UNIT_MISMATCH.value)
        line.material = material
        line.material_code = material.code
        line.material_name = material.name
        line.procurement_required = material.procurement_required
        line.validation_errors = sorted(errors)
        line.save(
            update_fields=(
                "material",
                "material_code",
                "material_name",
                "procurement_required",
                "validation_errors",
            )
        )
        return self._snapshot(line.bom_version)

    def confirm_duplicate(self, *, tenant_id: UUID, bom_id: UUID, line_id: UUID) -> BomSnapshot:
        line = (
            BomLine.objects.select_for_update()
            .filter(
                id=line_id,
                bom_version_id=bom_id,
                tenant_id=tenant_id,
                bom_version__status=BomStatus.DRAFT,
            )
            .first()
        )
        if line is None:
            raise BomNotFoundError("BOM 行不存在。")
        errors = set(map(str, line.validation_errors))
        if BomLineErrorCode.SUSPECTED_DUPLICATE.value not in errors:
            raise BomImportError("该行没有待确认的疑似重复问题。")
        errors.remove(BomLineErrorCode.SUSPECTED_DUPLICATE.value)
        line.duplicate_confirmed = True
        line.validation_errors = sorted(errors)
        line.save(update_fields=("duplicate_confirmed", "validation_errors"))
        return self._snapshot(line.bom_version)

    def publish(self, *, tenant_id: UUID, bom_id: UUID, membership_id: UUID) -> BomSnapshot:
        bom = (
            BomVersion.objects.select_for_update()
            .filter(id=bom_id, tenant_id=tenant_id, status=BomStatus.DRAFT)
            .first()
        )
        if bom is None:
            raise BomNotFoundError("BOM 草稿不存在。")
        BomVersion.objects.select_for_update().filter(
            tenant_id=tenant_id,
            project_id=bom.project_id,
            status=BomStatus.PUBLISHED,
        ).exclude(id=bom.id).update(status=BomStatus.SUPERSEDED)
        bom.status = BomStatus.PUBLISHED
        bom.published_by_membership_id = membership_id
        bom.published_at = timezone.now()
        bom.save(update_fields=("status", "published_by_membership", "published_at"))
        return self._snapshot(bom)

    def cancel(
        self,
        *,
        tenant_id: UUID,
        bom_id: UUID,
        membership_id: UUID,
        reason: str,
    ) -> BomSnapshot:
        updated = BomVersion.objects.filter(
            id=bom_id,
            tenant_id=tenant_id,
            status__in=(BomStatus.DRAFT, BomStatus.PUBLISHED),
        ).update(
            status=BomStatus.CANCELLED,
            cancelled_by_membership_id=membership_id,
            cancelled_at=timezone.now(),
            cancellation_reason=reason,
        )
        if updated != 1:
            raise BomNotFoundError("BOM 版本不能取消。")
        return self._snapshot(BomVersion.objects.get(id=bom_id, tenant_id=tenant_id))

    def compare(self, *, tenant_id: UUID, left_id: UUID, right_id: UUID) -> BomDiff:
        left = self._line_quantities(tenant_id=tenant_id, bom_id=left_id)
        right = self._line_quantities(tenant_id=tenant_id, bom_id=right_id)
        left_keys = set(left)
        right_keys = set(right)
        return BomDiff(
            added=tuple(sorted(right_keys - left_keys)),
            removed=tuple(sorted(left_keys - right_keys)),
            changed=tuple(sorted(key for key in left_keys & right_keys if left[key] != right[key])),
        )

    @staticmethod
    def _line_quantities(*, tenant_id: UUID, bom_id: UUID) -> dict[str, Decimal | None]:
        return {
            (line.material_code or line.duplicate_key or f"row:{line.source_row_number}"): (
                line.quantity_per_unit
            )
            for line in BomLine.objects.filter(tenant_id=tenant_id, bom_version_id=bom_id).order_by(
                "source_row_number", "id"
            )
        }

    @staticmethod
    def _snapshot(bom: BomVersion) -> BomSnapshot:
        counts = bom.lines.aggregate(
            line_count=Count("id"),
        )
        error_count = sum(
            len(errors)
            for errors in bom.lines.order_by("source_row_number").values_list(
                "validation_errors", flat=True
            )
        )
        return BomSnapshot(
            id=bom.id,
            project_id=bom.project_id,
            version_number=bom.version_number,
            status=BomStatus(bom.status),
            line_count=counts["line_count"],
            error_count=error_count,
        )


class DjangoBomProjectDownstreamLookup:
    """供项目取消用例查询是否已经形成 BOM 历史。"""

    def has_records(self, *, tenant_id: UUID, project_id: UUID) -> bool:
        return BomVersion.objects.filter(tenant_id=tenant_id, project_id=project_id).exists()
