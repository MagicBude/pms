"""完整 `SLICE-001` 业务链的 SQLite 备份、空目录恢复与关键字段对账。"""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from django.core.management import call_command
from django.test import override_settings

from pms.audit.infrastructure.django.models import AuditLog
from pms.legacy_migration.schema import load_legacy_slice_package
from pms.legacy_migration.service import LegacySliceMigrationService
from pms.platform.local_backup import (
    create_local_backup,
    restore_local_backup,
    verify_local_backup,
)

PASSWORD = "P2-05-backup-only-Strong!5927"
FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "migration"
    / "legacy-slice-v1-synthetic.json"
)


@pytest.mark.django_db(transaction=True)
@pytest.mark.sqlite
@pytest.mark.acceptance
def test_complete_business_chain_survives_backup_and_empty_directory_restore(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC-S001-004/042：项目、附件、状态和审计随一致性备份恢复。"""
    source_data = tmp_path / "source-data"
    attachment_root = source_data / "attachments"
    backup_root = tmp_path / "backups"
    restored_data = tmp_path / "restored-data"
    attachment_root.mkdir(parents=True)
    backup_root.mkdir()
    monkeypatch.setenv("PMS_INITIAL_ADMIN_PASSWORD", PASSWORD)
    call_command("initialize_pms", no_color=True, verbosity=0)
    monkeypatch.delenv("PMS_INITIAL_ADMIN_PASSWORD")

    with override_settings(
        DEPLOYMENT_PROFILE="local",
        DATA_DIR=source_data,
        ATTACHMENT_STORAGE_ROOT=attachment_root,
    ):
        report = LegacySliceMigrationService().migrate(package=load_legacy_slice_package(FIXTURE))
        assert report.overall_status == "MATCHED"
        source_audit_count = AuditLog.objects.count()
        backup_set = create_local_backup(backup_root).backup_set
        verify_local_backup(backup_set)
        restore_local_backup(backup_set=backup_set, target_data_dir=restored_data)

    with closing(sqlite3.connect(restored_data / "pms.sqlite3")) as restored:
        project = restored.execute(
            "SELECT number, status FROM projects_project WHERE number = ?",
            ("MIG-2026-001",),
        ).fetchone()
        bom = restored.execute("SELECT version_number, status FROM bom_version").fetchone()
        production = restored.execute(
            "SELECT production_units, status FROM production_release"
        ).fetchone()
        purchase_request = restored.execute(
            "SELECT request_number, status FROM procurement_purchase_request"
        ).fetchone()
        restored_audit_count = restored.execute("SELECT COUNT(*) FROM audit_log").fetchone()
        attachment = restored.execute(
            "SELECT original_filename, size_bytes, sha256_hex FROM attachments_attachment"
        ).fetchone()

    assert project == ("MIG-2026-001", "ACTIVE")
    assert bom == (1, "PUBLISHED")
    assert production == (3, "RELEASED")
    assert purchase_request is not None
    assert purchase_request[0]
    assert purchase_request[1] == "SUBMITTED"
    assert restored_audit_count == (source_audit_count,)
    assert attachment is not None
    assert attachment[0] == "legacy-synthetic-slice-001.xlsx"
    assert attachment[1] > 0
    assert len(attachment[2]) == 64
