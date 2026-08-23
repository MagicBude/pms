"""附件原子写入、租户隔离、补偿和一致性对账测试。"""

import uuid
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError

from pms.attachments.application.ports import StoredObject
from pms.attachments.application.service import (
    AttachmentFinalizationError,
    AttachmentNotFoundError,
    AttachmentService,
    UploadAttachmentCommand,
)
from pms.attachments.domain.attachments import (
    AttachmentRecord,
    AttachmentStatus,
    InvalidAttachmentFilenameError,
)
from pms.attachments.infrastructure.django.models import Attachment
from pms.attachments.infrastructure.django.repository import (
    AttachmentStateConflictError,
    DjangoAttachmentRepository,
)
from pms.attachments.infrastructure.local_storage import (
    AttachmentTooLargeError,
    InvalidStorageKeyError,
    LocalBinaryStorage,
    StorageObjectExistsError,
)
from pms.tenancy.domain.context import MembershipId, TenantContext, TenantId, UserId
from pms.tenancy.infrastructure.django.models import Membership, Tenant


def build_context(*, suffix: str) -> TenantContext:
    user = get_user_model().objects.create_user(username=f"attachment-user-{suffix}")
    tenant = Tenant.objects.create(code=f"attachment-tenant-{suffix}", name=f"Tenant {suffix}")
    membership = Membership.objects.create(user=user, tenant=tenant)
    return TenantContext(
        tenant_id=TenantId(tenant.id),
        user_id=UserId(user.id),
        membership_id=MembershipId(membership.id),
    )


def build_service(root: Path) -> tuple[AttachmentService, LocalBinaryStorage]:
    storage = LocalBinaryStorage(root)
    return (
        AttachmentService(repository=DjangoAttachmentRepository(), storage=storage),
        storage,
    )


def upload_sample(
    *, service: AttachmentService, context: TenantContext, content: bytes = b"safe-bom-content"
) -> AttachmentRecord:
    return service.upload(
        UploadAttachmentCommand(
            context=context,
            original_filename="脱敏BOM.xlsx",
            detected_media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            source="bom_import",
            chunks=[content[:4], content[4:]],
        )
    )


@pytest.mark.django_db
def test_upload_uses_random_key_and_persists_available_integrity(tmp_path: Path) -> None:
    context = build_context(suffix="success")
    service, _storage = build_service(tmp_path)

    record = upload_sample(service=service, context=context)

    assert record.status is AttachmentStatus.AVAILABLE
    assert record.size_bytes == len(b"safe-bom-content")
    assert record.sha256_hex is not None and len(record.sha256_hex) == 64
    assert record.storage_key.startswith(f"tenants/{context.tenant_id}/")
    assert record.original_filename not in record.storage_key
    with service.open_available(context=context, attachment_id=record.id) as content:
        assert content.read() == b"safe-bom-content"


@pytest.mark.django_db
def test_oversized_upload_is_failed_and_leaves_no_file(tmp_path: Path) -> None:
    context = build_context(suffix="oversized")
    service, _storage = build_service(tmp_path)

    with pytest.raises(AttachmentTooLargeError):
        service.upload(
            UploadAttachmentCommand(
                context=context,
                original_filename="large.xlsx",
                detected_media_type="application/zip",
                source="bom_import",
                chunks=[b"1234", b"5678"],
                max_size_bytes=7,
            )
        )

    model = Attachment.objects.get(tenant_id=context.tenant_id)
    assert model.status == AttachmentStatus.FAILED
    assert model.failure_code == "storage_write_failed"
    assert [path for path in tmp_path.rglob("*") if path.is_file()] == []


@pytest.mark.django_db
def test_invalid_filename_is_rejected_before_metadata_or_file_creation(tmp_path: Path) -> None:
    context = build_context(suffix="invalid-name")
    service, _storage = build_service(tmp_path)

    with pytest.raises(InvalidAttachmentFilenameError):
        service.upload(
            UploadAttachmentCommand(
                context=context,
                original_filename="../../secret.xlsx",
                detected_media_type="application/zip",
                source="bom_import",
                chunks=[b"content"],
            )
        )

    assert Attachment.objects.filter(tenant_id=context.tenant_id).exists() is False
    assert [path for path in tmp_path.rglob("*") if path.is_file()] == []


@pytest.mark.django_db
def test_stream_failure_cleans_temporary_file_and_marks_failed(tmp_path: Path) -> None:
    context = build_context(suffix="stream-failure")
    service, _storage = build_service(tmp_path)

    def broken_chunks() -> Iterator[bytes]:
        yield b"partial"
        raise RuntimeError("simulated stream failure")

    with pytest.raises(RuntimeError, match="simulated stream failure"):
        service.upload(
            UploadAttachmentCommand(
                context=context,
                original_filename="broken.xlsx",
                detected_media_type="application/zip",
                source="bom_import",
                chunks=broken_chunks(),
            )
        )

    assert Attachment.objects.get(tenant_id=context.tenant_id).status == AttachmentStatus.FAILED
    assert [path for path in tmp_path.rglob("*") if path.is_file()] == []


@pytest.mark.django_db
def test_metadata_finalization_failure_deletes_formal_object(tmp_path: Path) -> None:
    context = build_context(suffix="finalize-failure")
    repository = DjangoAttachmentRepository()
    storage = LocalBinaryStorage(tmp_path)
    service = AttachmentService(repository=repository, storage=storage)

    with (
        patch.object(repository, "mark_available", side_effect=RuntimeError("database conflict")),
        pytest.raises(AttachmentFinalizationError),
    ):
        upload_sample(service=service, context=context)

    model = Attachment.objects.get(tenant_id=context.tenant_id)
    assert model.status == AttachmentStatus.FAILED
    assert model.failure_code == "metadata_finalize_failed"
    assert (
        storage.exists(
            tenant_id=context.tenant_id,
            storage_key=model.storage_key,
        )
        is False
    )


@pytest.mark.django_db
def test_cross_tenant_and_pending_attachment_are_not_downloadable(tmp_path: Path) -> None:
    owner_context = build_context(suffix="owner")
    other_context = build_context(suffix="other")
    service, _storage = build_service(tmp_path)
    record = upload_sample(service=service, context=owner_context)

    with pytest.raises(AttachmentNotFoundError):
        service.open_available(context=other_context, attachment_id=record.id)

    Attachment.objects.filter(id=record.id).update(status=AttachmentStatus.PENDING)
    with pytest.raises(AttachmentNotFoundError):
        service.open_available(context=owner_context, attachment_id=record.id)


@pytest.mark.django_db
def test_reconciliation_reports_missing_tampered_and_unexpected_objects(tmp_path: Path) -> None:
    context = build_context(suffix="reconcile")
    service, storage = build_service(tmp_path)
    missing = upload_sample(service=service, context=context, content=b"missing")
    tampered = upload_sample(service=service, context=context, content=b"original")
    unexpected = upload_sample(service=service, context=context, content=b"unexpected")
    storage.delete(tenant_id=context.tenant_id, storage_key=missing.storage_key)
    tampered_path = tmp_path.joinpath(*tampered.storage_key.split("/"))
    tampered_path.write_bytes(b"changed-size-and-digest")
    Attachment.objects.filter(id=unexpected.id).update(status=AttachmentStatus.PENDING)

    issues = {
        (issue.attachment_id, issue.code)
        for issue in service.reconcile(tenant_id=context.tenant_id)
    }

    assert (missing.id, "missing_object") in issues
    assert (tampered.id, "size_mismatch") in issues
    assert (tampered.id, "digest_mismatch") in issues
    assert (unexpected.id, "unexpected_object") in issues


@pytest.mark.django_db
def test_available_database_record_requires_integrity_metadata() -> None:
    context = build_context(suffix="constraint")

    with pytest.raises(IntegrityError):
        Attachment.objects.create(
            tenant_id=context.tenant_id,
            created_by_id=context.user_id,
            original_filename="invalid.xlsx",
            display_filename="invalid.xlsx",
            detected_media_type="application/zip",
            detected_extension=".xlsx",
            storage_key=f"tenants/{context.tenant_id}/invalid",
            storage_backend="local",
            storage_version=1,
            status=AttachmentStatus.AVAILABLE,
            source="test",
        )


@pytest.mark.django_db
def test_repository_rejects_second_available_transition(tmp_path: Path) -> None:
    context = build_context(suffix="state-conflict")
    service, _storage = build_service(tmp_path)
    record = upload_sample(service=service, context=context)

    with pytest.raises(AttachmentStateConflictError):
        DjangoAttachmentRepository().mark_available(
            tenant_id=context.tenant_id,
            attachment_id=record.id,
            stored_object=StoredObject(
                storage_key=record.storage_key,
                size_bytes=record.size_bytes or 0,
                sha256_hex=record.sha256_hex or "",
            ),
        )


@pytest.mark.parametrize(
    "unsafe_key",
    ["../outside", "/absolute/object", "tenants/other/../../outside", "tenants\\tenant\\file"],
)
def test_local_storage_rejects_path_traversal(tmp_path: Path, unsafe_key: str) -> None:
    storage = LocalBinaryStorage(tmp_path)

    with pytest.raises(InvalidStorageKeyError):
        storage.exists(tenant_id=TenantId(uuid.uuid7()), storage_key=unsafe_key)


def test_local_storage_rejects_malformed_server_key_segments(tmp_path: Path) -> None:
    storage = LocalBinaryStorage(tmp_path)
    tenant_id = TenantId(uuid.uuid7())
    attachment_id = uuid.uuid7()
    object_id = uuid.uuid7()
    malformed_keys = [
        f"tenants/{tenant_id}/2026/13/{attachment_id}/{object_id}",
        f"tenants/{tenant_id}/2026/8/{attachment_id}/{object_id}",
        f"tenants/{tenant_id}/2026/08/not-a-uuid/{object_id}",
        f"tenants/{tenant_id}/2026/08/{attachment_id}/{object_id}/extra",
    ]

    for storage_key in malformed_keys:
        with pytest.raises(InvalidStorageKeyError):
            storage.exists(tenant_id=tenant_id, storage_key=storage_key)


@pytest.mark.django_db
def test_direct_storage_key_cannot_cross_tenant(tmp_path: Path) -> None:
    owner_context = build_context(suffix="storage-owner")
    other_context = build_context(suffix="storage-other")
    service, storage = build_service(tmp_path)
    record = upload_sample(service=service, context=owner_context)

    with pytest.raises(InvalidStorageKeyError):
        storage.open(tenant_id=other_context.tenant_id, storage_key=record.storage_key)


@pytest.mark.django_db
def test_existing_storage_key_is_never_overwritten_and_delete_is_idempotent(tmp_path: Path) -> None:
    context = build_context(suffix="no-overwrite")
    service, storage = build_service(tmp_path)
    record = upload_sample(service=service, context=context, content=b"original-evidence")

    with pytest.raises(StorageObjectExistsError):
        storage.store(
            tenant_id=context.tenant_id,
            storage_key=record.storage_key,
            chunks=[b"replacement"],
            max_size_bytes=100,
        )
    with storage.open(tenant_id=context.tenant_id, storage_key=record.storage_key) as content:
        assert content.read() == b"original-evidence"

    assert storage.delete(tenant_id=context.tenant_id, storage_key=record.storage_key) is True
    assert storage.delete(tenant_id=context.tenant_id, storage_key=record.storage_key) is False
    integrity = storage.verify(
        tenant_id=context.tenant_id,
        storage_key=record.storage_key,
        expected_size_bytes=len(b"original-evidence"),
        expected_sha256_hex=record.sha256_hex or "",
    )
    assert integrity.exists is False
