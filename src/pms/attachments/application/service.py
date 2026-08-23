"""编排附件元数据、二进制存储和故障补偿的应用服务。"""

import logging
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import BinaryIO

from pms.attachments.application.ports import AttachmentRepository, BinaryStorage
from pms.attachments.domain.attachments import (
    AttachmentId,
    AttachmentRecord,
    AttachmentStatus,
    StorageBackend,
    build_storage_key,
    detected_extension,
    normalize_original_filename,
)
from pms.tenancy.domain.context import TenantContext, TenantId

DEFAULT_MAX_ATTACHMENT_SIZE_BYTES = 25 * 1024 * 1024
logger = logging.getLogger("pms.attachments")


class AttachmentError(RuntimeError):
    """附件应用服务可预期失败的基类。"""


class AttachmentNotFoundError(AttachmentError):
    """附件不属于当前租户、状态不可下载或确实不存在。"""


class AttachmentFinalizationError(AttachmentError):
    """二进制已处理但元数据无法安全完成，补偿已经尝试。"""


@dataclass(frozen=True, slots=True)
class UploadAttachmentCommand:
    """可信调用方提交的附件存储命令。

    ``detected_media_type`` 必须来自后续上传边界的实际内容检测，不能直接
    使用浏览器 Content-Type。``chunks`` 是一次性二进制流，不应在日志或
    审计中序列化。大小上限以 byte 表示，默认对应 BOM 的 25 MiB 决策。
    """

    context: TenantContext
    original_filename: str
    detected_media_type: str
    source: str
    chunks: Iterable[bytes]
    max_size_bytes: int = DEFAULT_MAX_ATTACHMENT_SIZE_BYTES


@dataclass(frozen=True, slots=True)
class ConsistencyIssue:
    """数据库元数据与二进制存储之间可安全展示的对账问题。"""

    attachment_id: AttachmentId
    code: str


class AttachmentService:
    """提供上传、租户级读取和存储一致性对账。

    上传先提交 PENDING 元数据，再执行本地原子落盘，最后转为 AVAILABLE。
    文件系统与数据库无法共享事务，因此任一步失败都会尽力删除正式对象并
    标记 FAILED；进程在故障窗口退出时，由 reconciliation 报告残留状态。
    """

    def __init__(
        self,
        *,
        repository: AttachmentRepository,
        storage: BinaryStorage,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], uuid.UUID] | None = None,
    ) -> None:
        self.repository = repository
        self.storage = storage
        self.clock = clock or (lambda: datetime.now(tz=UTC))
        self.id_factory = id_factory or uuid.uuid7

    def upload(self, command: UploadAttachmentCommand) -> AttachmentRecord:
        """保存一个附件，成功返回 AVAILABLE 元数据。

        本函数不解析 BOM、不建立业务对象关联，也不执行下载授权。调用方应在
        进入本服务前完成类型检测和业务权限检查；业务关联由拥有对象的模块写入。
        """
        original_filename = normalize_original_filename(command.original_filename)
        if command.max_size_bytes <= 0:
            raise ValueError("附件大小上限必须大于 0 byte。")
        attachment_id = AttachmentId(self.id_factory())
        storage_key = build_storage_key(
            tenant_id=command.context.tenant_id,
            attachment_id=attachment_id,
            object_id=self.id_factory(),
            created_at=self.clock(),
        )
        pending = AttachmentRecord(
            id=attachment_id,
            tenant_id=command.context.tenant_id,
            created_by_id=command.context.user_id,
            original_filename=original_filename,
            display_filename=original_filename,
            detected_media_type=command.detected_media_type,
            detected_extension=detected_extension(original_filename),
            size_bytes=None,
            sha256_hex=None,
            storage_key=storage_key,
            storage_backend=StorageBackend.LOCAL,
            storage_version=1,
            status=AttachmentStatus.PENDING,
            source=command.source,
            failure_code="",
        )
        self.repository.create_pending(pending)

        try:
            stored_object = self.storage.store(
                tenant_id=command.context.tenant_id,
                storage_key=storage_key,
                chunks=command.chunks,
                max_size_bytes=command.max_size_bytes,
            )
        except Exception:
            # 存储端口在超时等情况下可能无法确定正式对象是否已经产生；幂等
            # delete 让本地和未来对象存储适配器都能执行同一补偿协议。
            self._delete_without_masking(
                tenant_id=command.context.tenant_id,
                storage_key=storage_key,
                attachment_id=attachment_id,
            )
            self._mark_failed_without_masking(
                tenant_id=command.context.tenant_id,
                attachment_id=attachment_id,
                failure_code="storage_write_failed",
            )
            raise

        try:
            return self.repository.mark_available(
                tenant_id=command.context.tenant_id,
                attachment_id=attachment_id,
                stored_object=stored_object,
            )
        except Exception as error:
            # 已移动到正式区但数据库最终状态失败时，优先删除二进制。即使删除也
            # 失败，对账仍会把 PENDING+存在的对象报告为 unexpected_object。
            self._delete_without_masking(
                tenant_id=command.context.tenant_id,
                storage_key=stored_object.storage_key,
                attachment_id=attachment_id,
            )
            self._mark_failed_without_masking(
                tenant_id=command.context.tenant_id,
                attachment_id=attachment_id,
                failure_code="metadata_finalize_failed",
            )
            raise AttachmentFinalizationError("附件最终状态保存失败。") from error

    def open_available(self, *, context: TenantContext, attachment_id: AttachmentId) -> BinaryIO:
        """打开当前租户 AVAILABLE 附件；其他租户和半成品统一为不存在。"""
        record = self.repository.get_available(
            tenant_id=context.tenant_id,
            attachment_id=attachment_id,
        )
        if record is None:
            raise AttachmentNotFoundError("附件不存在或不可用。")
        return self.storage.open(
            tenant_id=context.tenant_id,
            storage_key=record.storage_key,
        )

    def reconcile(self, *, tenant_id: TenantId) -> list[ConsistencyIssue]:
        """核对一个租户的元数据与二进制，不修改记录或自动删除证据。"""
        issues: list[ConsistencyIssue] = []
        for record in self.repository.list_for_reconciliation(tenant_id=tenant_id):
            exists = self.storage.exists(
                tenant_id=tenant_id,
                storage_key=record.storage_key,
            )
            if record.status is not AttachmentStatus.AVAILABLE:
                if exists:
                    issues.append(ConsistencyIssue(record.id, "unexpected_object"))
                continue
            if not exists or record.size_bytes is None or record.sha256_hex is None:
                issues.append(ConsistencyIssue(record.id, "missing_object"))
                continue
            integrity = self.storage.verify(
                tenant_id=tenant_id,
                storage_key=record.storage_key,
                expected_size_bytes=record.size_bytes,
                expected_sha256_hex=record.sha256_hex,
            )
            if not integrity.exists:
                # 对象可能在 exists 与 verify 之间被运维操作移除；把这个并发
                # 窗口仍归类为缺失，而不是误报两个完整性不匹配。
                issues.append(ConsistencyIssue(record.id, "missing_object"))
                continue
            if not integrity.size_matches:
                issues.append(ConsistencyIssue(record.id, "size_mismatch"))
            if not integrity.digest_matches:
                issues.append(ConsistencyIssue(record.id, "digest_mismatch"))
        return issues

    def _delete_without_masking(
        self,
        *,
        tenant_id: TenantId,
        storage_key: str,
        attachment_id: AttachmentId,
    ) -> None:
        """幂等补偿正式对象，同时保留触发补偿的原始异常。"""
        try:
            self.storage.delete(tenant_id=tenant_id, storage_key=storage_key)
        except Exception as cleanup_error:
            logger.warning(
                "attachment_compensation_delete_failed",
                extra={
                    "event": "attachment_compensation_delete_failed",
                    "tenant_id": tenant_id,
                    "entity_type": "attachment",
                    "entity_id": attachment_id,
                    "result": "failure",
                },
                exc_info=(
                    type(cleanup_error),
                    cleanup_error,
                    cleanup_error.__traceback__,
                ),
            )

    def _mark_failed_without_masking(
        self,
        *,
        tenant_id: TenantId,
        attachment_id: AttachmentId,
        failure_code: str,
    ) -> None:
        """尽力保留失败状态，同时让调用方继续收到最初的根因。"""
        try:
            self.repository.mark_failed(
                tenant_id=tenant_id,
                attachment_id=attachment_id,
                failure_code=failure_code,
            )
        except Exception as state_error:
            # 数据库不可用时无法持久化 FAILED；PENDING 记录和对账流程就是
            # 这个极端故障窗口的恢复入口，不能用第二个异常覆盖原始失败。
            logger.warning(
                "attachment_failure_state_not_recorded",
                extra={
                    "event": "attachment_failure_state_not_recorded",
                    "tenant_id": tenant_id,
                    "entity_type": "attachment",
                    "entity_id": attachment_id,
                    "result": "failure",
                },
                exc_info=(type(state_error), state_error, state_error.__traceback__),
            )
