"""附件应用层依赖的元数据仓储和二进制存储端口。"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import BinaryIO, Protocol

from pms.attachments.domain.attachments import AttachmentId, AttachmentRecord
from pms.tenancy.domain.context import TenantId


@dataclass(frozen=True, slots=True)
class StoredObject:
    """成功落盘后的内容事实；大小单位为 byte，摘要为小写 SHA-256。"""

    storage_key: str
    size_bytes: int
    sha256_hex: str


@dataclass(frozen=True, slots=True)
class IntegrityResult:
    """存储对象与数据库期望值的逐项核验结果。"""

    exists: bool
    size_matches: bool
    digest_matches: bool


class BinaryStorage(Protocol):
    """应用层可替换的二进制存储；实现不能暴露服务器绝对路径。"""

    def store(
        self,
        *,
        tenant_id: TenantId,
        storage_key: str,
        chunks: Iterable[bytes],
        max_size_bytes: int,
    ) -> StoredObject:
        """以临时文件和原子移动保存全新对象，不得覆盖既有键。"""

    def open(self, *, tenant_id: TenantId, storage_key: str) -> BinaryIO:
        """打开当前租户对象；调用方负责关闭返回的二进制流。"""

    def delete(self, *, tenant_id: TenantId, storage_key: str) -> bool:
        """幂等删除对象；对象不存在时返回 False。"""

    def exists(self, *, tenant_id: TenantId, storage_key: str) -> bool:
        """查询当前租户对象是否存在。"""

    def verify(
        self,
        *,
        tenant_id: TenantId,
        storage_key: str,
        expected_size_bytes: int,
        expected_sha256_hex: str,
    ) -> IntegrityResult:
        """重新读取对象并核对大小和摘要。"""


class AttachmentRepository(Protocol):
    """附件元数据仓储；所有读取和更新都必须显式携带 tenant。"""

    def create_pending(self, record: AttachmentRecord) -> AttachmentRecord:
        """提交一条 PENDING 元数据，使后续故障可以对账。"""

    def mark_available(
        self,
        *,
        tenant_id: TenantId,
        attachment_id: AttachmentId,
        stored_object: StoredObject,
    ) -> AttachmentRecord:
        """仅允许把当前租户 PENDING 记录原子转为 AVAILABLE。"""

    def mark_failed(
        self,
        *,
        tenant_id: TenantId,
        attachment_id: AttachmentId,
        failure_code: str,
    ) -> None:
        """把仍处于 PENDING 的记录标记为 FAILED。"""

    def get_available(
        self, *, tenant_id: TenantId, attachment_id: AttachmentId
    ) -> AttachmentRecord | None:
        """只返回当前租户可下载记录，其他租户和其他状态统一为不存在。"""

    def list_for_reconciliation(self, *, tenant_id: TenantId) -> list[AttachmentRecord]:
        """按稳定顺序列出当前租户全部附件供对账。"""
