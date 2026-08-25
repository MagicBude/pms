"""正式采购/外协订单的 Django ORM 适配器。"""

from datetime import date
from decimal import Decimal
from uuid import UUID

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from pms.procurement.application.orders import (
    PurchaseOrderConflictError,
    PurchaseOrderSnapshot,
)
from pms.procurement.domain.orders import (
    PurchaseOrderKind,
    PurchaseOrderStatus,
    derive_order_kind,
    order_number_prefix,
)
from pms.procurement.infrastructure.django.models import (
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseOrderSequence,
    PurchaseRequest,
    SupplierDecision,
)


class DjangoPurchaseOrderTransactionManager:
    def atomic(self) -> transaction.Atomic:
        return transaction.atomic()


class DjangoPurchaseOrderRepository:
    """以行锁和条件唯一约束共同阻止重复下单。"""

    def request_access(
        self, *, tenant_id: UUID, request_id: UUID, membership_id: UUID
    ) -> tuple[str, bool] | None:
        request = PurchaseRequest.objects.filter(id=request_id, tenant_id=tenant_id).first()
        if request is None:
            return None
        return request.status, request.project.owner_membership_id == membership_id

    def create_from_request(
        self, *, tenant_id: UUID, request_id: UUID, membership_id: UUID
    ) -> tuple[PurchaseOrderSnapshot, ...]:
        request = PurchaseRequest.objects.select_for_update().get(
            id=request_id, tenant_id=tenant_id
        )
        occupied = PurchaseOrderLine.objects.filter(
            tenant_id=tenant_id,
            request_line__purchase_request=request,
            is_active=True,
        ).values_list("request_line_id", flat=True)
        decisions = list(
            SupplierDecision.objects.filter(
                tenant_id=tenant_id,
                request_line__purchase_request=request,
                is_current=True,
            )
            .exclude(request_line_id__in=occupied)
            .select_related(
                "quote__supplier",
                "request_line__material__unit",
                "request_line__purchase_request__project",
            )
            .order_by("quote__supplier_id", "currency", "request_line_id")
        )
        groups: dict[tuple[UUID, str], list[SupplierDecision]] = {}
        for decision in decisions:
            groups.setdefault((decision.quote.supplier_id, decision.currency), []).append(decision)
        created: list[PurchaseOrderSnapshot] = []
        for rows in groups.values():
            first = rows[0]
            supplier = first.quote.supplier
            kind = derive_order_kind({row.request_line.material.part_attribute for row in rows})
            order = PurchaseOrder.objects.create(
                tenant_id=tenant_id,
                supplier=supplier,
                supplier_code_snapshot=first.supplier_code_snapshot,
                supplier_name_snapshot=first.supplier_name_snapshot,
                currency=first.currency,
                kind=kind.value,
                created_by_membership_id=membership_id,
            )
            for decision in rows:
                line = decision.request_line
                source_request = line.purchase_request
                PurchaseOrderLine.objects.create(
                    tenant_id=tenant_id,
                    order=order,
                    decision=decision,
                    request_line=line,
                    project_code_snapshot=source_request.project.number,
                    request_number_snapshot=source_request.request_number or "",
                    material_code_snapshot=line.material_code_snapshot,
                    material_name_snapshot=line.material_name_snapshot,
                    part_attribute_snapshot=line.material.part_attribute,
                    unit_name_snapshot=line.material.unit.name,
                    quantity=decision.requested_quantity,
                    unit_price=decision.unit_price,
                    tax_rate=decision.tax_rate,
                    tax_included=decision.tax_included,
                    net_amount=decision.net_amount,
                    tax_amount=decision.tax_amount,
                    gross_amount=decision.gross_amount,
                    remark_snapshot=line.remark,
                )
            created.append(self._snapshot(order))
        return tuple(created)

    def order_access(
        self, *, tenant_id: UUID, order_id: UUID, membership_id: UUID
    ) -> tuple[PurchaseOrderSnapshot, bool] | None:
        order = PurchaseOrder.objects.filter(id=order_id, tenant_id=tenant_id).first()
        if order is None:
            return None
        is_related = order.lines.filter(
            request_line__purchase_request__project__owner_membership_id=membership_id
        ).exists()
        return self._snapshot(order), is_related

    def issue(
        self, *, tenant_id: UUID, order_id: UUID, membership_id: UUID, business_date: date
    ) -> PurchaseOrderSnapshot:
        order = PurchaseOrder.objects.select_for_update().get(id=order_id, tenant_id=tenant_id)
        if order.status == PurchaseOrderStatus.ISSUED.value:
            return self._snapshot(order)
        if order.status != PurchaseOrderStatus.DRAFT.value:
            raise PurchaseOrderConflictError("只有草稿订单可以签发。")
        sequence, _ = PurchaseOrderSequence.objects.select_for_update().get_or_create(
            tenant_id=tenant_id,
            business_date=business_date,
            kind=order.kind,
            defaults={"last_value": 0},
        )
        sequence.last_value += 1
        sequence.save(update_fields=("last_value",))
        prefix = order_number_prefix(PurchaseOrderKind(order.kind))
        order.order_number = f"{prefix}-{business_date:%Y%m%d}-{sequence.last_value:03d}"
        order.status = PurchaseOrderStatus.ISSUED.value
        order.issued_by_membership_id = membership_id
        order.issued_at = timezone.now()
        order.save(update_fields=("order_number", "status", "issued_by_membership", "issued_at"))
        return self._snapshot(order)

    def cancel(
        self, *, tenant_id: UUID, order_id: UUID, membership_id: UUID, reason: str
    ) -> PurchaseOrderSnapshot:
        order = PurchaseOrder.objects.select_for_update().get(id=order_id, tenant_id=tenant_id)
        if order.status == PurchaseOrderStatus.CANCELLED.value:
            return self._snapshot(order)
        order.status = PurchaseOrderStatus.CANCELLED.value
        order.cancellation_reason = reason
        order.cancelled_by_membership_id = membership_id
        order.cancelled_at = timezone.now()
        order.save(
            update_fields=(
                "status",
                "cancellation_reason",
                "cancelled_by_membership",
                "cancelled_at",
            )
        )
        order.lines.filter(is_active=True).update(is_active=False)
        return self._snapshot(order)

    @staticmethod
    def _snapshot(order: PurchaseOrder) -> PurchaseOrderSnapshot:
        totals = order.lines.aggregate(
            net=Sum("net_amount"), tax=Sum("tax_amount"), gross=Sum("gross_amount")
        )
        return PurchaseOrderSnapshot(
            id=order.id,
            status=PurchaseOrderStatus(order.status),
            kind=PurchaseOrderKind(order.kind),
            order_number=order.order_number,
            supplier_name=order.supplier_name_snapshot,
            currency=order.currency,
            line_count=order.lines.count(),
            net_amount=totals["net"] or Decimal("0.00"),
            tax_amount=totals["tax"] or Decimal("0.00"),
            gross_amount=totals["gross"] or Decimal("0.00"),
        )
