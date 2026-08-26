"""正式订单页面、动作和版本化下载的 HTTP 验收。"""

from datetime import date
from pathlib import Path

import pytest
from django.conf import settings
from django.test import Client

from pms.master_data.application.drawings import UploadDrawingCommand
from pms.platform.business_services import drawing_service
from pms.procurement.infrastructure.django.models import (
    PurchaseOrderDocument,
    PurchaseOrderDrawingPackage,
)
from pms.tenancy.infrastructure.django.models import Membership
from pms.web.context import SESSION_MEMBERSHIP_KEY
from tests.integration.business.test_procurement_pricing import (
    command,
    pricing_service,
    submitted_line,
)
from tests.integration.business.test_purchase_orders import order_service


@pytest.mark.django_db
@pytest.mark.acceptance
def test_order_page_issues_generates_and_downloads_versioned_excel(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, client: Client
) -> None:
    """AC-P3A-021/022/024：页面完成签发、生成、展示和受控下载。"""
    monkeypatch.setattr(settings, "ATTACHMENT_STORAGE_ROOT", tmp_path / "web-orders", raising=False)
    context, line, supplier = submitted_line(monkeypatch, tmp_path)
    quote = pricing_service().create_quote(
        context=context, command=command(line_id=line.id, supplier_id=supplier.id)
    )
    pricing_service().select_quote(context=context, quote_id=quote.id, today=date(2026, 8, 25))
    order = order_service().create_from_request(
        context=context, request_id=line.purchase_request_id
    )[0]
    membership = Membership.objects.select_related("user").get(id=context.membership_id)
    client.force_login(membership.user)
    session = client.session
    session[SESSION_MEMBERSHIP_KEY] = str(membership.id)
    session.save()

    detail_url = f"/orders/{order.id}/"
    response = client.get(detail_url)
    assert response.status_code == 200
    assert "签发正式订单" in response.content.decode()
    response = client.post(f"/orders/{order.id}/issue/", follow=True)
    assert response.status_code == 200
    assert (
        "OS-" in response.content.decode()
        or "PO-" in response.content.decode()
        or "MX-" in response.content.decode()
    )
    response = client.post(f"/orders/{order.id}/documents/new/", follow=True)
    assert response.status_code == 200
    assert "下载 V1" in response.content.decode()
    document = PurchaseOrderDocument.objects.get(order_id=order.id)
    download = client.get(f"/order-documents/{document.attachment_id}/download/")
    assert download.status_code == 200
    assert download["Content-Disposition"].endswith('.xlsx"')

    drawing_service().upload(
        context=context,
        command=UploadDrawingCommand(
            material_id=line.material_id,
            filename="虚构订单页面图纸.pdf",
            content=b"%PDF-1.4\nweb-package",
        ),
    )
    response = client.post(f"/orders/{order.id}/drawing-packages/new/", follow=True)
    assert response.status_code == 200
    assert "图纸包 V1" in response.content.decode()
    package = PurchaseOrderDrawingPackage.objects.get(order_id=order.id)
    package_download = client.get(f"/drawing-packages/{package.attachment_id}/download/")
    assert package_download.status_code == 200
    assert ".zip" in package_download["Content-Disposition"]
