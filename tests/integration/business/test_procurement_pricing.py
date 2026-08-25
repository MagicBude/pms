"""Phase 3A 采购报价、撤销和供应商确定集成验收。"""

from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from pms.audit.infrastructure.django.models import AuditLog
from pms.audit.infrastructure.django.recorder import DjangoAuditRecorder
from pms.authorization.application.authorize import PermissionDeniedError
from pms.authorization.domain.permissions import RoleCode
from pms.authorization.infrastructure.django.grant_lookup import DjangoPermissionGrantLookup
from pms.master_data.application.service import CreatedMasterData, CreateSupplierCommand
from pms.master_data.infrastructure.django.models import Supplier
from pms.procurement.application.pricing import (
    CreateQuoteCommand,
    PricingConflictError,
    PricingService,
)
from pms.procurement.domain.pricing import QuoteStatus
from pms.procurement.infrastructure.django.models import (
    PurchaseRequestLine,
    SupplierDecision,
)
from pms.procurement.infrastructure.django.pricing_repository import (
    DjangoPricingRepository,
    DjangoPricingTransactionManager,
)
from pms.tenancy.domain.context import TenantContext
from pms.tenancy.infrastructure.django.models import Tenant
from tests.integration.business.test_bom_workflow import (
    create_member_context,
    initialize_context,
    master_service,
)
from tests.integration.business.test_production_procurement import (
    create_released_production,
    procurement_service,
)


def pricing_service() -> PricingService:
    return PricingService(
        repository=DjangoPricingRepository(),
        grants=DjangoPermissionGrantLookup(),
        audit=DjangoAuditRecorder(),
        transactions=DjangoPricingTransactionManager(),
    )


def submitted_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[TenantContext, PurchaseRequestLine, CreatedMasterData]:
    context = initialize_context(monkeypatch)
    production = create_released_production(context=context, tmp_path=tmp_path)
    request = procurement_service().create_draft(
        context=context, production_id=production.id, idempotency_key="pricing-request"
    )
    procurement_service().submit(context=context, request_id=request.id)
    supplier = master_service().create_supplier(
        context=context,
        command=CreateSupplierCommand(
            code="SUP-PRICE-1", short_name="报价供方", name="虚构报价供应商有限公司"
        ),
    )
    return context, PurchaseRequestLine.objects.get(purchase_request_id=request.id), supplier


def command(*, line_id: UUID, supplier_id: UUID, price: str = "113") -> CreateQuoteCommand:
    return CreateQuoteCommand(
        request_line_id=line_id,
        supplier_id=supplier_id,
        quote_date=date(2026, 8, 25),
        valid_until=date(2026, 9, 25),
        currency="CNY",
        unit_price=Decimal(price),
        tax_rate=Decimal("13"),
        tax_included=True,
        minimum_order_quantity=Decimal("1"),
        lead_time_days=7,
        source_type="SUPPLIER",
        source_reference="Q-TEST-1",
        remark="虚构测试报价",
    )


@pytest.mark.django_db
@pytest.mark.acceptance
def test_quote_selection_freezes_amounts_and_reselection_keeps_history(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC-P3A-001/008/009/012：报价、金额快照、重选版本和审计同时成立。"""
    context, line, supplier = submitted_line(monkeypatch, tmp_path)
    service = pricing_service()
    first_quote = service.create_quote(
        context=context, command=command(line_id=line.id, supplier_id=supplier.id)
    )
    first = service.select_quote(context=context, quote_id=first_quote.id, today=date(2026, 8, 25))
    retry = service.select_quote(context=context, quote_id=first_quote.id, today=date(2026, 8, 25))
    assert retry.id == first.id
    assert SupplierDecision.objects.filter(request_line=line).count() == 1
    with pytest.raises(PricingConflictError, match="正在使用"):
        service.withdraw_quote(context=context, quote_id=first_quote.id)
    second_quote = service.create_quote(
        context=context,
        command=command(line_id=line.id, supplier_id=supplier.id, price="100"),
    )
    second = service.select_quote(
        context=context, quote_id=second_quote.id, today=date(2026, 8, 25)
    )

    assert first.version == 1
    assert (first.net_amount, first.tax_amount, first.gross_amount) == (
        Decimal("600.00"),
        Decimal("78.00"),
        Decimal("678.00"),
    )
    assert second.version == 2
    assert SupplierDecision.objects.filter(request_line=line).count() == 2
    assert SupplierDecision.objects.get(id=first.id).is_current is False
    assert SupplierDecision.objects.get(id=second.id).is_current is True
    assert AuditLog.objects.filter(action="supplier_quote.created").count() == 2
    assert AuditLog.objects.filter(action="supplier_decision.selected").count() == 2


@pytest.mark.django_db
@pytest.mark.acceptance
def test_withdrawal_expiry_minimum_and_request_status_are_enforced(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC-P3A-002/006/007：状态、有效期和最小订购量阻断错误确定。"""
    context, line, supplier = submitted_line(monkeypatch, tmp_path)
    service = pricing_service()
    quote = service.create_quote(
        context=context, command=command(line_id=line.id, supplier_id=supplier.id)
    )
    withdrawn = service.withdraw_quote(context=context, quote_id=quote.id)
    assert withdrawn.status is QuoteStatus.WITHDRAWN
    with pytest.raises(PricingConflictError, match="已撤销"):
        service.select_quote(context=context, quote_id=quote.id, today=date(2026, 8, 25))

    expired_command = command(line_id=line.id, supplier_id=supplier.id)
    expired_command = replace(
        expired_command, quote_date=date(2026, 8, 1), valid_until=date(2026, 8, 24)
    )
    expired = service.create_quote(context=context, command=expired_command)
    with pytest.raises(PricingConflictError, match="有效期"):
        service.select_quote(context=context, quote_id=expired.id, today=date(2026, 8, 25))

    minimum_command = command(line_id=line.id, supplier_id=supplier.id)
    minimum_command = replace(minimum_command, minimum_order_quantity=Decimal("999"))
    minimum = service.create_quote(context=context, command=minimum_command)
    with pytest.raises(PricingConflictError, match="最小订购量"):
        service.select_quote(context=context, quote_id=minimum.id, today=date(2026, 8, 25))


@pytest.mark.django_db
@pytest.mark.acceptance
def test_cross_tenant_supplier_and_read_only_role_are_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC-P3A-003/010：租户边界和维护权限均在服务端执行。"""
    context, line, supplier = submitted_line(monkeypatch, tmp_path)
    other = create_member_context(
        tenant=Tenant.objects.get(id=context.tenant_id),
        role=RoleCode.PROJECT_MANAGER,
        suffix="price",
    )
    with pytest.raises(PermissionDeniedError):
        pricing_service().create_quote(
            context=other, command=command(line_id=line.id, supplier_id=supplier.id)
        )

    other_tenant = Tenant.objects.create(code="other-price", name="Other price tenant")
    Supplier.objects.filter(id=supplier.id).update(
        tenant=other_tenant,
        code="OTHER-SUP",
        name="其他租户虚构供应商",
        normalized_name="其他租户虚构供应商",
    )
    with pytest.raises(PricingConflictError, match="当前租户"):
        pricing_service().create_quote(
            context=context, command=command(line_id=line.id, supplier_id=supplier.id)
        )
