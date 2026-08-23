"""生产请购应用端口的 Django ORM 实现。"""

from contextlib import AbstractContextManager
from datetime import date
from decimal import Decimal
from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import Count, Sum
from django.utils import timezone

from pms.procurement.application.service import (
    PurchaseRequestConflictError,
    PurchaseRequestNotFoundError,
    PurchaseRequestSnapshot,
    PurchaseSource,
)
from pms.procurement.domain.request import (
    PurchaseRequestStatus,
    format_request_number,
    remaining_quantity,
)
from pms.procurement.infrastructure.django.models import (
    PurchaseRequest,
    PurchaseRequestLine,
    PurchaseRequestSequence,
)
from pms.production.domain.release import ProductionStatus
from pms.production.infrastructure.django.models import (
    ProductionRelease,
    ProductionRequirement,
)
from pms.projects.domain.lifecycle import ProjectStatus
from pms.tenancy.infrastructure.django.models import Membership, Tenant


class DjangoProcurementTransactionManager:
    """把 Django 原子事务适配为生产请购应用端口。"""

    def atomic(self) -> AbstractContextManager[None]:
        return transaction.atomic()


class DjangoProcurementRepository:
    """按 tenant 锁定来源、请购和日期序列，保护数量与编号一致性。"""

    def get_source(
        self, *, tenant_id: UUID, production_id: UUID, membership_id: UUID
    ) -> PurchaseSource | None:
        row = (
            ProductionRelease.objects.filter(id=production_id, tenant_id=tenant_id)
            .values(
                "id",
                "project_id",
                "status",
                "project__status",
                "project__owner_membership_id",
            )
            .first()
        )
        if row is None:
            return None
        return PurchaseSource(
            production_id=row["id"],
            project_id=row["project_id"],
            status=ProductionStatus(row["status"]),
            project_status=ProjectStatus(row["project__status"]),
            is_related=row["project__owner_membership_id"] == membership_id,
        )

    def create_or_get_draft(
        self,
        *,
        tenant_id: UUID,
        production_id: UUID,
        project_id: UUID,
        idempotency_key: str,
        created_by_membership_id: UUID,
    ) -> tuple[PurchaseRequestSnapshot, bool]:
        release = (
            ProductionRelease.objects.select_for_update()
            .filter(
                id=production_id,
                tenant_id=tenant_id,
                project_id=project_id,
                status=ProductionStatus.RELEASED,
            )
            .first()
        )
        if release is None:
            raise PurchaseRequestNotFoundError("已发布投产批次不存在。")
        existing_key = (
            PurchaseRequest.objects.select_for_update()
            .filter(tenant_id=tenant_id, idempotency_key=idempotency_key)
            .first()
        )
        if existing_key is not None:
            if existing_key.production_release_id != production_id:
                raise PurchaseRequestConflictError("幂等键已用于另一投产批次。")
            return self._snapshot(existing_key), False
        existing_active = (
            PurchaseRequest.objects.select_for_update()
            .filter(
                tenant_id=tenant_id,
                production_release_id=production_id,
            )
            .exclude(status=PurchaseRequestStatus.CANCELLED)
            .first()
        )
        if existing_active is not None:
            raise PurchaseRequestConflictError("该投产批次已经存在未取消请购。")
        membership_exists = Membership.objects.filter(
            id=created_by_membership_id, tenant_id=tenant_id, is_active=True
        ).exists()
        if not membership_exists:
            raise PurchaseRequestNotFoundError("创建成员不可用。")
        requirements = list(
            ProductionRequirement.objects.select_for_update().filter(
                tenant_id=tenant_id,
                production_release_id=production_id,
                procurement_required=True,
            )
        )
        if not requirements:
            raise PurchaseRequestConflictError("投产批次没有可请购物料。")
        try:
            request = PurchaseRequest.objects.create(
                tenant_id=tenant_id,
                project_id=project_id,
                production_release=release,
                idempotency_key=idempotency_key,
                created_by_membership_id=created_by_membership_id,
                status=PurchaseRequestStatus.DRAFT,
            )
        except IntegrityError as error:
            # 数据库唯一约束是并发竞态的最终裁判；事务回滚后调用方重试会
            # 走上面的幂等查询并得到同一结果。
            raise PurchaseRequestConflictError("请购已被并发请求创建，请重试查询。") from error
        PurchaseRequestLine.objects.bulk_create(
            [
                PurchaseRequestLine(
                    tenant_id=tenant_id,
                    purchase_request=request,
                    source_requirement=requirement,
                    material_id=requirement.material_id,
                    material_code_snapshot=requirement.material_code_snapshot,
                    material_name_snapshot=requirement.material_name_snapshot,
                    unit_id=requirement.unit_id,
                    requested_quantity=requirement.required_quantity,
                    remark="",
                )
                for requirement in requirements
            ]
        )
        return self._snapshot(request), True

    def get_for_update(
        self, *, tenant_id: UUID, request_id: UUID, membership_id: UUID
    ) -> tuple[PurchaseRequestSnapshot, bool] | None:
        request = (
            PurchaseRequest.objects.select_for_update()
            .select_related("project")
            .filter(id=request_id, tenant_id=tenant_id)
            .first()
        )
        if request is None:
            return None
        return self._snapshot(request), request.project.owner_membership_id == membership_id

    def submit(
        self,
        *,
        tenant_id: UUID,
        request_id: UUID,
        membership_id: UUID,
        business_date: date,
    ) -> PurchaseRequestSnapshot:
        request = (
            PurchaseRequest.objects.select_for_update()
            .filter(
                id=request_id,
                tenant_id=tenant_id,
                status=PurchaseRequestStatus.DRAFT,
            )
            .first()
        )
        if request is None:
            raise PurchaseRequestNotFoundError("请购草稿不存在。")
        lines = list(
            PurchaseRequestLine.objects.select_for_update()
            .filter(tenant_id=tenant_id, purchase_request=request)
            .select_related("source_requirement")
        )
        if not lines:
            raise PurchaseRequestConflictError("请购草稿没有明细。")
        for line in lines:
            other_requested = PurchaseRequestLine.objects.filter(
                tenant_id=tenant_id,
                source_requirement_id=line.source_requirement_id,
                purchase_request__status__in=(
                    PurchaseRequestStatus.DRAFT,
                    PurchaseRequestStatus.SUBMITTED,
                ),
            ).exclude(purchase_request=request).aggregate(value=Sum("requested_quantity"))[
                "value"
            ] or Decimal(0)
            available = remaining_quantity(
                required=line.source_requirement.required_quantity,
                non_cancelled_requested=other_requested,
            )
            if line.requested_quantity != available:
                raise PurchaseRequestConflictError("来源需求的剩余可请购数量已经变化。")
        sequence, _created = PurchaseRequestSequence.objects.select_for_update().get_or_create(
            tenant_id=tenant_id,
            business_date=business_date,
            defaults={"last_value": 0},
        )
        sequence.last_value += 1
        sequence.save(update_fields=("last_value",))
        request.request_number = format_request_number(
            business_date=business_date, sequence=sequence.last_value
        )
        request.status = PurchaseRequestStatus.SUBMITTED
        request.submitted_by_membership_id = membership_id
        request.submitted_at = timezone.now()
        request.save(
            update_fields=("request_number", "status", "submitted_by_membership", "submitted_at")
        )
        return self._snapshot(request)

    def cancel(self, *, tenant_id: UUID, request_id: UUID, reason: str) -> PurchaseRequestSnapshot:
        updated = PurchaseRequest.objects.filter(
            id=request_id,
            tenant_id=tenant_id,
            status__in=(PurchaseRequestStatus.DRAFT, PurchaseRequestStatus.SUBMITTED),
        ).update(
            status=PurchaseRequestStatus.CANCELLED,
            cancellation_reason=reason,
            cancelled_at=timezone.now(),
        )
        if updated != 1:
            raise PurchaseRequestNotFoundError("生产请购不能取消。")
        return self._snapshot(PurchaseRequest.objects.get(id=request_id, tenant_id=tenant_id))

    def get_tenant_timezone(self, *, tenant_id: UUID) -> str | None:
        return (
            Tenant.objects.filter(id=tenant_id, is_active=True)
            .values_list("timezone", flat=True)
            .first()
        )

    @staticmethod
    def _snapshot(request: PurchaseRequest) -> PurchaseRequestSnapshot:
        return PurchaseRequestSnapshot(
            id=request.id,
            production_id=request.production_release_id,
            project_id=request.project_id,
            status=PurchaseRequestStatus(request.status),
            request_number=request.request_number,
            line_count=request.lines.aggregate(value=Count("id"))["value"],
            idempotency_key=request.idempotency_key,
        )


class DjangoProcurementProductionDownstreamLookup:
    """供投产取消用例查询未取消请购。"""

    def has_active_purchase_request(self, *, tenant_id: UUID, production_id: UUID) -> bool:
        return (
            PurchaseRequest.objects.filter(
                tenant_id=tenant_id,
                production_release_id=production_id,
            )
            .exclude(status=PurchaseRequestStatus.CANCELLED)
            .exists()
        )
