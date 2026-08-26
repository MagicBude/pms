"""订单图纸包版本、清单、摘要与空包拒绝验收。"""

import json
from datetime import date
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import UUID
from zipfile import ZipFile

import pytest
from django.conf import settings

from pms.attachments.domain.attachments import AttachmentId
from pms.master_data.application.drawings import UploadDrawingCommand
from pms.platform.business_services import (
    attachment_service,
    drawing_package_service,
    drawing_service,
)
from pms.procurement.application.orders import PurchaseOrderConflictError
from pms.procurement.infrastructure.django.models import PurchaseOrderDrawingPackage
from pms.tenancy.domain.context import TenantContext
from tests.integration.business.test_procurement_pricing import (
    command,
    pricing_service,
    submitted_line,
)
from tests.integration.business.test_purchase_orders import order_service


@pytest.mark.django_db
@pytest.mark.acceptance
def test_package_freezes_versions_and_old_zip_does_not_change(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC-P3A-030/031/032/034/035：包版本冻结且全缺图拒绝。"""
    monkeypatch.setattr(settings, "ATTACHMENT_STORAGE_ROOT", tmp_path / "packages", raising=False)
    context, line, supplier = submitted_line(monkeypatch, tmp_path)
    quote = pricing_service().create_quote(
        context=context, command=command(line_id=line.id, supplier_id=supplier.id)
    )
    pricing_service().select_quote(context=context, quote_id=quote.id, today=date(2026, 8, 25))
    order = order_service().create_from_request(
        context=context, request_id=line.purchase_request_id
    )[0]
    order = order_service().issue(
        context=context, order_id=order.id, business_date=date(2026, 8, 26)
    )
    with pytest.raises(PurchaseOrderConflictError, match="空图纸包"):
        drawing_package_service().generate(context=context, order_id=order.id)

    pdf_v1 = drawing_service().upload(
        context=context,
        command=UploadDrawingCommand(
            material_id=line.material_id,
            filename="虚构包图纸.pdf",
            content=b"%PDF-1.4\npackage-v1",
            revision_label="A",
        ),
    )
    drawing_service().upload(
        context=context,
        command=UploadDrawingCommand(
            material_id=line.material_id,
            filename="虚构包图纸.dwg",
            content=b"AC1027package-dwg",
            revision_label="A",
        ),
    )
    package_v1 = drawing_package_service().generate(context=context, order_id=order.id)
    drawing_service().upload(
        context=context,
        command=UploadDrawingCommand(
            material_id=line.material_id,
            filename="虚构包图纸新版.pdf",
            content=b"%PDF-1.4\npackage-v2",
            revision_label="B",
        ),
    )
    package_v2 = drawing_package_service().generate(context=context, order_id=order.id)
    assert (package_v1.version, package_v2.version) == (1, 2)
    assert package_v1.included_file_count == 2
    assert package_v1.missing_material_count == 0

    manifest_v1, names_v1 = _read_package(context, package_v1.attachment_id)
    manifest_v2, names_v2 = _read_package(context, package_v2.attachment_id)
    assert manifest_v1["package_version"] == 1
    assert manifest_v2["package_version"] == 2
    assert str(pdf_v1.id) in {item["drawing_id"] for item in manifest_v1["files"]}
    assert str(pdf_v1.id) not in {item["drawing_id"] for item in manifest_v2["files"]}
    assert "manifest.json" in names_v1
    assert len(names_v1) == len(names_v2) == 3
    assert len([name for name in names_v1 if "-DWG-" in name]) == 1
    assert PurchaseOrderDrawingPackage.objects.filter(order_id=order.id).count() == 2


def _read_package(context: TenantContext, attachment_id: UUID) -> tuple[dict[str, Any], set[str]]:
    """从正式附件服务读取测试 ZIP，避免绕过实际存储边界。"""
    with attachment_service().open_available(
        context=context, attachment_id=AttachmentId(attachment_id)
    ) as stream:
        content = stream.read()
    with ZipFile(BytesIO(content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        names = set(archive.namelist())
    return manifest, names
