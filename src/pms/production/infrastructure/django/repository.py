"""投产应用端口的 Django ORM 实现。"""

from contextlib import AbstractContextManager
from uuid import UUID

from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from pms.bom.domain.lifecycle import BomStatus
from pms.bom.infrastructure.django.models import BomLine, BomVersion
from pms.production.application.service import (
    ProductionNotFoundError,
    ProductionSnapshot,
    ProductionSource,
    RequirementSnapshot,
)
from pms.production.domain.release import (
    ProductionStatus,
    calculate_required_quantity,
)
from pms.production.infrastructure.django.models import (
    ProductionRelease,
    ProductionRequirement,
)
from pms.projects.domain.lifecycle import ProjectStatus
from pms.tenancy.infrastructure.django.models import Membership


class DjangoProductionTransactionManager:
    """把 Django 原子事务适配为投产应用端口。"""

    def atomic(self) -> AbstractContextManager[None]:
        return transaction.atomic()


class DjangoProductionRepository:
    """投产查询始终同时限定 tenant、项目与 BOM 归属。"""

    def get_source(
        self, *, tenant_id: UUID, project_id: UUID, bom_id: UUID, membership_id: UUID
    ) -> ProductionSource | None:
        row = (
            BomVersion.objects.filter(
                id=bom_id,
                tenant_id=tenant_id,
                project_id=project_id,
            )
            .values(
                "id",
                "status",
                "project_id",
                "project__status",
                "project__owner_membership_id",
            )
            .first()
        )
        if row is None:
            return None
        return ProductionSource(
            project_id=row["project_id"],
            bom_id=row["id"],
            project_status=ProjectStatus(row["project__status"]),
            bom_status=BomStatus(row["status"]),
            is_related=row["project__owner_membership_id"] == membership_id,
        )

    def create_draft(
        self,
        *,
        tenant_id: UUID,
        project_id: UUID,
        bom_id: UUID,
        production_units: int,
        production_unit: str,
        receiving_department: str,
        created_by_membership_id: UUID,
    ) -> ProductionSnapshot:
        source_exists = BomVersion.objects.filter(
            id=bom_id,
            tenant_id=tenant_id,
            project_id=project_id,
            project__status=ProjectStatus.ACTIVE,
            status=BomStatus.PUBLISHED,
        ).exists()
        membership_exists = Membership.objects.filter(
            id=created_by_membership_id,
            tenant_id=tenant_id,
            is_active=True,
        ).exists()
        if not source_exists or not membership_exists:
            raise ProductionNotFoundError("项目、BOM 或创建成员不可用。")
        release = ProductionRelease.objects.create(
            tenant_id=tenant_id,
            project_id=project_id,
            bom_version_id=bom_id,
            production_units=production_units,
            production_unit=production_unit,
            receiving_department=receiving_department,
            created_by_membership_id=created_by_membership_id,
            status=ProductionStatus.DRAFT,
        )
        return self._snapshot(release)

    def get_for_update(
        self, *, tenant_id: UUID, production_id: UUID, membership_id: UUID
    ) -> tuple[ProductionSnapshot, bool] | None:
        release = (
            ProductionRelease.objects.select_for_update()
            .select_related("project")
            .filter(id=production_id, tenant_id=tenant_id)
            .first()
        )
        if release is None:
            return None
        return self._snapshot(release), release.project.owner_membership_id == membership_id

    def release(
        self, *, tenant_id: UUID, production_id: UUID, membership_id: UUID
    ) -> ProductionSnapshot:
        release = (
            ProductionRelease.objects.select_for_update()
            .filter(
                id=production_id,
                tenant_id=tenant_id,
                status=ProductionStatus.DRAFT,
                project__status=ProjectStatus.ACTIVE,
                bom_version__status=BomStatus.PUBLISHED,
            )
            .first()
        )
        if release is None:
            raise ProductionNotFoundError("投产草稿或其来源不可用。")
        bom_lines = list(
            BomLine.objects.filter(
                tenant_id=tenant_id,
                bom_version_id=release.bom_version_id,
                material__isnull=False,
                unit__isnull=False,
                quantity_per_unit__isnull=False,
                validation_errors=[],
            ).select_related("material")
        )
        if not bom_lines:
            raise ValueError("已发布 BOM 没有可形成投产需求的明细。")
        ProductionRequirement.objects.bulk_create(
            [
                ProductionRequirement(
                    tenant_id=tenant_id,
                    production_release=release,
                    source_bom_line=line,
                    material_id=line.material_id,
                    material_code_snapshot=line.material_code,
                    material_name_snapshot=line.material_name,
                    unit_id=line.unit_id,
                    quantity_per_unit=line.quantity_per_unit,
                    required_quantity=calculate_required_quantity(
                        quantity_per_unit=line.quantity_per_unit,
                        production_units=release.production_units,
                    ),
                    procurement_required=line.procurement_required,
                )
                for line in bom_lines
                if line.material_id is not None
                and line.unit_id is not None
                and line.quantity_per_unit is not None
            ]
        )
        release.status = ProductionStatus.RELEASED
        release.released_by_membership_id = membership_id
        release.released_at = timezone.now()
        release.save(update_fields=("status", "released_by_membership", "released_at"))
        return self._snapshot(release)

    def cancel(self, *, tenant_id: UUID, production_id: UUID) -> ProductionSnapshot:
        updated = ProductionRelease.objects.filter(
            id=production_id,
            tenant_id=tenant_id,
            status__in=(ProductionStatus.DRAFT, ProductionStatus.RELEASED),
        ).update(status=ProductionStatus.CANCELLED)
        if updated != 1:
            raise ProductionNotFoundError("投产批次不能取消。")
        return self._snapshot(ProductionRelease.objects.get(id=production_id, tenant_id=tenant_id))

    def list_requirements(
        self, *, tenant_id: UUID, production_id: UUID
    ) -> list[RequirementSnapshot]:
        return [
            RequirementSnapshot(
                id=row.id,
                material_id=row.material_id,
                material_code=row.material_code_snapshot,
                material_name=row.material_name_snapshot,
                unit_id=row.unit_id,
                quantity_per_unit=row.quantity_per_unit,
                required_quantity=row.required_quantity,
                procurement_required=row.procurement_required,
            )
            for row in ProductionRequirement.objects.filter(
                tenant_id=tenant_id, production_release_id=production_id
            ).order_by("source_bom_line__source_row_number", "id")
        ]

    @staticmethod
    def _snapshot(release: ProductionRelease) -> ProductionSnapshot:
        requirement_count = release.requirements.aggregate(value=Count("id"))["value"]
        return ProductionSnapshot(
            id=release.id,
            project_id=release.project_id,
            bom_id=release.bom_version_id,
            production_units=release.production_units,
            status=ProductionStatus(release.status),
            requirement_count=requirement_count,
        )
