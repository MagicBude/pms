"""SQLite 本机档案的备份集创建、离线验证与空目录恢复。

数据库与附件不能共享事务。这里先用 SQLite Online Backup API 取得一致
快照，再以快照中的 AVAILABLE 附件元数据为清单复制二进制；复制过程中
重新计算摘要，并在发布备份集前再次检查对象集合。任何漂移都会安全失败，
不会把部分目录命名为完整备份。

恢复始终写入目标目录旁的私有暂存目录，完成全部摘要、迁移、记录数和
附件验证后才原子发布到显式空目标。它绝不覆盖当前 ``PMS_DATA_DIR``。
"""

import hashlib
import os
import shutil
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import BinaryIO

from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from pms.attachments.domain.attachments import AttachmentStatus, StorageBackend
from pms.attachments.infrastructure.local_storage import LocalBinaryStorage, LocalStorageError
from pms.platform.backup_manifest import (
    BACKUP_FORMAT,
    BACKUP_FORMAT_VERSION,
    DATABASE_RELATIVE_PATH,
    MANIFEST_DIGEST_FILENAME,
    MANIFEST_FILENAME,
    SHA256_PATTERN,
    AttachmentBackupEntry,
    BackupManifest,
    BackupManifestError,
    DatabaseBackupEntry,
    backup_object_relative_path,
    validate_safe_relative_path,
)
from pms.tenancy.domain.context import TenantId

COPY_CHUNK_SIZE_BYTES = 1024 * 1024


class LocalBackupError(RuntimeError):
    """本机备份、验证或恢复无法安全继续。"""


class LocalBackupConfigurationError(LocalBackupError):
    """命令不在受支持的 local + SQLite 配置边界内。"""


class BackupIntegrityError(LocalBackupError):
    """数据库、附件或清单之间存在缺失、篡改或不一致。"""


class RestoreTargetError(LocalBackupError):
    """恢复目标可能覆盖数据、逃逸边界或不是明确空目录。"""


@dataclass(frozen=True, slots=True)
class BackupCreationResult:
    """成功发布的备份集及其可安全展示计数。"""

    backup_set: Path
    backup_id: str
    attachment_count: int


@dataclass(frozen=True, slots=True)
class RestoreResult:
    """成功原子发布的恢复数据目录及其附件计数。"""

    target_data_dir: Path
    backup_id: str
    attachment_count: int


def create_local_backup(destination_root: Path) -> BackupCreationResult:
    """在用户选择的现有目录中创建不可部分可见的本机备份集。

    Args:
        destination_root: 用于保存新备份集的现有目录。它不能位于当前
            ``PMS_DATA_DIR`` 内；是否位于另一物理磁盘需由部署者确认。

    Returns:
        最终备份集路径、备份 UUID 和已核验 AVAILABLE 附件数量。

    Raises:
        LocalBackupConfigurationError: 当前不是 local + SQLite 档案。
        BackupIntegrityError: 迁移、数据库或附件在备份时不一致。
        LocalBackupError: 目标目录无效或文件系统操作失败。

    Side Effects:
        只在 destination_root 下创建一个新目录；不修改业务记录、附件或
        当前数据目录。失败时清理本函数创建的暂存目录。
    """
    data_dir, attachment_root = _configured_local_paths()
    destination = _require_existing_directory(destination_root, label="备份目标")
    if _is_within(destination, data_dir):
        raise LocalBackupConfigurationError("备份目标不能位于当前 PMS 数据目录内。")

    expected_migrations = _expected_current_migrations(require_fully_applied=True)
    backup_uuid = uuid.uuid7()
    created_at = datetime.now(tz=UTC)
    directory_name = f"pms-backup-{created_at:%Y%m%d}-{backup_uuid.hex[:12]}"
    final_path = destination / directory_name
    staging_path = destination / f".{backup_uuid.hex[:12]}.pending"
    if final_path.exists() or staging_path.exists():
        raise LocalBackupError("备份目标中已经存在同名备份或暂存目录。")

    try:
        staging_path.mkdir()
        database_path = _resolve_member(staging_path, DATABASE_RELATIVE_PATH)
        database_path.parent.mkdir(parents=True)
        _create_sqlite_snapshot(database_path)
        _validate_sqlite_integrity(database_path)
        applied_migrations = _read_applied_migrations(database_path)
        if applied_migrations != expected_migrations:
            raise BackupIntegrityError("SQLite 快照迁移记录与当前应用不一致。")
        _validate_initialized_installation(database_path)

        attachments = _read_available_attachments(database_path)
        _copy_snapshot_attachments(
            entries=attachments,
            source_root=attachment_root,
            backup_root=staging_path,
        )
        database_size, database_digest = _hash_file(database_path)
        manifest = BackupManifest(
            format=BACKUP_FORMAT,
            format_version=BACKUP_FORMAT_VERSION,
            backup_id=str(backup_uuid),
            created_at=created_at.isoformat().replace("+00:00", "Z"),
            application_version=_application_version(),
            deployment_profile="local",
            migrations=applied_migrations,
            record_counts=_read_record_counts(database_path),
            database=DatabaseBackupEntry(
                relative_path=DATABASE_RELATIVE_PATH,
                size_bytes=database_size,
                sha256_hex=database_digest,
            ),
            attachments=attachments,
        )
        manifest.validate()
        _write_manifest(staging_path, manifest)
        verify_local_backup(staging_path)
        os.replace(staging_path, final_path)
    except (
        LocalBackupError,
        LocalStorageError,
        BackupManifestError,
        OSError,
        sqlite3.Error,
    ) as error:
        _cleanup_private_staging(staging_path)
        if isinstance(error, LocalBackupError):
            raise
        if isinstance(error, BackupManifestError):
            raise BackupIntegrityError("备份清单验证失败。") from error
        raise LocalBackupError("无法安全创建本机备份集。") from error

    return BackupCreationResult(
        backup_set=final_path,
        backup_id=str(backup_uuid),
        attachment_count=len(attachments),
    )


def verify_local_backup(backup_set: Path) -> BackupManifest:
    """离线验证备份集格式、文件集合、摘要、SQLite 与附件元数据。

    本验证证明备份集内部一致且适用于当前应用版本，不证明它来自可信
    发布者。清单摘要用于发现传输损坏或普通误改，不是数字签名。
    """
    root = _require_existing_directory(backup_set, label="备份集")
    manifest = _read_and_verify_manifest(root)
    if manifest.application_version != _application_version():
        raise BackupIntegrityError("备份应用版本与当前程序版本不一致。")
    expected_migrations = _expected_current_migrations(require_fully_applied=False)
    if manifest.migrations != expected_migrations:
        raise BackupIntegrityError("备份迁移清单与当前程序不一致。")

    expected_files = {
        MANIFEST_FILENAME,
        MANIFEST_DIGEST_FILENAME,
        manifest.database.relative_path,
        *(backup_object_relative_path(entry.attachment_id) for entry in manifest.attachments),
    }
    actual_files = _list_regular_files(root)
    if actual_files != expected_files:
        raise BackupIntegrityError("备份集存在缺失、额外文件或不安全链接。")

    database_path = _resolve_member(root, manifest.database.relative_path)
    _verify_file_entry(
        database_path,
        expected_size=manifest.database.size_bytes,
        expected_digest=manifest.database.sha256_hex,
        label="SQLite 快照",
    )
    _validate_sqlite_integrity(database_path)
    if _read_applied_migrations(database_path) != manifest.migrations:
        raise BackupIntegrityError("SQLite 快照的迁移记录与备份清单不一致。")
    if _read_record_counts(database_path) != manifest.record_counts:
        raise BackupIntegrityError("SQLite 快照的记录计数与备份清单不一致。")
    _validate_initialized_installation(database_path)
    if _read_available_attachments(database_path) != manifest.attachments:
        raise BackupIntegrityError("SQLite 附件元数据与备份清单不一致。")
    for entry in manifest.attachments:
        _verify_file_entry(
            _resolve_member(root, backup_object_relative_path(entry.attachment_id)),
            expected_size=entry.size_bytes,
            expected_digest=entry.sha256_hex,
            label="附件对象",
        )
    return manifest


def restore_local_backup(*, backup_set: Path, target_data_dir: Path) -> RestoreResult:
    """把已验证备份集恢复到不存在或明确为空的新数据目录。

    当前运行数据目录、非空目录和备份集内部路径都被拒绝。恢复内容先写入
    目标父目录下的随机暂存目录，所有验证完成后再发布，因此失败不会留下
    一个看似可启动的半恢复目标。
    """
    current_data_dir, _attachment_root = _configured_local_paths()
    manifest = verify_local_backup(backup_set)
    backup_root = backup_set.resolve()
    target = target_data_dir.resolve()
    if target == current_data_dir or _is_within(target, current_data_dir):
        raise RestoreTargetError("恢复目标不能覆盖或位于当前 PMS 数据目录内。")
    if _is_within(target, backup_root) or _is_within(backup_root, target):
        raise RestoreTargetError("恢复目标与备份集不能互相包含。")
    if target == target.parent or not target.parent.is_dir():
        raise RestoreTargetError("恢复目标必须具有已存在的普通父目录。")
    if target.is_symlink():
        raise RestoreTargetError("恢复目标不能是符号链接。")
    target_was_empty = target.exists()
    if target_was_empty and (not target.is_dir() or any(target.iterdir())):
        raise RestoreTargetError("恢复目标必须不存在或是明确空目录。")

    # Windows 的传统路径长度限制会把暂存目录的每个字符都计入最终附件路径。
    # 暂存名无需承载业务语义，因此只保留短随机标识，降低合法深层 storage key
    # 在恢复期间超过路径上限的风险。
    staging = target.parent / f".r-{uuid.uuid7().hex[:8]}.p"
    if staging.exists():
        raise RestoreTargetError("恢复暂存目录发生不可接受的名称冲突。")
    try:
        staging.mkdir()
        source_database = _resolve_member(backup_root, manifest.database.relative_path)
        target_database = staging / "pms.sqlite3"
        _copy_file_exact(source_database, target_database)
        target_storage = LocalBinaryStorage(staging / "attachments")
        for entry in manifest.attachments:
            source_attachment = _resolve_member(
                backup_root,
                backup_object_relative_path(entry.attachment_id),
            )
            with source_attachment.open("rb") as content:
                stored = target_storage.store(
                    tenant_id=TenantId(uuid.UUID(entry.tenant_id)),
                    storage_key=entry.storage_key,
                    chunks=_read_chunks(content),
                    max_size_bytes=max(1, entry.size_bytes),
                )
            if stored.size_bytes != entry.size_bytes or stored.sha256_hex != entry.sha256_hex:
                raise BackupIntegrityError("恢复附件与备份清单不一致。")

        _validate_restored_data(staging, manifest)
        if target_was_empty:
            target.rmdir()
        os.replace(staging, target)
    except (
        LocalBackupError,
        LocalStorageError,
        BackupManifestError,
        OSError,
        sqlite3.Error,
    ) as error:
        _cleanup_private_staging(staging)
        if isinstance(error, LocalBackupError):
            raise
        if isinstance(error, BackupManifestError):
            raise BackupIntegrityError("恢复时备份清单验证失败。") from error
        raise LocalBackupError("无法安全恢复本机备份集。") from error

    return RestoreResult(
        target_data_dir=target,
        backup_id=manifest.backup_id,
        attachment_count=len(manifest.attachments),
    )


def _configured_local_paths() -> tuple[Path, Path]:
    """集中限制 local + SQLite，避免在内网 PostgreSQL 上误用本机命令。"""
    if getattr(settings, "DEPLOYMENT_PROFILE", None) != "local" or connection.vendor != "sqlite":
        raise LocalBackupConfigurationError("本命令只支持 local 档案的 SQLite 数据目录。")
    data_dir_setting = getattr(settings, "DATA_DIR", None)
    attachment_root_setting = getattr(settings, "ATTACHMENT_STORAGE_ROOT", None)
    if not isinstance(data_dir_setting, str | os.PathLike) or not isinstance(
        attachment_root_setting, str | os.PathLike
    ):
        raise LocalBackupConfigurationError("本机数据目录或附件目录配置缺失。")
    data_dir = Path(data_dir_setting).resolve()
    attachment_root = Path(attachment_root_setting).resolve()
    if not data_dir.is_dir() or not _is_within(attachment_root, data_dir):
        raise LocalBackupConfigurationError("本机数据目录或附件目录配置无效。")
    return data_dir, attachment_root


def _expected_current_migrations(*, require_fully_applied: bool) -> tuple[str, ...]:
    executor = MigrationExecutor(connection)
    if require_fully_applied and executor.migration_plan(executor.loader.graph.leaf_nodes()):
        raise BackupIntegrityError("数据库仍有未应用迁移，不能创建备份。")
    return tuple(sorted(f"{app_label}.{name}" for app_label, name in executor.loader.graph.nodes))


def _create_sqlite_snapshot(destination: Path) -> None:
    connection.ensure_connection()
    source = connection.connection
    if not isinstance(source, sqlite3.Connection):
        raise LocalBackupConfigurationError("当前数据库连接不是受支持的 SQLite 连接。")
    with closing(sqlite3.connect(destination)) as target:
        source.backup(target)
        target.commit()


def _read_available_attachments(database_path: Path) -> tuple[AttachmentBackupEntry, ...]:
    query = """
        SELECT id, tenant_id, storage_key, size_bytes, sha256_hex, status, storage_backend
        FROM attachments_attachment
        ORDER BY storage_key
    """
    entries: list[AttachmentBackupEntry] = []
    try:
        with _open_readonly_database(database_path) as database:
            rows = database.execute(query).fetchall()
    except sqlite3.Error as error:
        raise BackupIntegrityError("SQLite 快照缺少可读取的附件元数据。") from error
    for attachment_id, tenant_id, storage_key, size_bytes, digest, status, backend in rows:
        if backend != StorageBackend.LOCAL.value:
            raise BackupIntegrityError("本机备份中出现非本地附件后端。")
        if status != AttachmentStatus.AVAILABLE.value:
            continue
        if not isinstance(size_bytes, int) or not isinstance(digest, str):
            raise BackupIntegrityError("AVAILABLE 附件缺少大小或摘要。")
        entries.append(
            AttachmentBackupEntry(
                # SQLite 的 UUIDField 物理值是无连字符的 32 位十六进制，
                # PostgreSQL 返回的则通常是 UUID 对象。统一为标准 UUID 文本，
                # 让清单及对象名不随数据库适配器变化。
                attachment_id=str(uuid.UUID(str(attachment_id))),
                tenant_id=str(uuid.UUID(str(tenant_id))),
                storage_key=str(storage_key),
                size_bytes=size_bytes,
                sha256_hex=digest,
            )
        )
    result = tuple(entries)
    try:
        BackupManifest(
            format=BACKUP_FORMAT,
            format_version=BACKUP_FORMAT_VERSION,
            backup_id=str(uuid.uuid7()),
            created_at=datetime.now(tz=UTC).isoformat(),
            application_version=_application_version(),
            deployment_profile="local",
            migrations=(),
            record_counts={},
            database=DatabaseBackupEntry(DATABASE_RELATIVE_PATH, 0, "0" * 64),
            attachments=result,
        ).validate()
    except BackupManifestError as error:
        raise BackupIntegrityError("SQLite 附件元数据包含无效标识、路径或摘要。") from error
    return result


def _copy_snapshot_attachments(
    *,
    entries: tuple[AttachmentBackupEntry, ...],
    source_root: Path,
    backup_root: Path,
) -> None:
    expected_keys = {entry.storage_key for entry in entries}
    _assert_source_object_set(source_root, expected_keys)
    source_storage = LocalBinaryStorage(source_root)
    for entry in entries:
        destination = _resolve_member(
            backup_root,
            backup_object_relative_path(entry.attachment_id),
        )
        with source_storage.open(
            tenant_id=TenantId(uuid.UUID(entry.tenant_id)),
            storage_key=entry.storage_key,
        ) as content:
            actual_size, actual_digest = _copy_stream(content, destination)
        if actual_size != entry.size_bytes or actual_digest != entry.sha256_hex:
            raise BackupIntegrityError("附件在数据库快照与复制之间发生变化。")
    _assert_source_object_set(source_root, expected_keys)


def _assert_source_object_set(root: Path, expected_keys: set[str]) -> None:
    actual_keys: set[str] = set()
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if path.is_symlink():
            raise BackupIntegrityError("附件目录包含不允许的符号链接。")
        if relative.parts and relative.parts[0] == ".staging":
            continue
        if path.is_file():
            actual_keys.add(relative.as_posix())
    if actual_keys != expected_keys:
        raise BackupIntegrityError("附件目录与 AVAILABLE 元数据集合不一致。")


def _read_and_verify_manifest(root: Path) -> BackupManifest:
    manifest_path = root / MANIFEST_FILENAME
    digest_path = root / MANIFEST_DIGEST_FILENAME
    try:
        manifest_content = manifest_path.read_bytes()
        digest_line = digest_path.read_text(encoding="ascii").strip()
    except OSError as error:
        raise BackupIntegrityError("备份集缺少可读取的清单或清单摘要。") from error
    expected_digest = digest_line.partition(" ")[0]
    if SHA256_PATTERN.fullmatch(expected_digest) is None:
        raise BackupIntegrityError("备份清单摘要格式无效。")
    if hashlib.sha256(manifest_content).hexdigest() != expected_digest:
        raise BackupIntegrityError("备份清单摘要不匹配。")
    try:
        return BackupManifest.from_bytes(manifest_content)
    except BackupManifestError as error:
        raise BackupIntegrityError("备份清单内容无效。") from error


def _write_manifest(root: Path, manifest: BackupManifest) -> None:
    content = manifest.to_bytes()
    _write_bytes_fsynced(root / MANIFEST_FILENAME, content)
    digest = hashlib.sha256(content).hexdigest()
    _write_bytes_fsynced(
        root / MANIFEST_DIGEST_FILENAME,
        f"{digest}  {MANIFEST_FILENAME}\n".encode("ascii"),
    )


def _validate_restored_data(root: Path, manifest: BackupManifest) -> None:
    database_path = root / "pms.sqlite3"
    _verify_file_entry(
        database_path,
        expected_size=manifest.database.size_bytes,
        expected_digest=manifest.database.sha256_hex,
        label="恢复 SQLite",
    )
    _validate_sqlite_integrity(database_path)
    if _read_applied_migrations(database_path) != manifest.migrations:
        raise BackupIntegrityError("恢复数据库迁移记录不一致。")
    if _read_record_counts(database_path) != manifest.record_counts:
        raise BackupIntegrityError("恢复数据库记录计数不一致。")
    _validate_initialized_installation(database_path)
    if _read_available_attachments(database_path) != manifest.attachments:
        raise BackupIntegrityError("恢复数据库附件元数据不一致。")
    storage = LocalBinaryStorage(root / "attachments")
    _assert_source_object_set(
        root / "attachments", {entry.storage_key for entry in manifest.attachments}
    )
    for entry in manifest.attachments:
        integrity = storage.verify(
            tenant_id=TenantId(uuid.UUID(entry.tenant_id)),
            storage_key=entry.storage_key,
            expected_size_bytes=entry.size_bytes,
            expected_sha256_hex=entry.sha256_hex,
        )
        if not integrity.exists or not integrity.size_matches or not integrity.digest_matches:
            raise BackupIntegrityError("恢复附件完整性验证失败。")


def _validate_sqlite_integrity(database_path: Path) -> None:
    try:
        with _open_readonly_database(database_path) as database:
            result = database.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.Error as error:
        raise BackupIntegrityError("SQLite 完整性检查无法执行。") from error
    if result != ("ok",):
        raise BackupIntegrityError("SQLite 完整性检查未通过。")


def _validate_initialized_installation(database_path: Path) -> None:
    required_tables = (
        "identity_user",
        "tenancy_tenant",
        "tenancy_membership",
        "authorization_membership_role",
    )
    counts = _read_record_counts(database_path)
    if any(counts.get(table_name, 0) < 1 for table_name in required_tables):
        raise BackupIntegrityError("备份数据库尚未完成默认身份与租户初始化。")
    query = """
        SELECT COUNT(*)
        FROM authorization_membership_role AS assignment
        INNER JOIN authorization_role AS role ON role.code = assignment.role_id
        WHERE role.code = 'tenant_admin'
    """
    try:
        with _open_readonly_database(database_path) as database:
            admin_count = database.execute(query).fetchone()
    except sqlite3.Error as error:
        raise BackupIntegrityError("无法验证备份数据库的默认管理员角色。") from error
    if admin_count is None or admin_count[0] < 1:
        raise BackupIntegrityError("备份数据库缺少租户管理员角色。")


def _read_applied_migrations(database_path: Path) -> tuple[str, ...]:
    try:
        with _open_readonly_database(database_path) as database:
            rows = database.execute(
                "SELECT app, name FROM django_migrations ORDER BY app, name"
            ).fetchall()
    except sqlite3.Error as error:
        raise BackupIntegrityError("无法读取 SQLite 迁移记录。") from error
    return tuple(f"{app_label}.{name}" for app_label, name in rows)


def _read_record_counts(database_path: Path) -> dict[str, int]:
    try:
        with _open_readonly_database(database_path) as database:
            table_rows = database.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            ).fetchall()
            counts: dict[str, int] = {}
            for (table_name,) in table_rows:
                escaped_name = str(table_name).replace('"', '""')
                # 表名来自同一 SQLite schema 且已按标识符规则转义，不含外部输入。
                query = f'SELECT COUNT(*) FROM "{escaped_name}"'  # noqa: S608
                row = database.execute(query).fetchone()
                if row is None or not isinstance(row[0], int):
                    raise BackupIntegrityError("无法读取 SQLite 表记录计数。")
                counts[str(table_name)] = row[0]
    except sqlite3.Error as error:
        raise BackupIntegrityError("无法读取 SQLite 表清单或记录计数。") from error
    return counts


def _open_readonly_database(database_path: Path) -> closing[sqlite3.Connection]:
    uri = f"{database_path.resolve().as_uri()}?mode=ro"
    return closing(sqlite3.connect(uri, uri=True))


def _list_regular_files(root: Path) -> set[str]:
    files: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise BackupIntegrityError("备份集包含不允许的符号链接。")
        if path.is_file():
            files.add(path.relative_to(root).as_posix())
    return files


def _resolve_member(root: Path, relative_path: str) -> Path:
    safe_path = validate_safe_relative_path(relative_path, field="backup_path")
    candidate = root.joinpath(*safe_path.parts).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as error:
        raise BackupIntegrityError("备份成员路径逃逸根目录。") from error
    return candidate


def _copy_stream(source: BinaryIO, destination: Path) -> tuple[int, str]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    size_bytes = 0
    digest = hashlib.sha256()
    try:
        with destination.open("xb") as output:
            for chunk in _read_chunks(source):
                output.write(chunk)
                size_bytes += len(chunk)
                digest.update(chunk)
            output.flush()
            os.fsync(output.fileno())
    except OSError as error:
        raise LocalBackupError("无法写入备份成员。") from error
    return size_bytes, digest.hexdigest()


def _copy_file_exact(source: Path, destination: Path) -> None:
    with source.open("rb") as content:
        _copy_stream(content, destination)


def _read_chunks(source: BinaryIO) -> Iterator[bytes]:
    while chunk := source.read(COPY_CHUNK_SIZE_BYTES):
        yield chunk


def _hash_file(path: Path) -> tuple[int, str]:
    size_bytes = 0
    digest = hashlib.sha256()
    try:
        with path.open("rb") as content:
            for chunk in _read_chunks(content):
                size_bytes += len(chunk)
                digest.update(chunk)
    except OSError as error:
        raise BackupIntegrityError("备份成员无法读取。") from error
    return size_bytes, digest.hexdigest()


def _verify_file_entry(path: Path, *, expected_size: int, expected_digest: str, label: str) -> None:
    if not path.is_file() or path.is_symlink():
        raise BackupIntegrityError(f"{label} 缺失或不是普通文件。")
    actual_size, actual_digest = _hash_file(path)
    if actual_size != expected_size or actual_digest != expected_digest:
        raise BackupIntegrityError(f"{label} 大小或摘要不匹配。")


def _write_bytes_fsynced(path: Path, content: bytes) -> None:
    try:
        with path.open("xb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
    except OSError as error:
        raise LocalBackupError("无法写入备份清单。") from error


def _require_existing_directory(path: Path, *, label: str) -> Path:
    if path.is_symlink():
        raise LocalBackupError(f"{label}必须是已存在的普通目录。")
    resolved = path.resolve()
    if not resolved.is_dir():
        raise LocalBackupError(f"{label}必须是已存在的普通目录。")
    return resolved


def _is_within(candidate: Path, parent: Path) -> bool:
    try:
        candidate.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _application_version() -> str:
    try:
        return version("pms")
    except LookupError as error:
        raise LocalBackupConfigurationError("无法确定当前 PMS 应用版本。") from error


def _cleanup_private_staging(path: Path) -> None:
    """只删除本模块生成的精确暂存目录。"""
    is_backup_staging = path.name.startswith(".") and path.name.endswith(".pending")
    is_restore_staging = path.name.startswith(".r-") and path.name.endswith(".p")
    if path.exists() and (is_backup_staging or is_restore_staging):
        shutil.rmtree(path, ignore_errors=True)
