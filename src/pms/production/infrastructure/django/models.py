"""投产批次和需求快照的租户级 ORM 映射。"""

import uuid

from django.db import models

from pms.bom.infrastructure.django.models import BomLine, BomVersion
from pms.master_data.infrastructure.django.models import Material, Unit
from pms.production.domain.release import ProductionStatus
from pms.projects.infrastructure.django.models import Project
from pms.tenancy.infrastructure.django.models import Membership, Tenant


class ProductionRelease(models.Model):
    """引用一个已发布 BOM 的投产批次。"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="production_releases")
    project = models.ForeignKey(
        Project, on_delete=models.PROTECT, related_name="production_releases"
    )
    bom_version = models.ForeignKey(
        BomVersion, on_delete=models.PROTECT, related_name="production_releases"
    )
    production_units = models.PositiveIntegerField()
    production_unit = models.CharField(max_length=64)
    receiving_department = models.CharField(max_length=100)
    status = models.CharField(
        max_length=16,
        choices=[(status.value, status.value) for status in ProductionStatus],
        default=ProductionStatus.DRAFT.value,
    )
    created_by_membership = models.ForeignKey(
        Membership, on_delete=models.PROTECT, related_name="created_production_releases"
    )
    released_by_membership = models.ForeignKey(
        Membership,
        on_delete=models.PROTECT,
        related_name="released_production_releases",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    released_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "production_release"
        ordering = ("-created_at", "-id")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(production_units__gt=0),
                name="ck_production_units_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=[status.value for status in ProductionStatus]),
                name="ck_production_status_valid",
            ),
        ]


class ProductionRequirement(models.Model):
    """投产发布时固化的 BOM 行需求和主数据快照。"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    tenant = models.ForeignKey(
        Tenant, on_delete=models.PROTECT, related_name="production_requirements"
    )
    production_release = models.ForeignKey(
        ProductionRelease, on_delete=models.CASCADE, related_name="requirements"
    )
    source_bom_line = models.ForeignKey(
        BomLine, on_delete=models.PROTECT, related_name="production_requirements"
    )
    material = models.ForeignKey(
        Material, on_delete=models.PROTECT, related_name="production_requirements"
    )
    material_code_snapshot = models.CharField(max_length=64)
    material_name_snapshot = models.CharField(max_length=200)
    unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name="production_requirements")
    quantity_per_unit = models.DecimalField(max_digits=18, decimal_places=6)
    required_quantity = models.DecimalField(max_digits=24, decimal_places=6)
    procurement_required = models.BooleanField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "production_requirement"
        ordering = ("source_bom_line__source_row_number", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("production_release", "source_bom_line"),
                name="uq_production_requirement_source",
            ),
            models.CheckConstraint(
                condition=models.Q(quantity_per_unit__gt=0),
                name="ck_requirement_unit_quantity_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(required_quantity__gt=0),
                name="ck_requirement_total_positive",
            ),
        ]
