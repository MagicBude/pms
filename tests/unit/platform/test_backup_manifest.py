"""本机备份清单纯格式与路径安全测试。"""

import json

import pytest

from pms.platform.backup_manifest import (
    BACKUP_FORMAT,
    BACKUP_FORMAT_VERSION,
    DATABASE_RELATIVE_PATH,
    AttachmentBackupEntry,
    BackupManifest,
    BackupManifestError,
    DatabaseBackupEntry,
)


def build_manifest() -> BackupManifest:
    return BackupManifest(
        format=BACKUP_FORMAT,
        format_version=BACKUP_FORMAT_VERSION,
        backup_id="019d2f5a-8d8b-7000-8000-000000000001",
        created_at="2026-08-23T12:00:00Z",
        application_version="0.0.0",
        deployment_profile="local",
        migrations=("identity.0001_initial",),
        record_counts={"identity_user": 1},
        database=DatabaseBackupEntry(
            relative_path=DATABASE_RELATIVE_PATH,
            size_bytes=128,
            sha256_hex="a" * 64,
        ),
        attachments=(
            AttachmentBackupEntry(
                attachment_id="019d2f5a-8d8b-7000-8000-000000000002",
                tenant_id="019d2f5a-8d8b-7000-8000-000000000003",
                storage_key=(
                    "tenants/019d2f5a-8d8b-7000-8000-000000000003/2026/08/"
                    "019d2f5a-8d8b-7000-8000-000000000002/"
                    "019d2f5a-8d8b-7000-8000-000000000004"
                ),
                size_bytes=4,
                sha256_hex="b" * 64,
            ),
        ),
    )


@pytest.mark.unit
def test_manifest_round_trip_preserves_stable_typed_content() -> None:
    manifest = build_manifest()

    restored = BackupManifest.from_bytes(manifest.to_bytes())

    assert restored == manifest


@pytest.mark.unit
@pytest.mark.parametrize("unsafe_path", ["../escape", "/absolute", "a\\b", "a/../../b"])
def test_manifest_rejects_attachment_path_escape(unsafe_path: str) -> None:
    payload = json.loads(build_manifest().to_bytes())
    payload["attachments"][0]["storage_key"] = unsafe_path

    with pytest.raises(BackupManifestError, match="安全相对路径"):
        BackupManifest.from_bytes(json.dumps(payload).encode())


@pytest.mark.unit
def test_manifest_rejects_unknown_fields_instead_of_guessing_compatibility() -> None:
    payload = json.loads(build_manifest().to_bytes())
    payload["future_option"] = True

    with pytest.raises(BackupManifestError, match="字段集合"):
        BackupManifest.from_bytes(json.dumps(payload).encode())
