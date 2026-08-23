"""与 Django、数据库和文件系统无关的附件元数据规则。"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import NewType
from uuid import UUID

from pms.tenancy.domain.context import TenantId, UserId

AttachmentId = NewType("AttachmentId", UUID)


class AttachmentStatus(StrEnum):
    """数据库和二进制文件之间故障窗口的显式状态。"""

    PENDING = "pending"
    AVAILABLE = "available"
    QUARANTINED = "quarantined"
    FAILED = "failed"
    DELETED = "deleted"


class StorageBackend(StrEnum):
    """稳定存储后端代码；供应商名称不得进入领域规则。"""

    LOCAL = "local"


class InvalidAttachmentFilenameError(ValueError):
    """表示原文件名不适合安全保存为元数据或响应显示名。"""


def normalize_original_filename(value: str) -> str:
    """验证并规范仅作元数据使用的原文件名。

    即使原文件名永远不会参与磁盘路径，也拒绝目录分隔符、控制字符和
    ``.``/``..``，避免它以后进入下载头、审计摘要或导出清单时造成注入。
    """
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > 255
        or normalized in {".", ".."}
        or "/" in normalized
        or "\\" in normalized
        or any(ord(character) < 32 or ord(character) == 127 for character in normalized)
    ):
        raise InvalidAttachmentFilenameError("附件文件名无效。")
    return normalized


def detected_extension(filename: str) -> str:
    """从已验证文件名提取小写显示扩展名，不据此判断真实内容类型。"""
    return PurePosixPath(filename).suffix.lower()[:16]


def build_storage_key(
    *,
    tenant_id: TenantId,
    attachment_id: AttachmentId,
    object_id: UUID,
    created_at: datetime,
) -> str:
    """生成不含业务名称和用户文件名的租户隔离随机存储键。"""
    if created_at.utcoffset() is None:
        raise ValueError("存储键时间必须带时区。")
    return f"tenants/{tenant_id}/{created_at:%Y}/{created_at:%m}/{attachment_id}/{object_id}"


@dataclass(frozen=True, slots=True)
class AttachmentRecord:
    """应用层使用的附件元数据快照，不暴露 ORM 实体或本机路径。"""

    id: AttachmentId
    tenant_id: TenantId
    created_by_id: UserId
    original_filename: str
    display_filename: str
    detected_media_type: str
    detected_extension: str
    size_bytes: int | None
    sha256_hex: str | None
    storage_key: str
    storage_backend: StorageBackend
    storage_version: int
    status: AttachmentStatus
    source: str
    failure_code: str
