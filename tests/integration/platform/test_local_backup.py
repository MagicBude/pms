"""F-010 本机备份、篡改检测和空目录恢复集成测试。"""

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from django.db import connection
from django.test import override_settings

from pms.attachments.application.service import AttachmentService, UploadAttachmentCommand
from pms.attachments.domain.attachments import AttachmentRecord
from pms.attachments.infrastructure.django.repository import DjangoAttachmentRepository
from pms.attachments.infrastructure.local_storage import LocalBinaryStorage
from pms.identity.infrastructure.django.models import User
from pms.platform.backup_manifest import MANIFEST_DIGEST_FILENAME, MANIFEST_FILENAME
from pms.platform.bootstrap import initialize_installation
from pms.platform.local_backup import (
    BackupIntegrityError,
    LocalBackupConfigurationError,
    RestoreTargetError,
    create_local_backup,
    restore_local_backup,
    verify_local_backup,
)
from pms.tenancy.domain.context import MembershipId, TenantContext, TenantId, UserId
from pms.tenancy.infrastructure.django.models import Membership, Tenant

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.sqlite]
ATTACHMENT_CONTENT = b"phase-1-backup-attachment"


@dataclass(frozen=True, slots=True)
class LocalInstallation:
    source_data: Path
    backup_destination: Path
    record: AttachmentRecord


@pytest.fixture(autouse=True)
def require_sqlite_backend() -> None:
    if connection.vendor != "sqlite":
        pytest.skip("F-010 只验证 local SQLite 备份。")


@pytest.fixture
def local_installation(tmp_path: Path) -> Iterator[LocalInstallation]:
    source_data = tmp_path / "source-data"
    attachment_root = source_data / "attachments"
    backup_destination = tmp_path / "backups"
    attachment_root.mkdir(parents=True)
    backup_destination.mkdir()
    with override_settings(
        DEPLOYMENT_PROFILE="local",
        DATA_DIR=source_data,
        ATTACHMENT_STORAGE_ROOT=attachment_root,
    ):
        initialize_installation(
            tenant_code="local",
            tenant_name="本机租户",
            admin_username="admin",
            initial_password="F010-test-only!5927",
        )
        tenant = Tenant.objects.get(code="local")
        admin = User.objects.get(username="admin")
        membership = Membership.objects.get(tenant=tenant, user=admin)
        context = TenantContext(
            tenant_id=TenantId(tenant.id),
            user_id=UserId(admin.id),
            membership_id=MembershipId(membership.id),
        )
        service = AttachmentService(
            repository=DjangoAttachmentRepository(),
            storage=LocalBinaryStorage(attachment_root),
        )
        record = service.upload(
            UploadAttachmentCommand(
                context=context,
                original_filename="脱敏恢复样本.xlsx",
                detected_media_type="application/zip",
                source="backup_test",
                chunks=[ATTACHMENT_CONTENT],
            )
        )
        yield LocalInstallation(source_data, backup_destination, record)


def create_backup(installation: LocalInstallation) -> Path:
    return create_local_backup(installation.backup_destination).backup_set


def restored_attachment_path(root: Path, record: AttachmentRecord) -> Path:
    return root.joinpath("attachments", *record.storage_key.split("/"))


def backup_attachment_path(root: Path, record: AttachmentRecord) -> Path:
    return root / "objects" / str(record.id)


def rewrite_manifest_digest(backup_set: Path) -> None:
    content = (backup_set / MANIFEST_FILENAME).read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    (backup_set / MANIFEST_DIGEST_FILENAME).write_text(
        f"{digest}  {MANIFEST_FILENAME}\n",
        encoding="ascii",
    )


def test_management_commands_create_verify_and_restore_empty_directory(
    local_installation: LocalInstallation,
    tmp_path: Path,
) -> None:
    backup_output = StringIO()
    call_command(
        "backup_local",
        destination=str(local_installation.backup_destination),
        stdout=backup_output,
        no_color=True,
    )
    backup_sets = list(local_installation.backup_destination.iterdir())
    assert len(backup_sets) == 1
    backup_set = backup_sets[0]
    verify_output = StringIO()
    call_command(
        "verify_local_backup",
        backup_set=str(backup_set),
        stdout=verify_output,
        no_color=True,
    )
    target = tmp_path / "restored-data"
    target.mkdir()
    restore_output = StringIO()

    call_command(
        "restore_local",
        backup_set=str(backup_set),
        target_data_dir=str(target),
        stdout=restore_output,
        no_color=True,
    )

    assert (target / "pms.sqlite3").is_file()
    assert (
        restored_attachment_path(target, local_installation.record).read_bytes()
        == ATTACHMENT_CONTENT
    )
    assert "备份完成" in backup_output.getvalue()
    assert "备份验证通过" in verify_output.getvalue()
    assert "恢复完成" in restore_output.getvalue()


def test_backup_rejects_destination_inside_current_data_directory(
    local_installation: LocalInstallation,
) -> None:
    unsafe_destination = local_installation.source_data / "backups"
    unsafe_destination.mkdir()

    with pytest.raises(LocalBackupConfigurationError, match="不能位于"):
        create_local_backup(unsafe_destination)


def test_backup_rejects_nonlocal_deployment_profile(
    local_installation: LocalInstallation,
) -> None:
    with (
        override_settings(DEPLOYMENT_PROFILE="lan"),
        pytest.raises(LocalBackupConfigurationError, match="只支持 local"),
    ):
        create_local_backup(local_installation.backup_destination)


def test_backup_rejects_tampered_source_attachment_and_publishes_nothing(
    local_installation: LocalInstallation,
) -> None:
    restored_attachment_path(local_installation.source_data, local_installation.record).write_bytes(
        b"tampered"
    )

    with pytest.raises(BackupIntegrityError, match="发生变化"):
        create_local_backup(local_installation.backup_destination)

    assert list(local_installation.backup_destination.iterdir()) == []


def test_restore_rejects_missing_or_tampered_backup_attachment(
    local_installation: LocalInstallation,
    tmp_path: Path,
) -> None:
    backup_set = create_backup(local_installation)
    backup_attachment = backup_attachment_path(backup_set, local_installation.record)
    backup_attachment.write_bytes(b"tampered-backup")
    target = tmp_path / "restored-data"

    with pytest.raises(BackupIntegrityError, match="摘要不匹配"):
        restore_local_backup(backup_set=backup_set, target_data_dir=target)

    assert not target.exists()


def test_restore_rejects_nonempty_target_without_overwriting_marker(
    local_installation: LocalInstallation,
    tmp_path: Path,
) -> None:
    backup_set = create_backup(local_installation)
    target = tmp_path / "existing-data"
    target.mkdir()
    marker = target / "do-not-overwrite.txt"
    marker.write_text("preserve", encoding="utf-8")

    with pytest.raises(RestoreTargetError, match="明确空目录"):
        restore_local_backup(backup_set=backup_set, target_data_dir=target)

    assert marker.read_text(encoding="utf-8") == "preserve"


def test_restore_rejects_current_data_directory(
    local_installation: LocalInstallation,
) -> None:
    backup_set = create_backup(local_installation)

    with pytest.raises(RestoreTargetError, match="当前 PMS 数据目录"):
        restore_local_backup(
            backup_set=backup_set,
            target_data_dir=local_installation.source_data,
        )


def test_restore_rejects_path_escape_even_when_manifest_digest_is_recomputed(
    local_installation: LocalInstallation,
    tmp_path: Path,
) -> None:
    backup_set = create_backup(local_installation)
    manifest_path = backup_set / MANIFEST_FILENAME
    payload = json.loads(manifest_path.read_bytes())
    payload["attachments"][0]["storage_key"] = "../../escaped.bin"
    manifest_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    rewrite_manifest_digest(backup_set)
    target = tmp_path / "restored-data"

    with pytest.raises(BackupIntegrityError, match="清单内容无效"):
        restore_local_backup(backup_set=backup_set, target_data_dir=target)

    assert not target.exists()
    assert not (tmp_path / "escaped.bin").exists()


def test_verify_rejects_missing_attachment_file(local_installation: LocalInstallation) -> None:
    backup_set = create_backup(local_installation)
    backup_attachment_path(backup_set, local_installation.record).unlink()

    with pytest.raises(BackupIntegrityError, match="缺失"):
        verify_local_backup(backup_set)


def test_verify_rejects_modified_manifest_without_matching_digest(
    local_installation: LocalInstallation,
) -> None:
    backup_set = create_backup(local_installation)
    manifest_path = backup_set / MANIFEST_FILENAME
    manifest_path.write_bytes(manifest_path.read_bytes() + b" ")

    with pytest.raises(BackupIntegrityError, match="清单摘要不匹配"):
        verify_local_backup(backup_set)
