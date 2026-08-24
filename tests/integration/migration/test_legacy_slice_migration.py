"""P2-05 受控脱敏样例迁移、重复执行和对账报告测试。"""

import json
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from pms.attachments.infrastructure.django.models import Attachment
from pms.audit.infrastructure.django.models import AuditLog
from pms.bom.infrastructure.django.models import BomVersion
from pms.master_data.infrastructure.django.models import Customer, Material
from pms.procurement.infrastructure.django.models import PurchaseRequest
from pms.production.infrastructure.django.models import ProductionRelease
from pms.projects.infrastructure.django.models import Project

PASSWORD = "P2-05-only-Strong!5927"
FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "migration"
    / "legacy-slice-v1-synthetic.json"
)


def initialize(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PMS_INITIAL_ADMIN_PASSWORD", PASSWORD)
    call_command("initialize_pms", no_color=True, verbosity=0)
    monkeypatch.delenv("PMS_INITIAL_ADMIN_PASSWORD")


@pytest.mark.django_db
@pytest.mark.sqlite
@pytest.mark.acceptance
def test_synthetic_package_imports_reconciles_and_repeats_without_duplicates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC-S001-043 技术部分：虚构包可迁移对账，但报告明确不能冒充业务签收。"""
    initialize(monkeypatch)
    attachment_root = tmp_path / "attachments"
    attachment_root.mkdir()
    first_report = tmp_path / "first-report.json"
    second_report = tmp_path / "second-report.json"
    with override_settings(
        DEPLOYMENT_PROFILE="local",
        DATA_DIR=tmp_path,
        ATTACHMENT_STORAGE_ROOT=attachment_root,
    ):
        call_command(
            "migrate_legacy_slice",
            input=str(FIXTURE),
            report=str(first_report),
            no_color=True,
            verbosity=0,
        )
        first_audit_count = AuditLog.objects.count()
        call_command(
            "migrate_legacy_slice",
            input=str(FIXTURE),
            report=str(second_report),
            no_color=True,
            verbosity=0,
        )

    report = json.loads(first_report.read_text(encoding="utf-8"))
    assert report["overall_status"] == "MATCHED"
    assert report["acceptance_scope"] == "TECHNICAL_ONLY"
    assert all(item["status"] == "MATCHED" for item in report["checks"])
    assert Customer.objects.filter(code="CUS-MIG").count() == 1
    assert Material.objects.filter(code__startswith="MAT-MIG-").count() == 2
    assert Project.objects.filter(number="MIG-2026-001").count() == 1
    assert BomVersion.objects.count() == 1
    assert ProductionRelease.objects.count() == 1
    assert PurchaseRequest.objects.count() == 1
    assert Attachment.objects.count() == 1
    assert AuditLog.objects.count() == first_audit_count
    assert json.loads(second_report.read_text(encoding="utf-8"))["overall_status"] == "MATCHED"


@pytest.mark.django_db
@pytest.mark.sqlite
@pytest.mark.acceptance
def test_unaccepted_difference_writes_report_and_returns_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC-S001-043：差异没有原因和接受人时不能得到成功退出状态。"""
    initialize(monkeypatch)
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["legacy_purchase_candidates"][0]["requested_quantity"] = "7.000000"
    input_path = tmp_path / "difference.json"
    input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    report_path = tmp_path / "difference-report.json"
    attachment_root = tmp_path / "attachments"
    attachment_root.mkdir()
    with (
        override_settings(
            DEPLOYMENT_PROFILE="local",
            DATA_DIR=tmp_path,
            ATTACHMENT_STORAGE_ROOT=attachment_root,
        ),
        pytest.raises(CommandError, match="未签收差异"),
    ):
        call_command(
            "migrate_legacy_slice",
            input=str(input_path),
            report=str(report_path),
            no_color=True,
            verbosity=0,
        )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["overall_status"] == "DIFFERENCES_PENDING"
    difference = next(item for item in report["checks"] if item["status"] == "DIFFERENCE_PENDING")
    assert difference["rule_id"] == "BR-PUR-001"
    assert difference["accepted_by"] is None
    assert difference["reason"] is None


@pytest.mark.django_db
@pytest.mark.sqlite
def test_business_pending_package_requires_explicit_isolated_review_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """真实待确认包默认禁止写库，防止把自动选择误当成业务签收。"""
    initialize(monkeypatch)
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["sample"] = {
        "id": "real-pending-review",
        "kind": "business_pending",
        "confirmed_by": None,
    }
    input_path = tmp_path / "pending.json"
    input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    report_path = tmp_path / "pending-report.json"

    with (
        override_settings(DEPLOYMENT_PROFILE="local"),
        pytest.raises(CommandError, match="--allow-business-pending"),
    ):
        call_command(
            "migrate_legacy_slice",
            input=str(input_path),
            report=str(report_path),
            no_color=True,
            verbosity=0,
        )

    assert not Project.objects.exists()
    assert not report_path.exists()


@pytest.mark.django_db
@pytest.mark.sqlite
@pytest.mark.acceptance
def test_v2_business_pending_package_imports_only_with_explicit_review_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """显式隔离复核允许保留 v2 溯源字段，但报告仍不能冒充业务签收。"""
    initialize(monkeypatch)
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["schema_version"] = "pms-legacy-slice-v2"
    payload["sample"] = {
        "id": "pending-v2-review",
        "kind": "business_pending",
        "confirmed_by": None,
    }
    for index, material in enumerate(payload["master_data"]["materials"]):
        material["part_attribute"] = "采购件" if index == 0 else "加工件"
    for index, row in enumerate(payload["bom"]["rows"], start=2):
        row.update(
            {
                "source_row_number": index,
                "level_path": f"1.{index - 1}",
                "assembly_code": "ASM-TEST",
                "assembly_name": "虚构测试部套",
            }
        )
    input_path = tmp_path / "pending-v2.json"
    input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    report_path = tmp_path / "pending-v2-report.json"
    attachment_root = tmp_path / "attachments"
    attachment_root.mkdir()

    with override_settings(
        DEPLOYMENT_PROFILE="local",
        DATA_DIR=tmp_path,
        ATTACHMENT_STORAGE_ROOT=attachment_root,
    ):
        call_command(
            "migrate_legacy_slice",
            input=str(input_path),
            report=str(report_path),
            allow_business_pending=True,
            no_color=True,
            verbosity=0,
        )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["overall_status"] == "MATCHED"
    assert report["acceptance_scope"] == "BUSINESS_PENDING"
    material = Material.objects.get(code="MAT-MIG-001")
    assert material.part_attribute == "采购件"
    bom = BomVersion.objects.get()
    assert list(
        bom.lines.order_by("source_row_number").values_list(
            "source_row_number", "assembly_code", "assembly_name"
        )
    ) == [
        (2, "ASM-TEST", "虚构测试部套"),
        (3, "ASM-TEST", "虚构测试部套"),
    ]


@pytest.mark.django_db
@pytest.mark.sqlite
def test_business_pending_flag_still_rejects_formal_data_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """显式开关不是正式库通行证，默认 data 目录仍必须硬拒绝。"""
    initialize(monkeypatch)
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["sample"] = {
        "id": "pending-formal-data",
        "kind": "business_pending",
        "confirmed_by": None,
    }
    input_path = tmp_path / "pending-formal.json"
    input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    report_path = tmp_path / "pending-formal-report.json"

    with (
        override_settings(
            DEPLOYMENT_PROFILE="local",
            DATA_DIR=settings.BASE_DIR / "data",
        ),
        pytest.raises(CommandError, match="独立 PMS_DATA_DIR"),
    ):
        call_command(
            "migrate_legacy_slice",
            input=str(input_path),
            report=str(report_path),
            allow_business_pending=True,
            no_color=True,
            verbosity=0,
        )

    assert not Project.objects.exists()
    assert not report_path.exists()
