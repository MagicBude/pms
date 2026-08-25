"""供应商报价、撤销和确定供应商的应用用例。"""

from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from pms.audit.application.recorder import AuditRecorder
from pms.audit.domain.events import AuditEvent, AuditResult
from pms.authorization.application.authorize import PermissionGrantLookup, authorize
from pms.authorization.domain.permissions import PermissionCode
from pms.procurement.domain.pricing import Currency, QuoteSource, QuoteStatus
from pms.procurement.domain.request import PurchaseRequestStatus
from pms.tenancy.domain.context import TenantContext


class PricingNotFoundError(LookupError):
    """当前租户或权限范围内找不到报价相关对象。"""


class PricingConflictError(ValueError):
    """报价或供应商确定违反业务状态、不变量或并发结果。"""


@dataclass(frozen=True, slots=True)
class CreateQuoteCommand:
    request_line_id: UUID
    supplier_id: UUID
    quote_date: date
    valid_until: date | None
    currency: str
    unit_price: Decimal
    tax_rate: Decimal
    tax_included: bool
    minimum_order_quantity: Decimal | None
    lead_time_days: int | None
    source_type: str
    source_reference: str = ""
    remark: str = ""


@dataclass(frozen=True, slots=True)
class QuoteSnapshot:
    id: UUID
    request_line_id: UUID
    status: QuoteStatus
    currency: Currency
    unit_price: Decimal


@dataclass(frozen=True, slots=True)
class DecisionSnapshot:
    id: UUID
    request_line_id: UUID
    quote_id: UUID
    version: int
    currency: Currency
    net_amount: Decimal
    tax_amount: Decimal
    gross_amount: Decimal
    changed: bool


class PricingTransactions(Protocol):
    def atomic(self) -> AbstractContextManager[None]: ...


class PricingRepository(Protocol):
    def line_access(
        self, *, tenant_id: UUID, line_id: UUID, membership_id: UUID
    ) -> tuple[PurchaseRequestStatus, bool] | None: ...

    def create_quote(
        self, *, tenant_id: UUID, membership_id: UUID, command: CreateQuoteCommand
    ) -> QuoteSnapshot: ...

    def quote_access(
        self, *, tenant_id: UUID, quote_id: UUID, membership_id: UUID
    ) -> tuple[QuoteSnapshot, PurchaseRequestStatus, bool] | None: ...

    def withdraw_quote(
        self, *, tenant_id: UUID, quote_id: UUID, membership_id: UUID
    ) -> QuoteSnapshot: ...

    def select_quote(
        self, *, tenant_id: UUID, quote_id: UUID, membership_id: UUID, today: date
    ) -> DecisionSnapshot: ...


class PricingService:
    """执行采购报价写入和追加式供应商确定。"""

    def __init__(
        self,
        *,
        repository: PricingRepository,
        grants: PermissionGrantLookup,
        audit: AuditRecorder,
        transactions: PricingTransactions,
    ) -> None:
        self._repository = repository
        self._grants = grants
        self._audit = audit
        self._transactions = transactions

    def create_quote(self, *, context: TenantContext, command: CreateQuoteCommand) -> QuoteSnapshot:
        """给已提交请购行新增不可变报价；相同供应商可有多个历史报价。"""
        normalized = self._validate_command(command)
        access = self._repository.line_access(
            tenant_id=context.tenant_id,
            line_id=normalized.request_line_id,
            membership_id=context.membership_id,
        )
        if access is None:
            raise PricingNotFoundError("生产请购明细不存在。")
        status, is_related = access
        self._authorize(context=context, is_related=is_related)
        if status is not PurchaseRequestStatus.SUBMITTED:
            raise PricingConflictError("只有已提交生产请购可以录入报价。")
        with self._transactions.atomic():
            quote = self._repository.create_quote(
                tenant_id=context.tenant_id,
                membership_id=context.membership_id,
                command=normalized,
            )
            self._record(context, "supplier_quote.created", quote.id, {"currency": quote.currency})
        return quote

    def withdraw_quote(self, *, context: TenantContext, quote_id: UUID) -> QuoteSnapshot:
        """撤销错误报价；已被当前决策采用的报价不能撤销。"""
        with self._transactions.atomic():
            found = self._repository.quote_access(
                tenant_id=context.tenant_id,
                quote_id=quote_id,
                membership_id=context.membership_id,
            )
            if found is None:
                raise PricingNotFoundError("供应商报价不存在。")
            quote, request_status, is_related = found
            self._authorize(context=context, is_related=is_related)
            if request_status is not PurchaseRequestStatus.SUBMITTED:
                raise PricingConflictError("当前生产请购不能撤销报价。")
            if quote.status is QuoteStatus.WITHDRAWN:
                return quote
            withdrawn = self._repository.withdraw_quote(
                tenant_id=context.tenant_id,
                quote_id=quote_id,
                membership_id=context.membership_id,
            )
            self._record(context, "supplier_quote.withdrawn", quote_id, {})
        return withdrawn

    def select_quote(
        self, *, context: TenantContext, quote_id: UUID, today: date | None = None
    ) -> DecisionSnapshot:
        """选择有效报价并冻结金额；重选会生成新的追加版本。"""
        with self._transactions.atomic():
            found = self._repository.quote_access(
                tenant_id=context.tenant_id,
                quote_id=quote_id,
                membership_id=context.membership_id,
            )
            if found is None:
                raise PricingNotFoundError("供应商报价不存在。")
            quote, request_status, is_related = found
            self._authorize(context=context, is_related=is_related)
            if request_status is not PurchaseRequestStatus.SUBMITTED:
                raise PricingConflictError("只有已提交生产请购可以确定供应商。")
            if quote.status is not QuoteStatus.ACTIVE:
                raise PricingConflictError("已撤销报价不能确定为供应商依据。")
            decision = self._repository.select_quote(
                tenant_id=context.tenant_id,
                quote_id=quote_id,
                membership_id=context.membership_id,
                today=today or datetime.now(tz=UTC).date(),
            )
            if decision.changed:
                self._record(
                    context,
                    "supplier_decision.selected",
                    decision.id,
                    {"currency": decision.currency, "version": decision.version},
                )
        return decision

    def _authorize(self, *, context: TenantContext, is_related: bool) -> None:
        authorize(
            context=context,
            resource_tenant_id=context.tenant_id,
            permission=PermissionCode.PURCHASE_QUOTE_MANAGE,
            is_related=is_related,
            lookup=self._grants,
        )

    @staticmethod
    def _validate_command(command: CreateQuoteCommand) -> CreateQuoteCommand:
        try:
            currency = Currency(command.currency.strip().upper())
            source = QuoteSource(command.source_type.strip().upper())
        except ValueError as error:
            raise ValueError("币种或报价来源不受支持。") from error
        if command.unit_price <= 0:
            raise ValueError("报价单价必须大于零。")
        if command.tax_rate < 0 or command.tax_rate > 100:
            raise ValueError("税率必须在 0 至 100 之间。")
        if command.valid_until is not None and command.valid_until < command.quote_date:
            raise ValueError("报价有效期不能早于报价日期。")
        if command.minimum_order_quantity is not None and command.minimum_order_quantity <= 0:
            raise ValueError("最小订购量必须大于零。")
        if command.lead_time_days is not None and command.lead_time_days < 0:
            raise ValueError("交期天数不能为负数。")
        reference = " ".join(command.source_reference.split())
        remark = " ".join(command.remark.split())
        if len(reference) > 100 or len(remark) > 500:
            raise ValueError("报价来源编号或备注超过长度限制。")
        return CreateQuoteCommand(
            request_line_id=command.request_line_id,
            supplier_id=command.supplier_id,
            quote_date=command.quote_date,
            valid_until=command.valid_until,
            currency=currency.value,
            unit_price=command.unit_price,
            tax_rate=command.tax_rate,
            tax_included=command.tax_included,
            minimum_order_quantity=command.minimum_order_quantity,
            lead_time_days=command.lead_time_days,
            source_type=source.value,
            source_reference=reference,
            remark=remark,
        )

    def _record(
        self, context: TenantContext, action: str, object_id: UUID, summary: dict[str, object]
    ) -> None:
        self._audit.record(
            AuditEvent(
                tenant_id=context.tenant_id,
                actor_id=context.user_id,
                membership_id=context.membership_id,
                action=action,
                object_type="procurement_pricing",
                object_id=str(object_id),
                result=AuditResult.SUCCESS,
                summary=summary,
            )
        )
