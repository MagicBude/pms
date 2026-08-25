"""采购报价与供应商确定的 Django ORM 适配器。"""

from datetime import date
from uuid import UUID

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from pms.master_data.infrastructure.django.models import Supplier
from pms.procurement.application.pricing import (
    CreateQuoteCommand,
    DecisionSnapshot,
    PricingConflictError,
    QuoteSnapshot,
)
from pms.procurement.domain.pricing import Currency, QuoteStatus, calculate_price_amounts
from pms.procurement.domain.request import PurchaseRequestStatus
from pms.procurement.infrastructure.django.models import (
    PurchaseRequestLine,
    SupplierDecision,
    SupplierQuote,
)


class DjangoPricingTransactionManager:
    def atomic(self) -> transaction.Atomic:
        return transaction.atomic()


class DjangoPricingRepository:
    """所有查询同时约束 tenant，并在决策时锁定请购行和现行版本。"""

    def line_access(
        self, *, tenant_id: UUID, line_id: UUID, membership_id: UUID
    ) -> tuple[PurchaseRequestStatus, bool] | None:
        line = (
            PurchaseRequestLine.objects.filter(id=line_id, tenant_id=tenant_id)
            .select_related("purchase_request__project")
            .first()
        )
        if line is None:
            return None
        return (
            PurchaseRequestStatus(line.purchase_request.status),
            line.purchase_request.project.owner_membership_id == membership_id,
        )

    def create_quote(
        self, *, tenant_id: UUID, membership_id: UUID, command: CreateQuoteCommand
    ) -> QuoteSnapshot:
        line = PurchaseRequestLine.objects.filter(
            id=command.request_line_id, tenant_id=tenant_id
        ).first()
        supplier = Supplier.objects.filter(id=command.supplier_id, tenant_id=tenant_id).first()
        if line is None or supplier is None:
            raise PricingConflictError("请购明细或供应商不属于当前租户。")
        quote = SupplierQuote.objects.create(
            tenant_id=tenant_id,
            request_line=line,
            supplier=supplier,
            quote_date=command.quote_date,
            valid_until=command.valid_until,
            currency=command.currency,
            unit_price=command.unit_price,
            tax_rate=command.tax_rate,
            tax_included=command.tax_included,
            minimum_order_quantity=command.minimum_order_quantity,
            lead_time_days=command.lead_time_days,
            source_type=command.source_type,
            source_reference=command.source_reference,
            remark=command.remark,
            created_by_membership_id=membership_id,
        )
        return self._quote_snapshot(quote)

    def quote_access(
        self, *, tenant_id: UUID, quote_id: UUID, membership_id: UUID
    ) -> tuple[QuoteSnapshot, PurchaseRequestStatus, bool] | None:
        quote = (
            SupplierQuote.objects.filter(id=quote_id, tenant_id=tenant_id)
            .select_related("request_line__purchase_request__project")
            .first()
        )
        if quote is None:
            return None
        request = quote.request_line.purchase_request
        return (
            self._quote_snapshot(quote),
            PurchaseRequestStatus(request.status),
            request.project.owner_membership_id == membership_id,
        )

    def withdraw_quote(
        self, *, tenant_id: UUID, quote_id: UUID, membership_id: UUID
    ) -> QuoteSnapshot:
        quote = SupplierQuote.objects.select_for_update().get(id=quote_id, tenant_id=tenant_id)
        if SupplierDecision.objects.filter(quote=quote, is_current=True).exists():
            raise PricingConflictError("当前供应商决策正在使用该报价，不能撤销。")
        quote.status = QuoteStatus.WITHDRAWN.value
        quote.withdrawn_by_membership_id = membership_id
        quote.withdrawn_at = timezone.now()
        quote.save(update_fields=("status", "withdrawn_by_membership", "withdrawn_at"))
        return self._quote_snapshot(quote)

    def select_quote(
        self, *, tenant_id: UUID, quote_id: UUID, membership_id: UUID, today: date
    ) -> DecisionSnapshot:
        quote = (
            SupplierQuote.objects.select_for_update()
            .select_related("request_line", "supplier")
            .get(id=quote_id, tenant_id=tenant_id)
        )
        line = PurchaseRequestLine.objects.select_for_update().get(
            id=quote.request_line_id, tenant_id=tenant_id
        )
        current_decision = (
            SupplierDecision.objects.select_for_update()
            .filter(request_line=line, is_current=True)
            .first()
        )
        # 浏览器双击或网络重试选择同一报价时直接返回现行版本，不能制造
        # 没有业务变化的虚假决策历史。
        if current_decision is not None and current_decision.quote_id == quote.id:
            return self._decision_snapshot(current_decision, changed=False)
        if quote.status != QuoteStatus.ACTIVE.value:
            raise PricingConflictError("已撤销报价不能被选择。")
        if quote.valid_until is not None and quote.valid_until < today:
            raise PricingConflictError("报价已超过有效期。")
        if (
            quote.minimum_order_quantity is not None
            and line.requested_quantity < quote.minimum_order_quantity
        ):
            raise PricingConflictError("申请数量小于报价的最小订购量。")
        amounts = calculate_price_amounts(
            quantity=line.requested_quantity,
            unit_price=quote.unit_price,
            tax_rate=quote.tax_rate,
            tax_included=quote.tax_included,
        )
        maximum = SupplierDecision.objects.filter(request_line=line).aggregate(Max("version"))[
            "version__max"
        ]
        if current_decision is not None:
            current_decision.is_current = False
            current_decision.superseded_at = timezone.now()
            current_decision.save(update_fields=("is_current", "superseded_at"))
        decision = SupplierDecision.objects.create(
            tenant_id=tenant_id,
            request_line=line,
            quote=quote,
            version=(maximum or 0) + 1,
            supplier_code_snapshot=quote.supplier.code,
            supplier_name_snapshot=quote.supplier.name,
            currency=quote.currency,
            unit_price=quote.unit_price,
            tax_rate=quote.tax_rate,
            tax_included=quote.tax_included,
            requested_quantity=line.requested_quantity,
            net_amount=amounts.net_amount,
            tax_amount=amounts.tax_amount,
            gross_amount=amounts.gross_amount,
            decided_by_membership_id=membership_id,
        )
        return self._decision_snapshot(decision, changed=True)

    @staticmethod
    def _decision_snapshot(decision: SupplierDecision, *, changed: bool) -> DecisionSnapshot:
        return DecisionSnapshot(
            id=decision.id,
            request_line_id=decision.request_line_id,
            quote_id=decision.quote_id,
            version=decision.version,
            currency=Currency(decision.currency),
            net_amount=decision.net_amount,
            tax_amount=decision.tax_amount,
            gross_amount=decision.gross_amount,
            changed=changed,
        )

    @staticmethod
    def _quote_snapshot(quote: SupplierQuote) -> QuoteSnapshot:
        return QuoteSnapshot(
            id=quote.id,
            request_line_id=quote.request_line_id,
            status=QuoteStatus(quote.status),
            currency=Currency(quote.currency),
            unit_price=quote.unit_price,
        )
