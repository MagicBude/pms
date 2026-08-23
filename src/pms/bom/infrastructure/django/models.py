"""BOM 版本与来源行的租户级 ORM 映射。"""

import uuid

from django.db import models

from pms.attachments.infrastructure.django.models import Attachment
from pms.bom.domain.lifecycle import BomStatus
from pms.master_data.infrastructure.django.models import Material, Unit
from pms.projects.infrastructure.django.models import Project
from pms.tenancy.infrastructure.django.models import Membership, Tenant


class BomVersion(models.Model):
    """项目内有序、发布后不可原地覆盖的 BOM 版本。"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="bom_versions")
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="bom_versions")
    version_number = models.PositiveIntegerField()
    source_attachment = models.ForeignKey(
        Attachment, on_delete=models.PROTECT, related_name="source_bom_versions"
    )
    source_mapping = models.JSONField(default=dict)
    status = models.CharField(
        max_length=16,
        choices=[(status.value, status.value) for status in BomStatus],
        default=BomStatus.DRAFT.value,
    )
    created_by_membership = models.ForeignKey(
        Membership, on_delete=models.PROTECT, related_name="created_bom_versions"
    )
    published_by_membership = models.ForeignKey(
        Membership,
        on_delete=models.PROTECT,
        related_name="published_bom_versions",
        null=True,
        blank=True,
    )
    cancelled_by_membership = models.ForeignKey(
        Membership,
        on_delete=models.PROTECT,
        related_name="cancelled_bom_versions",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.CharField(max_length=500, blank=True)

    class Meta:
        db_table = "bom_version"
        ordering = ("project_id", "version_number", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("project", "version_number"), name="uq_bom_project_version"
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=[status.value for status in BomStatus]),
                name="ck_bom_version_status_valid",
            ),
        ]


class BomLine(models.Model):
    """保留来源行、匹配事实、数量和可修正错误的 BOM 明细。"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="bom_lines")
    bom_version = models.ForeignKey(BomVersion, on_delete=models.CASCADE, related_name="lines")
    source_row_number = models.PositiveIntegerField()
    level_path = models.CharField(max_length=100, blank=True)
    assembly_code = models.CharField(max_length=100, blank=True)
    assembly_name = models.CharField(max_length=200, blank=True)
    material = models.ForeignKey(
        Material, on_delete=models.PROTECT, related_name="bom_lines", null=True, blank=True
    )
    material_code = models.CharField(max_length=64, blank=True)
    material_name = models.CharField(max_length=200, blank=True)
    specification = models.CharField(max_length=200, blank=True)
    brand = models.CharField(max_length=100, blank=True)
    quantity_per_unit = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    unit = models.ForeignKey(
        Unit, on_delete=models.PROTECT, related_name="bom_lines", null=True, blank=True
    )
    unit_text = models.CharField(max_length=64, blank=True)
    procurement_required = models.BooleanField(default=True)
    remark = models.CharField(max_length=500, blank=True)
    validation_errors = models.JSONField(default=list)
    duplicate_key = models.CharField(max_length=300, blank=True)
    duplicate_confirmed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "bom_line"
        ordering = ("source_row_number", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("bom_version", "source_row_number"), name="uq_bom_line_source_row"
            ),
            models.CheckConstraint(
                condition=models.Q(quantity_per_unit__isnull=True)
                | models.Q(quantity_per_unit__gt=0),
                name="ck_bom_line_quantity_positive",
            ),
        ]
