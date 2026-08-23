"""生产请购、来源行和租户日期序列的 ORM 映射。"""

import uuid

from django.db import models

from pms.master_data.infrastructure.django.models import Material, Unit
from pms.procurement.domain.request import PurchaseRequestStatus
from pms.production.infrastructure.django.models import (
    ProductionRelease,
    ProductionRequirement,
)
from pms.projects.infrastructure.django.models import Project
from pms.tenancy.infrastructure.django.models import Membership, Tenant


class PurchaseRequest(models.Model):
    """由一个投产批次生成、提交后取得正式编号的生产请购。"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="purchase_requests")
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="purchase_requests")
    production_release = models.ForeignKey(
        ProductionRelease, on_delete=models.PROTECT, related_name="purchase_requests"
    )
    idempotency_key = models.CharField(max_length=128)
    request_number = models.CharField(max_length=32, null=True, blank=True)
    status = models.CharField(
        max_length=16,
        choices=[(status.value, status.value) for status in PurchaseRequestStatus],
        default=PurchaseRequestStatus.DRAFT.value,
    )
    created_by_membership = models.ForeignKey(
        Membership, on_delete=models.PROTECT, related_name="created_purchase_requests"
    )
    submitted_by_membership = models.ForeignKey(
        Membership,
        on_delete=models.PROTECT,
        related_name="submitted_purchase_requests",
        null=True,
        blank=True,
    )
    cancelled_by_membership = models.ForeignKey(
        Membership,
        on_delete=models.PROTECT,
        related_name="cancelled_purchase_requests",
        null=True,
        blank=True,
    )
    cancellation_reason = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "procurement_purchase_request"
        ordering = ("-created_at", "-id")
        constraints = [
            models.UniqueConstraint(
                fields=("tenant", "idempotency_key"), name="uq_request_tenant_idempotency"
            ),
            models.UniqueConstraint(
                fields=("tenant", "request_number"), name="uq_request_tenant_number"
            ),
            models.UniqueConstraint(
                fields=("tenant", "production_release"),
                condition=~models.Q(status=PurchaseRequestStatus.CANCELLED.value),
                name="uq_request_active_production",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=[status.value for status in PurchaseRequestStatus]),
                name="ck_request_status_valid",
            ),
        ]


class PurchaseRequestLine(models.Model):
    """引用投产需求并冻结物料、单位和申请数量的请购行。"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    tenant = models.ForeignKey(
        Tenant, on_delete=models.PROTECT, related_name="purchase_request_lines"
    )
    purchase_request = models.ForeignKey(
        PurchaseRequest, on_delete=models.CASCADE, related_name="lines"
    )
    source_requirement = models.ForeignKey(
        ProductionRequirement, on_delete=models.PROTECT, related_name="purchase_request_lines"
    )
    material = models.ForeignKey(
        Material, on_delete=models.PROTECT, related_name="purchase_request_lines"
    )
    material_code_snapshot = models.CharField(max_length=64)
    material_name_snapshot = models.CharField(max_length=200)
    unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name="purchase_request_lines")
    requested_quantity = models.DecimalField(max_digits=24, decimal_places=6)
    remark = models.CharField(max_length=500, blank=True)

    class Meta:
        db_table = "procurement_purchase_request_line"
        ordering = ("source_requirement_id", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("purchase_request", "source_requirement"),
                name="uq_request_line_source",
            ),
            models.CheckConstraint(
                condition=models.Q(requested_quantity__gt=0),
                name="ck_request_line_quantity_positive",
            ),
        ]


class PurchaseRequestSequence(models.Model):
    """一个租户业务日期内的最后分配序号。

    行锁与唯一约束共同保护并发分配。允许事务安全导致序号缺口，但绝不
    为追求连续号而把编号放到请购事务之外。
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    tenant = models.ForeignKey(
        Tenant, on_delete=models.PROTECT, related_name="purchase_request_sequences"
    )
    business_date = models.DateField()
    last_value = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "procurement_purchase_request_sequence"
        constraints = [
            models.UniqueConstraint(
                fields=("tenant", "business_date"), name="uq_request_sequence_tenant_date"
            )
        ]
