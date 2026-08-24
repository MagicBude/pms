"""客户与供应商规范包的正式用例导入和对账测试。"""

import json
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from pms.audit.infrastructure.django.models import AuditLog
from pms.legacy_migration.master_data_package import (
    LegacyCustomerRecord,
    LegacyMasterDataPackage,
    LegacySupplierRecord,
    write_legacy_master_data_package,
)
from pms.master_data.infrastructure.django.models import Customer, Supplier
from pms.tenancy.infrastructure.django.models import Tenant

PASSWORD = "P3A-master-data-only-Strong!5927"


@pytest.mark.django_db
@pytest.mark.sqlite
@pytest.mark.acceptance
def test_import_command_is_idempotent_and_reports_without_sensitive_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """规范包首次创建、再次复用，并且报告不包含银行和税务原文。"""
    monkeypatch.setenv("PMS_INITIAL_ADMIN_PASSWORD", PASSWORD)
    call_command("initialize_pms", no_color=True, verbosity=0)
    monkeypatch.delenv("PMS_INITIAL_ADMIN_PASSWORD")
    package_path = tmp_path / "master-data.json"
    write_legacy_master_data_package(_package(), package_path)
    first_report = tmp_path / "first.json"
    second_report = tmp_path / "second.json"
    with override_settings(DEPLOYMENT_PROFILE="local"):
        call_command(
            "import_legacy_master_data",
            input=package_path,
            report=first_report,
            no_color=True,
            verbosity=0,
        )
        audit_count = AuditLog.objects.count()
        call_command(
            "import_legacy_master_data",
            input=package_path,
            report=second_report,
            no_color=True,
            verbosity=0,
        )

    assert Customer.objects.filter(code="LEG-C-00002").count() == 1
    assert Supplier.objects.filter(code="LEG-S-00007").count() == 1
    assert AuditLog.objects.count() == audit_count
    first = first_report.read_text(encoding="utf-8")
    second = json.loads(second_report.read_text(encoding="utf-8"))
    assert "TEST-ACCOUNT" not in first
    assert second["customer_reused"] == 1
    assert second["supplier_reused"] == 1


@pytest.mark.django_db
@pytest.mark.sqlite
def test_conflicting_supplier_rolls_back_customers_created_earlier_in_batch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """冲突不能留下半批客户；既有供应商也不能被迁移静默覆盖。"""
    monkeypatch.setenv("PMS_INITIAL_ADMIN_PASSWORD", PASSWORD)
    call_command("initialize_pms", no_color=True, verbosity=0)
    monkeypatch.delenv("PMS_INITIAL_ADMIN_PASSWORD")
    Supplier.objects.create(
        tenant=Tenant.objects.get(code="local"),
        code="LEG-S-00007",
        name="既有冲突供应商",
        normalized_name="既有冲突供应商",
    )
    package_path = tmp_path / "master-data.json"
    write_legacy_master_data_package(_package(), package_path)
    report_path = tmp_path / "conflict-report.json"

    with (
        override_settings(DEPLOYMENT_PROFILE="local"),
        pytest.raises(CommandError, match="供应商来源第 7 行"),
    ):
        call_command(
            "import_legacy_master_data",
            input=package_path,
            report=report_path,
            no_color=True,
            verbosity=0,
        )

    assert not Customer.objects.filter(code="LEG-C-00002").exists()
    assert Supplier.objects.get(code="LEG-S-00007").name == "既有冲突供应商"
    assert not report_path.exists()


def _package() -> LegacyMasterDataPackage:
    return LegacyMasterDataPackage(
        source_manifest_sha256="a" * 64,
        customers=(
            LegacyCustomerRecord(
                2,
                "LEG-C-00002",
                "甲客户",
                "虚构客户有限公司",
                "TEST-TAX",
                "虚构地址",
                "000-0000",
                "测试银行",
                "TEST-CUSTOMER-ACCOUNT",
                "TEST-ROUTING",
            ),
        ),
        suppliers=(
            LegacySupplierRecord(
                7,
                "LEG-S-00007",
                "乙供方",
                "虚构供应商有限公司",
                "测试联系人",
                "000-0001",
                "虚构地址二",
                "TEST-SUP-TAX",
                "TEST-SUP-ROUTING",
                "测试银行二",
                "TEST-ACCOUNT",
                "加工",
                "Example Supplier",
                "Example Address",
            ),
        ),
    )
