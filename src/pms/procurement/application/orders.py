"""从供应商确定结果创建、签发和取消正式订单。"""

from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from pms.audit.application.recorder import AuditRecorder
from pms.audit.domain.events import AuditEvent, AuditResult
from pms.authorization.application.authorize import PermissionGrantLookup, authorize
from pms.authorization.domain.permissions import PermissionCode
from pms.procurement.domain.orders import PurchaseOrderKind, PurchaseOrderStatus
from pms.tenancy.domain.context import TenantContext


class PurchaseOrderNotFoundError(LookupError):
    """当前租户或授权范围内找不到订单或来源请购。"""


class PurchaseOrderConflictError(ValueError):
    """订单状态、来源决策或并发占用不允许当前动作。"""


@dataclass(frozen=True, slots=True)
class PurchaseOrderSnapshot:
    """供应用层返回和审计使用的稳定订单摘要。"""

    id: UUID
    status: PurchaseOrderStatus
    kind: PurchaseOrderKind
    order_number: str | None
    supplier_name: str
    currency: str
    line_count: int
    net_amount: Decimal
    tax_amount: Decimal
    gross_amount: Decimal


class PurchaseOrderTransactions(Protocol):
    def atomic(self) -> AbstractContextManager[None]: ...


class PurchaseOrderRepository(Protocol):
    def request_access(
        self, *, tenant_id: UUID, request_id: UUID, membership_id: UUID
    ) -> tuple[str, bool] | None: ...

    def create_from_request(
        self, *, tenant_id: UUID, request_id: UUID, membership_id: UUID
    ) -> tuple[PurchaseOrderSnapshot, ...]: ...

    def order_access(
        self, *, tenant_id: UUID, order_id: UUID, membership_id: UUID
    ) -> tuple[PurchaseOrderSnapshot, bool] | None: ...

    def issue(
        self, *, tenant_id: UUID, order_id: UUID, membership_id: UUID, business_date: date
    ) -> PurchaseOrderSnapshot: ...

    def cancel(
        self, *, tenant_id: UUID, order_id: UUID, membership_id: UUID, reason: str
    ) -> PurchaseOrderSnapshot: ...


class PurchaseOrderService:
    """保护正式订单的租户、权限、状态和审计边界。"""

    def __init__(
        self,
        *,
        repository: PurchaseOrderRepository,
        grants: PermissionGrantLookup,
        audit: AuditRecorder,
        transactions: PurchaseOrderTransactions,
    ) -> None:
        self._repository = repository
        self._grants = grants
        self._audit = audit
        self._transactions = transactions

    def create_from_request(
        self, *, context: TenantContext, request_id: UUID
    ) -> tuple[PurchaseOrderSnapshot, ...]:
        """把尚未下单的当前决策按供应商与币种分组成草稿订单。"""
        access = self._repository.request_access(
            tenant_id=context.tenant_id,
            request_id=request_id,
            membership_id=context.membership_id,
        )
        if access is None:
            raise PurchaseOrderNotFoundError("生产请购不存在。")
        status, is_related = access
        self._authorize(context=context, is_related=is_related)
        if status != "SUBMITTED":
            raise PurchaseOrderConflictError("只有已提交生产请购可以生成订单。")
        with self._transactions.atomic():
            orders = self._repository.create_from_request(
                tenant_id=context.tenant_id,
                request_id=request_id,
                membership_id=context.membership_id,
            )
            if not orders:
                raise PurchaseOrderConflictError("没有已确定且尚未下单的请购明细。")
            for order in orders:
                self._record(context, "purchase_order.created", order, {})
        return orders

    def issue(
        self, *, context: TenantContext, order_id: UUID, business_date: date
    ) -> PurchaseOrderSnapshot:
        """签发草稿并在同一事务内分配稳定正式编号。"""
        with self._transactions.atomic():
            order = self._get_authorized(context=context, order_id=order_id)
            if order.status is PurchaseOrderStatus.ISSUED:
                return order
            if order.status is not PurchaseOrderStatus.DRAFT:
                raise PurchaseOrderConflictError("只有草稿订单可以签发。")
            issued = self._repository.issue(
                tenant_id=context.tenant_id,
                order_id=order_id,
                membership_id=context.membership_id,
                business_date=business_date,
            )
            self._record(context, "purchase_order.issued", issued, {})
        return issued

    def cancel(
        self, *, context: TenantContext, order_id: UUID, reason: str
    ) -> PurchaseOrderSnapshot:
        """取消订单并释放明细占用；原因必填以支持业务追溯。"""
        normalized = " ".join(reason.split())
        if not normalized or len(normalized) > 500:
            raise ValueError("取消原因必填且不能超过 500 字。")
        with self._transactions.atomic():
            order = self._get_authorized(context=context, order_id=order_id)
            if order.status is PurchaseOrderStatus.CANCELLED:
                return order
            cancelled = self._repository.cancel(
                tenant_id=context.tenant_id,
                order_id=order_id,
                membership_id=context.membership_id,
                reason=normalized,
            )
            self._record(context, "purchase_order.cancelled", cancelled, {})
        return cancelled

    def _get_authorized(self, *, context: TenantContext, order_id: UUID) -> PurchaseOrderSnapshot:
        found = self._repository.order_access(
            tenant_id=context.tenant_id,
            order_id=order_id,
            membership_id=context.membership_id,
        )
        if found is None:
            raise PurchaseOrderNotFoundError("正式订单不存在。")
        order, is_related = found
        self._authorize(context=context, is_related=is_related)
        return order

    def _authorize(self, *, context: TenantContext, is_related: bool) -> None:
        authorize(
            context=context,
            resource_tenant_id=context.tenant_id,
            permission=PermissionCode.PURCHASE_ORDER_MANAGE,
            is_related=is_related,
            lookup=self._grants,
        )

    def _record(
        self,
        context: TenantContext,
        action: str,
        order: PurchaseOrderSnapshot,
        extra: dict[str, object],
    ) -> None:
        summary: dict[str, object] = {
            "status": order.status.value,
            "kind": order.kind.value,
            "order_number": order.order_number or "",
            "currency": order.currency,
            "line_count": order.line_count,
        }
        summary.update(extra)
        self._audit.record(
            AuditEvent(
                tenant_id=context.tenant_id,
                actor_id=context.user_id,
                membership_id=context.membership_id,
                action=action,
                object_type="purchase_order",
                object_id=str(order.id),
                result=AuditResult.SUCCESS,
                summary=summary,
            )
        )
