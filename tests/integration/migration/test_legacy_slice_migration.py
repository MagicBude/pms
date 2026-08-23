"""P2-05 受控脱敏样例迁移、重复执行和对账报告测试。"""

import json
from pathlib import Path

import pytest
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
