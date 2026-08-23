"""本机备份清单的稳定格式、序列化和不可信输入校验。

备份清单会被复制到其他磁盘，也可能在恢复前遭遇截断或人工修改，因此
读取端不能把 JSON 当作可信字典使用。本模块只描述格式和纯校验，不访问
Django、数据库或文件系统，便于独立测试格式兼容与路径边界。
"""

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any
from uuid import UUID

BACKUP_FORMAT = "pms-local-backup"
BACKUP_FORMAT_VERSION = 1
DATABASE_RELATIVE_PATH = "database/pms.sqlite3"
MANIFEST_FILENAME = "manifest.json"
MANIFEST_DIGEST_FILENAME = "manifest.sha256"
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


class BackupManifestError(ValueError):
    """表示备份清单结构、类型或安全边界无效。"""


@dataclass(frozen=True, slots=True)
class DatabaseBackupEntry:
    """备份集中 SQLite 快照的位置和完整性事实。"""

    relative_path: str
    size_bytes: int
    sha256_hex: str


@dataclass(frozen=True, slots=True)
class AttachmentBackupEntry:
    """数据库 AVAILABLE 元数据对应的单个附件对象。"""

    attachment_id: str
    tenant_id: str
    storage_key: str
    size_bytes: int
    sha256_hex: str


@dataclass(frozen=True, slots=True)
class BackupManifest:
    """恢复一个本机数据目录所需的无秘密版本化清单。"""

    format: str
    format_version: int
    backup_id: str
    created_at: str
    application_version: str
    deployment_profile: str
    migrations: tuple[str, ...]
    record_counts: dict[str, int]
    database: DatabaseBackupEntry
    attachments: tuple[AttachmentBackupEntry, ...]

    def to_bytes(self) -> bytes:
        """生成 UTF-8、稳定排序且以换行结束的可审查 JSON。"""
        payload = asdict(self)
        payload["migrations"] = list(self.migrations)
        payload["attachments"] = [asdict(entry) for entry in self.attachments]
        return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()

    @classmethod
    def from_bytes(cls, content: bytes) -> BackupManifest:
        """从不可信 JSON 构造强类型清单并执行完整边界校验。"""
        try:
            raw = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise BackupManifestError("备份清单不是有效 UTF-8 JSON。") from error
        root = _require_mapping(raw, field="manifest")
        _require_exact_keys(
            root,
            {
                "format",
                "format_version",
                "backup_id",
                "created_at",
                "application_version",
                "deployment_profile",
                "migrations",
                "record_counts",
                "database",
                "attachments",
            },
            field="manifest",
        )
        database = _parse_database(root["database"])
        attachments = tuple(_parse_attachment(item) for item in _require_list(root["attachments"]))
        manifest = cls(
            format=_require_string(root["format"], field="format"),
            format_version=_require_integer(root["format_version"], field="format_version"),
            backup_id=_require_string(root["backup_id"], field="backup_id"),
            created_at=_require_string(root["created_at"], field="created_at"),
            application_version=_require_string(
                root["application_version"], field="application_version"
            ),
            deployment_profile=_require_string(
                root["deployment_profile"], field="deployment_profile"
            ),
            migrations=tuple(
                _require_string(item, field="migrations[]")
                for item in _require_list(root["migrations"])
            ),
            record_counts=_parse_record_counts(root["record_counts"]),
            database=database,
            attachments=attachments,
        )
        manifest.validate()
        return manifest

    def validate(self) -> None:
        """拒绝未知版本、重复记录、无时区时间和可逃逸路径。"""
        if self.format != BACKUP_FORMAT or self.format_version != BACKUP_FORMAT_VERSION:
            raise BackupManifestError("备份格式或版本不受当前应用支持。")
        try:
            UUID(self.backup_id)
        except ValueError as error:
            raise BackupManifestError("备份标识不是有效 UUID。") from error
        try:
            created_at = datetime.fromisoformat(self.created_at.replace("Z", "+00:00"))
        except ValueError as error:
            raise BackupManifestError("备份时间格式无效。") from error
        if created_at.tzinfo is None:
            raise BackupManifestError("备份时间必须包含时区。")
        if not self.application_version or self.deployment_profile != "local":
            raise BackupManifestError("备份应用版本或部署档案无效。")
        if tuple(sorted(set(self.migrations))) != self.migrations:
            raise BackupManifestError("迁移清单必须有序且不能重复。")
        if self.database.relative_path != DATABASE_RELATIVE_PATH:
            raise BackupManifestError("SQLite 快照路径不是受支持的固定位置。")
        _validate_size_and_digest(
            size_bytes=self.database.size_bytes,
            sha256_hex=self.database.sha256_hex,
            field="database",
        )
        attachment_ids: set[str] = set()
        storage_keys: set[str] = set()
        for attachment in self.attachments:
            try:
                UUID(attachment.attachment_id)
                UUID(attachment.tenant_id)
            except ValueError as error:
                raise BackupManifestError("附件或租户标识不是有效 UUID。") from error
            validate_safe_relative_path(attachment.storage_key, field="storage_key")
            _validate_size_and_digest(
                size_bytes=attachment.size_bytes,
                sha256_hex=attachment.sha256_hex,
                field="attachment",
            )
            if attachment.attachment_id in attachment_ids:
                raise BackupManifestError("附件清单包含重复附件标识。")
            if attachment.storage_key in storage_keys:
                raise BackupManifestError("附件清单包含重复存储键。")
            attachment_ids.add(attachment.attachment_id)
            storage_keys.add(attachment.storage_key)
        if tuple(sorted(self.attachments, key=lambda entry: entry.storage_key)) != self.attachments:
            raise BackupManifestError("附件清单必须按存储键稳定排序。")


def validate_safe_relative_path(value: str, *, field: str) -> PurePosixPath:
    """只接受不能逃逸备份根目录的规范 POSIX 相对路径。"""
    if not value or "\\" in value or "\x00" in value:
        raise BackupManifestError(f"{field} 不是安全相对路径。")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise BackupManifestError(f"{field} 不是安全相对路径。")
    return path


def backup_object_relative_path(attachment_id: str) -> str:
    """用扁平附件 ID 定位备份对象，避免 Windows 深路径超限。"""
    try:
        UUID(attachment_id)
    except ValueError as error:
        raise BackupManifestError("附件标识不是有效 UUID。") from error
    return f"objects/{attachment_id}"


def _parse_database(raw: object) -> DatabaseBackupEntry:
    mapping = _require_mapping(raw, field="database")
    _require_exact_keys(mapping, {"relative_path", "size_bytes", "sha256_hex"}, field="database")
    return DatabaseBackupEntry(
        relative_path=_require_string(mapping["relative_path"], field="database.relative_path"),
        size_bytes=_require_integer(mapping["size_bytes"], field="database.size_bytes"),
        sha256_hex=_require_string(mapping["sha256_hex"], field="database.sha256_hex"),
    )


def _parse_attachment(raw: object) -> AttachmentBackupEntry:
    mapping = _require_mapping(raw, field="attachment")
    _require_exact_keys(
        mapping,
        {"attachment_id", "tenant_id", "storage_key", "size_bytes", "sha256_hex"},
        field="attachment",
    )
    return AttachmentBackupEntry(
        attachment_id=_require_string(mapping["attachment_id"], field="attachment_id"),
        tenant_id=_require_string(mapping["tenant_id"], field="tenant_id"),
        storage_key=_require_string(mapping["storage_key"], field="storage_key"),
        size_bytes=_require_integer(mapping["size_bytes"], field="size_bytes"),
        sha256_hex=_require_string(mapping["sha256_hex"], field="sha256_hex"),
    )


def _parse_record_counts(raw: object) -> dict[str, int]:
    mapping = _require_mapping(raw, field="record_counts")
    result: dict[str, int] = {}
    for table_name, count in mapping.items():
        if not table_name or "\x00" in table_name:
            raise BackupManifestError("记录计数包含无效表名。")
        result[table_name] = _require_integer(count, field=f"record_counts.{table_name}")
        if result[table_name] < 0:
            raise BackupManifestError("记录计数不能为负数。")
    return dict(sorted(result.items()))


def _validate_size_and_digest(*, size_bytes: int, sha256_hex: str, field: str) -> None:
    if size_bytes < 0 or SHA256_PATTERN.fullmatch(sha256_hex) is None:
        raise BackupManifestError(f"{field} 的大小或 SHA-256 无效。")


def _require_mapping(raw: object, *, field: str) -> dict[str, Any]:
    if not isinstance(raw, dict) or not all(isinstance(key, str) for key in raw):
        raise BackupManifestError(f"{field} 必须是对象。")
    return raw


def _require_list(raw: object) -> list[object]:
    if not isinstance(raw, list):
        raise BackupManifestError("清单字段必须是数组。")
    return raw


def _require_string(raw: object, *, field: str) -> str:
    if not isinstance(raw, str) or not raw:
        raise BackupManifestError(f"{field} 必须是非空字符串。")
    return raw


def _require_integer(raw: object, *, field: str) -> int:
    if not isinstance(raw, int) or isinstance(raw, bool):
        raise BackupManifestError(f"{field} 必须是整数。")
    return raw


def _require_exact_keys(mapping: dict[str, Any], expected: set[str], *, field: str) -> None:
    if set(mapping) != expected:
        raise BackupManifestError(f"{field} 字段集合与当前格式不一致。")
