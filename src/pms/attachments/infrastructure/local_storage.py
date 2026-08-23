"""应用私有目录中的租户隔离原子文件存储适配器。"""

import hashlib
import logging
import os
import tempfile
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import BinaryIO
from uuid import UUID

from pms.attachments.application.ports import IntegrityResult, StoredObject
from pms.tenancy.domain.context import TenantId

READ_CHUNK_SIZE_BYTES = 1024 * 1024
logger = logging.getLogger("pms.attachments.storage")


class LocalStorageError(RuntimeError):
    """不回显服务器路径的本地存储失败基类。"""


class InvalidStorageKeyError(LocalStorageError):
    """表示存储键不是当前租户下的安全相对键。"""


class StorageObjectExistsError(LocalStorageError):
    """表示随机存储键发生冲突；适配器拒绝静默覆盖。"""


class AttachmentTooLargeError(LocalStorageError):
    """表示流式写入已经超过 byte 上限。"""


class InvalidContentChunkError(LocalStorageError):
    """表示调用方提供的分块不是 bytes。"""


class LocalBinaryStorage:
    """在同一文件系统中临时写入、校验并原子移动附件。

    正式路径只接受服务端生成的 POSIX 风格 storage key，并再次校验 tenant
    前缀和解析后的根目录边界。原始文件名从不进入这里。临时区位于同一根目录，
    使 ``os.replace`` 在支持的本地文件系统上保持原子重命名语义。
    """

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.staging_root = self.root / ".staging"
        try:
            self.staging_root.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise LocalStorageError("无法初始化附件存储。") from error

    def store(
        self,
        *,
        tenant_id: TenantId,
        storage_key: str,
        chunks: Iterable[bytes],
        max_size_bytes: int,
    ) -> StoredObject:
        """流式计算大小和 SHA-256，完成后才把对象原子移动到正式区。"""
        if max_size_bytes <= 0:
            raise ValueError("附件大小上限必须大于 0 byte。")
        final_path = self._resolve_key(tenant_id=tenant_id, storage_key=storage_key)
        if final_path.exists():
            raise StorageObjectExistsError("附件存储键已经存在。")
        try:
            final_path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix="attachment-",
                suffix=".pending",
                dir=self.staging_root,
            )
        except OSError as error:
            raise LocalStorageError("无法准备附件临时存储。") from error

        temporary_path = Path(temporary_name)
        size_bytes = 0
        digest = hashlib.sha256()
        try:
            with os.fdopen(descriptor, "wb") as output:
                for chunk in chunks:
                    if not isinstance(chunk, bytes):
                        raise InvalidContentChunkError("附件分块必须是 bytes。")
                    size_bytes += len(chunk)
                    if size_bytes > max_size_bytes:
                        raise AttachmentTooLargeError("附件超过允许的大小上限。")
                    output.write(chunk)
                    digest.update(chunk)
                output.flush()
                os.fsync(output.fileno())
            # storage key 由两个 UUIDv7 组成，冲突概率极低；移动前仍二次检查，
            # 防止同一服务进程中的重复调用静默覆盖已有业务证据。
            if final_path.exists():
                raise StorageObjectExistsError("附件存储键已经存在。")
            os.replace(temporary_path, final_path)
        except AttachmentTooLargeError, InvalidContentChunkError, StorageObjectExistsError:
            raise
        except OSError as error:
            raise LocalStorageError("无法保存附件内容。") from error
        finally:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError as cleanup_error:
                # 临时清理失败不会把半成品变成可下载文件；.staging 可由后续
                # 运维清理和对账处理，且异常不能覆盖更重要的写入根因。
                logger.warning(
                    "attachment_staging_cleanup_failed",
                    extra={
                        "event": "attachment_staging_cleanup_failed",
                        "tenant_id": tenant_id,
                        "result": "failure",
                    },
                    exc_info=(
                        type(cleanup_error),
                        cleanup_error,
                        cleanup_error.__traceback__,
                    ),
                )

        return StoredObject(
            storage_key=storage_key,
            size_bytes=size_bytes,
            sha256_hex=digest.hexdigest(),
        )

    def open(self, *, tenant_id: TenantId, storage_key: str) -> BinaryIO:
        """打开正式对象，不把解析后的绝对路径放入异常消息。"""
        path = self._resolve_key(tenant_id=tenant_id, storage_key=storage_key)
        try:
            return path.open("rb")
        except OSError as error:
            raise LocalStorageError("附件内容不可读取。") from error

    def delete(self, *, tenant_id: TenantId, storage_key: str) -> bool:
        """幂等删除正式对象，供失败补偿和未来保留策略调用。"""
        path = self._resolve_key(tenant_id=tenant_id, storage_key=storage_key)
        try:
            if not path.exists():
                return False
            path.unlink()
            return True
        except OSError as error:
            raise LocalStorageError("附件内容无法删除。") from error

    def exists(self, *, tenant_id: TenantId, storage_key: str) -> bool:
        """检查安全解析后的正式对象是否是普通文件。"""
        path = self._resolve_key(tenant_id=tenant_id, storage_key=storage_key)
        return path.is_file()

    def verify(
        self,
        *,
        tenant_id: TenantId,
        storage_key: str,
        expected_size_bytes: int,
        expected_sha256_hex: str,
    ) -> IntegrityResult:
        """流式重算正式对象摘要；任何路径都不会出现在返回结果。"""
        path = self._resolve_key(tenant_id=tenant_id, storage_key=storage_key)
        if not path.is_file():
            return IntegrityResult(False, False, False)
        size_bytes = 0
        digest = hashlib.sha256()
        try:
            with path.open("rb") as content:
                while chunk := content.read(READ_CHUNK_SIZE_BYTES):
                    size_bytes += len(chunk)
                    digest.update(chunk)
        except OSError as error:
            raise LocalStorageError("附件完整性无法核验。") from error
        return IntegrityResult(
            exists=True,
            size_matches=size_bytes == expected_size_bytes,
            digest_matches=digest.hexdigest() == expected_sha256_hex,
        )

    def _resolve_key(self, *, tenant_id: TenantId, storage_key: str) -> Path:
        """验证 tenant 前缀、相对路径和最终根目录边界。"""
        if "\\" in storage_key or "\x00" in storage_key:
            raise InvalidStorageKeyError("附件存储键无效。")
        key = PurePosixPath(storage_key)
        expected_prefix = ("tenants", str(tenant_id))
        if (
            key.is_absolute()
            or key.parts[:2] != expected_prefix
            or len(key.parts) != 6
            or any(part in {"", ".", ".."} for part in key.parts)
        ):
            raise InvalidStorageKeyError("附件存储键无效。")
        year, month, attachment_id, object_id = key.parts[2:]
        try:
            valid_date_path = len(year) == 4 and year.isdigit() and 1 <= int(month) <= 12
            UUID(attachment_id)
            UUID(object_id)
        except (ValueError, AttributeError) as error:
            raise InvalidStorageKeyError("附件存储键无效。") from error
        if not valid_date_path or len(month) != 2:
            raise InvalidStorageKeyError("附件存储键无效。")
        candidate = self.root.joinpath(*key.parts).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as error:
            raise InvalidStorageKeyError("附件存储键无效。") from error
        return candidate
