"""附件显示文件名与内部随机存储键规则。"""

import uuid
from datetime import UTC, datetime

import pytest

from pms.attachments.domain.attachments import (
    AttachmentId,
    InvalidAttachmentFilenameError,
    build_storage_key,
    detected_extension,
    normalize_original_filename,
)
from pms.tenancy.domain.context import TenantId


@pytest.mark.unit
@pytest.mark.parametrize(
    "unsafe_name",
    ["", "   ", ".", "..", "../bom.xlsx", "folder/bom.xlsx", "folder\\bom.xlsx", "bad\n.xlsx"],
)
def test_original_filename_rejects_path_and_control_characters(unsafe_name: str) -> None:
    with pytest.raises(InvalidAttachmentFilenameError):
        normalize_original_filename(unsafe_name)


@pytest.mark.unit
def test_storage_key_contains_tenant_and_random_ids_but_not_filename() -> None:
    tenant_id = TenantId(uuid.uuid7())
    attachment_id = AttachmentId(uuid.uuid7())

    storage_key = build_storage_key(
        tenant_id=tenant_id,
        attachment_id=attachment_id,
        object_id=uuid.uuid7(),
        created_at=datetime(2026, 8, 23, tzinfo=UTC),
    )

    assert storage_key.startswith(f"tenants/{tenant_id}/2026/08/{attachment_id}/")
    assert "bom.xlsx" not in storage_key


@pytest.mark.unit
def test_storage_key_rejects_naive_time() -> None:
    with pytest.raises(ValueError, match="带时区"):
        build_storage_key(
            tenant_id=TenantId(uuid.uuid7()),
            attachment_id=AttachmentId(uuid.uuid7()),
            object_id=uuid.uuid7(),
            created_at=datetime(2026, 8, 23),  # noqa: DTZ001 - 刻意构造无时区输入。
        )


@pytest.mark.unit
def test_detected_extension_is_display_metadata_not_type_detection() -> None:
    filename = normalize_original_filename(" 脱敏BOM.XLSX ")

    assert filename == "脱敏BOM.XLSX"
    assert detected_extension(filename) == ".xlsx"
