"""Phase 3A 正式采购/外协订单领域与持久化验收。"""

from dataclasses import replace
from datetime import date
from io import BytesIO
from pathlib import Path

import pytest
from django.conf import settings
from openpyxl import load_workbook

from pms.attachments.domain.attachments import AttachmentId
from pms.audit.infrastructure.django.models import AuditLog
from pms.audit.infrastructure.django.recorder import DjangoAuditRecorder
from pms.authorization.application.authorize import PermissionDeniedError
from pms.authorization.domain.permissions import RoleCode
from pms.authorization.infrastructure.django.grant_lookup import DjangoPermissionGrantLookup
from pms.platform.business_services import attachment_service, order_document_service
from pms.procurement.application.orders import (
    PurchaseOrderConflictError,
    PurchaseOrderNotFoundError,
    PurchaseOrderService,
)
from pms.procurement.domain.orders import PurchaseOrderKind, PurchaseOrderStatus
from pms.procurement.infrastructure.django.models import (
    PurchaseOrder,
    PurchaseOrderDocument,
    PurchaseOrderLine,
)
from pms.procurement.infrastructure.django.order_repository import (
    DjangoPurchaseOrderRepository,
    DjangoPurchaseOrderTransactionManager,
)
from pms.tenancy.domain.context import TenantId
from pms.tenancy.infrastructure.django.models import Tenant
from tests.integration.business.test_bom_workflow import create_member_context
from tests.integration.business.test_procurement_pricing import (
    command,
    pricing_service,
    submitted_line,
)


def order_service() -> PurchaseOrderService:
    return PurchaseOrderService(
        repository=DjangoPurchaseOrderRepository(),
        grants=DjangoPermissionGrantLookup(),
        audit=DjangoAuditRecorder(),
        transactions=DjangoPurchaseOrderTransactionManager(),
    )


@pytest.mark.django_db
@pytest.mark.acceptance
def test_create_issue_cancel_and_reorder_preserve_history(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC-P3A-014/015/016/017/019/020/024：订单全生命周期可追溯。"""
    context, line, supplier = submitted_line(monkeypatch, tmp_path)
    line.material.part_attribute = "加工件"
    line.material.save(update_fields=("part_attribute",))
    quote = pricing_service().create_quote(
        context=context, command=command(line_id=line.id, supplier_id=supplier.id)
    )
    decision = pricing_service().select_quote(
        context=context, quote_id=quote.id, today=date(2026, 8, 25)
    )

    created = order_service().create_from_request(
        context=context, request_id=line.purchase_request_id
    )
    assert len(created) == 1
    draft = created[0]
    assert draft.kind is PurchaseOrderKind.OUTSOURCE
    assert (draft.net_amount, draft.tax_amount, draft.gross_amount) == (
        decision.net_amount,
        decision.tax_amount,
        decision.gross_amount,
    )
    frozen = PurchaseOrderLine.objects.get(order_id=draft.id)
    assert frozen.material_code_snapshot == line.material_code_snapshot
    assert frozen.part_attribute_snapshot == "加工件"
    assert frozen.is_active is True

    issued = order_service().issue(
        context=context, order_id=draft.id, business_date=date(2026, 8, 25)
    )
    retry = order_service().issue(
        context=context, order_id=draft.id, business_date=date(2026, 8, 25)
    )
    assert issued.status is PurchaseOrderStatus.ISSUED
    assert issued.order_number == "OS-20260825-001"
    assert retry.order_number == issued.order_number

    with pytest.raises(PurchaseOrderConflictError, match="尚未下单"):
        order_service().create_from_request(context=context, request_id=line.purchase_request_id)
    cancelled = order_service().cancel(
        context=context, order_id=draft.id, reason="虚构测试订单信息有误"
    )
    assert cancelled.status is PurchaseOrderStatus.CANCELLED
    assert PurchaseOrderLine.objects.get(order_id=draft.id).is_active is False

    replacement = order_service().create_from_request(
        context=context, request_id=line.purchase_request_id
    )[0]
    replacement = order_service().issue(
        context=context, order_id=replacement.id, business_date=date(2026, 8, 25)
    )
    assert replacement.order_number == "OS-20260825-002"
    assert PurchaseOrder.objects.count() == 2
    assert AuditLog.objects.filter(action="purchase_order.created").count() == 2
    assert AuditLog.objects.filter(action="purchase_order.issued").count() == 2
    assert AuditLog.objects.filter(action="purchase_order.cancelled").count() == 1


@pytest.mark.django_db
@pytest.mark.acceptance
def test_read_only_role_and_cross_tenant_order_are_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC-P3A-023：正式订单写操作服从服务端权限与租户边界。"""
    context, line, supplier = submitted_line(monkeypatch, tmp_path)
    quote = pricing_service().create_quote(
        context=context, command=command(line_id=line.id, supplier_id=supplier.id)
    )
    pricing_service().select_quote(context=context, quote_id=quote.id, today=date(2026, 8, 25))
    project_manager = create_member_context(
        tenant=line.tenant, role=RoleCode.PROJECT_MANAGER, suffix="order"
    )
    with pytest.raises(PermissionDeniedError):
        order_service().create_from_request(
            context=project_manager, request_id=line.purchase_request_id
        )
    draft = order_service().create_from_request(
        context=context, request_id=line.purchase_request_id
    )[0]
    foreign_tenant = Tenant.objects.create(code="other-order", name="Other order tenant")
    foreign_context = replace(context, tenant_id=TenantId(foreign_tenant.id))
    with pytest.raises(PurchaseOrderNotFoundError):
        order_service().issue(
            context=foreign_context,
            order_id=draft.id,
            business_date=date(2026, 8, 25),
        )


@pytest.mark.django_db
@pytest.mark.acceptance
def test_each_excel_generation_adds_downloadable_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC-P3A-021/022：Excel 只读取冻结事实并追加可下载历史版本。"""
    monkeypatch.setattr(
        settings, "ATTACHMENT_STORAGE_ROOT", tmp_path / "order-files", raising=False
    )
    context, line, supplier = submitted_line(monkeypatch, tmp_path)
    quote = pricing_service().create_quote(
        context=context, command=command(line_id=line.id, supplier_id=supplier.id)
    )
    pricing_service().select_quote(context=context, quote_id=quote.id, today=date(2026, 8, 25))
    order = order_service().create_from_request(
        context=context, request_id=line.purchase_request_id
    )[0]
    order = order_service().issue(
        context=context, order_id=order.id, business_date=date(2026, 8, 25)
    )

    first = order_document_service().generate(context=context, order_id=order.id)
    second = order_document_service().generate(context=context, order_id=order.id)
    assert (first.version, second.version) == (1, 2)
    assert PurchaseOrderDocument.objects.filter(order_id=order.id).count() == 2
    with attachment_service().open_available(
        context=context, attachment_id=AttachmentId(first.attachment_id)
    ) as stream:
        workbook = load_workbook(BytesIO(stream.read()), read_only=True, data_only=True)
    sheet = workbook["正式订单"]
    assert sheet["B2"].value == order.order_number
    assert sheet["D2"].value == order.kind.value
    assert sheet["F2"].value == order.supplier_name
    assert sheet["C6"].value == line.material_code_snapshot
    workbook.close()
