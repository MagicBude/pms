"""旧采购订单导入预检的引用完整性、租户边界和报告脱敏验收。"""

import json
from pathlib import Path

import pytest

from pms.legacy_migration.purchase_order_package import (
    LegacyPurchaseOrder,
    LegacyPurchaseOrderLine,
    LegacyPurchaseOrderPackage,
)
from pms.legacy_migration.purchase_order_preflight import (
    LegacyPurchaseOrderPreflightReport,
    preflight_legacy_purchase_orders,
    write_purchase_order_preflight_report,
)
from pms.procurement.infrastructure.django.models import PurchaseRequestLine
from pms.tenancy.infrastructure.django.models import Membership
from tests.integration.business.test_procurement_pricing import submitted_line


@pytest.mark.django_db
def test_all_existing_references_are_ready_and_report_has_no_business_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """五类引用唯一命中时允许进入导入阶段，报告只保留计数与来源行号。"""
    context, line, supplier = submitted_line(monkeypatch, tmp_path)
    package = _package(line=line, supplier_name=supplier.name)
    username = Membership.objects.get(id=context.membership_id).user.username

    report = preflight_legacy_purchase_orders(package=package, actor_username=username)
    output = tmp_path / "preflight.json"
    write_purchase_order_preflight_report(report, output)

    assert report.ready_for_import is True
    assert all(item.resolved_rows == 1 for item in report.references)
    payload = output.read_text(encoding="utf-8")
    assert supplier.name not in payload
    assert line.material.name not in payload
    assert "内部迁移备注" not in payload


@pytest.mark.django_db
def test_missing_supplier_and_request_are_reported_without_creating_placeholders(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """缺失关系只报告来源行，预检不会创建供应商或伪造生产请购。"""
    context, line, _supplier = submitted_line(monkeypatch, tmp_path)
    package = _package(
        line=line,
        supplier_name="不存在的虚构供方",
        request_number="不存在的请购号",
    )
    username = Membership.objects.get(id=context.membership_id).user.username
    before_lines = PurchaseRequestLine.objects.count()

    report = preflight_legacy_purchase_orders(package=package, actor_username=username)

    assert report.ready_for_import is False
    references = {item.reference: item for item in report.references}
    assert references["supplier"].unresolved_source_rows == (77,)
    assert references["request_line"].unresolved_source_rows == (77,)
    assert references["project"].resolved_rows == 1
    assert PurchaseRequestLine.objects.count() == before_lines


def test_report_shape_contains_only_stable_reference_names(tmp_path: Path) -> None:
    """报告 JSON 的引用标签为稳定英文键，便于不同电脑和 AI 继续处理。"""
    report = _empty_report()
    output = tmp_path / "preflight.json"

    write_purchase_order_preflight_report(report, output)

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "pms-legacy-purchase-order-preflight-v1"
    assert payload["references"] == []


def _package(
    *, line: PurchaseRequestLine, supplier_name: str, request_number: str | None = None
) -> LegacyPurchaseOrderPackage:
    request = line.purchase_request
    project = request.project
    source = LegacyPurchaseOrderLine(
        source_row_number=77,
        project_code=project.number,
        device_model=project.device_model,
        material_code=line.material.code,
        material_name=line.material.name,
        quantity="1",
        specification=line.material.specification,
        brand=line.material.brand,
        unit_name=line.unit.name,
        remark="内部迁移备注",
        project_start_date="2026-01-01",
        planned_completion_date="2026-01-31",
        receiving_department="测试部门",
        request_number=request_number or request.request_number or "",
        legacy_unit_price="10",
        legacy_saved_total="10",
        recalculated_total="10.00",
        production_unit="台",
        part_attribute=line.material.part_attribute,
        material_category=line.material.category.name,
    )
    order = LegacyPurchaseOrder(
        order_number="OLD-001",
        supplier_name=supplier_name,
        line_count=1,
        legacy_saved_total="10.00",
        recalculated_total="10.00",
        difference="0.00",
        has_amount_difference=False,
        lines=(source,),
    )
    return LegacyPurchaseOrderPackage("a" * 64, 1, 0, (order,))


def _empty_report() -> LegacyPurchaseOrderPreflightReport:
    return LegacyPurchaseOrderPreflightReport(
        schema_version="pms-legacy-purchase-order-preflight-v1",
        source_manifest_sha256="a" * 64,
        source_record_count=0,
        order_count=0,
        difference_order_count=0,
        ready_for_import=True,
        references=(),
    )
