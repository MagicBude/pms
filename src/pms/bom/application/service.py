"""BOM 导入、修正、发布和差异用例。

工作簿解析器与附件服务通过端口协作。应用层从可信上下文执行授权，
再在数据库事务中建立草稿、行和审计；来源二进制永远不会作为审计内容。
"""

from collections import Counter
from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from pms.attachments.application.service import AttachmentService, UploadAttachmentCommand
from pms.attachments.domain.attachments import AttachmentId
from pms.audit.application.recorder import AuditRecorder
from pms.audit.domain.events import AuditEvent, AuditResult
from pms.authorization.application.authorize import PermissionGrantLookup, authorize
from pms.authorization.domain.permissions import PermissionCode
from pms.bom.domain.lifecycle import BomStatus, cancel_bom, ensure_draft_editable, publish_bom
from pms.bom.domain.validation import (
    ERROR_MESSAGES,
    BomLineErrorCode,
    BomLineIssue,
    parse_positive_quantity,
)
from pms.projects.domain.lifecycle import ProjectStatus
from pms.tenancy.domain.context import TenantContext

MAX_BOM_SIZE_BYTES = 25 * 1024 * 1024


class BomImportError(ValueError):
    """表示文件、映射、版本或关联对象不能安全导入。"""


class BomNotFoundError(LookupError):
    """表示当前租户看不到目标 BOM 或行。"""


@dataclass(frozen=True, slots=True)
class ParsedSpreadsheetRow:
    """解析器输出的来源行；所有单元格先转为受控文本。"""

    source_row_number: int
    values: Mapping[str, str]
    formula_fields: tuple[str, ...] = ()


class BomSpreadsheetParser(Protocol):
    """只读解析 `.xlsx`/`.xlsm` 的应用端口。"""

    def parse(
        self, *, filename: str, content: bytes, mapping: Mapping[str, str]
    ) -> list[ParsedSpreadsheetRow]: ...


@dataclass(frozen=True, slots=True)
class ProjectAccess:
    """BOM 用例需要的只读项目事实。"""

    id: UUID
    tenant_id: UUID
    status: ProjectStatus
    is_related: bool


@dataclass(frozen=True, slots=True)
class MasterReference:
    """BOM 行匹配到的同租户主数据最小快照。"""

    id: UUID
    code: str
    unit_id: UUID | None = None
    procurement_required: bool = True


@dataclass(frozen=True, slots=True)
class DraftBomLine:
    """准备持久化的 BOM 草稿行及其可修正错误。"""

    source_row_number: int
    level_path: str
    assembly_code: str
    assembly_name: str
    material_id: UUID | None
    material_code: str
    material_name: str
    specification: str
    brand: str
    quantity_per_unit: Decimal | None
    unit_id: UUID | None
    unit_text: str
    procurement_required: bool
    remark: str
    validation_errors: tuple[str, ...]
    duplicate_key: str


@dataclass(frozen=True, slots=True)
class BomSnapshot:
    """页面和下游模块使用的 BOM 版本快照。"""

    id: UUID
    project_id: UUID
    version_number: int
    status: BomStatus
    line_count: int
    error_count: int


@dataclass(frozen=True, slots=True)
class BomDiff:
    """两个版本按物料代码/待匹配键比较的数量差异摘要。"""

    added: tuple[str, ...]
    removed: tuple[str, ...]
    changed: tuple[str, ...]


class BomTransactionManager(Protocol):
    """BOM 写入和发布的原子事务端口。"""

    def atomic(self) -> AbstractContextManager[None]: ...


class BomDownstreamLookup(Protocol):
    """查询 BOM 是否已经形成不能被取消隐藏的投产引用。"""

    def has_active_production(self, *, tenant_id: UUID, bom_id: UUID) -> bool: ...


class BomRepository(Protocol):
    """BOM 模块拥有的数据访问端口。"""

    def get_project_access(
        self, *, tenant_id: UUID, project_id: UUID, membership_id: UUID
    ) -> ProjectAccess | None: ...

    def find_units(self, *, tenant_id: UUID, keys: set[str]) -> dict[str, MasterReference]: ...

    def find_materials(self, *, tenant_id: UUID, codes: set[str]) -> dict[str, MasterReference]: ...

    def create_draft(
        self,
        *,
        tenant_id: UUID,
        project_id: UUID,
        version_number: int,
        source_attachment_id: AttachmentId,
        mapping: Mapping[str, str],
        created_by_membership_id: UUID,
        lines: list[DraftBomLine],
    ) -> BomSnapshot: ...

    def get_for_update(self, *, tenant_id: UUID, bom_id: UUID) -> BomSnapshot | None: ...

    def assign_line_material(
        self,
        *,
        tenant_id: UUID,
        bom_id: UUID,
        line_id: UUID,
        material_id: UUID,
    ) -> BomSnapshot: ...

    def confirm_duplicate(self, *, tenant_id: UUID, bom_id: UUID, line_id: UUID) -> BomSnapshot: ...

    def publish(self, *, tenant_id: UUID, bom_id: UUID, membership_id: UUID) -> BomSnapshot: ...

    def cancel(
        self,
        *,
        tenant_id: UUID,
        bom_id: UUID,
        membership_id: UUID,
        reason: str,
    ) -> BomSnapshot: ...

    def compare(self, *, tenant_id: UUID, left_id: UUID, right_id: UUID) -> BomDiff: ...


@dataclass(frozen=True, slots=True)
class ImportBomCommand:
    """导入 BOM 的受控输入；tenant、状态和附件 ID 均由服务端决定。"""

    project_id: UUID
    version_number: int
    filename: str
    content: bytes
    mapping: Mapping[str, str]


class BomService:
    """执行 BOM 安全导入、人工修正、发布与差异查询。"""

    def __init__(
        self,
        *,
        repository: BomRepository,
        parser: BomSpreadsheetParser,
        attachments: AttachmentService,
        grants: PermissionGrantLookup,
        audit: AuditRecorder,
        transactions: BomTransactionManager,
        downstream: BomDownstreamLookup,
    ) -> None:
        self._repository = repository
        self._parser = parser
        self._attachments = attachments
        self._grants = grants
        self._audit = audit
        self._transactions = transactions
        self._downstream = downstream

    def import_bom(self, *, context: TenantContext, command: ImportBomCommand) -> BomSnapshot:
        """上传原文件并建立保留来源行错误的 BOM 草稿。

        扩展名、ZIP 结构和字段映射由只读解析器验证。即使业务行有错误，
        文件仍会留存并形成草稿，使用者可根据来源行号修正；无法识别为
        安全工作簿的输入不会进入附件存储。
        """
        project = self._require_project(
            context=context,
            project_id=command.project_id,
            permission=PermissionCode.BOM_IMPORT,
        )
        if project.status is not ProjectStatus.ACTIVE:
            raise BomImportError("只有活动项目可以导入 BOM。")
        if command.version_number <= 0:
            raise BomImportError("BOM 版本号必须是大于零的整数。")
        parsed_rows = self._parser.parse(
            filename=command.filename,
            content=command.content,
            mapping=command.mapping,
        )
        lines = self._validate_rows(context=context, rows=parsed_rows)
        media_type = (
            "application/vnd.ms-excel.sheet.macroenabled.12"
            if command.filename.lower().endswith(".xlsm")
            else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        attachment = self._attachments.upload(
            UploadAttachmentCommand(
                context=context,
                original_filename=command.filename,
                detected_media_type=media_type,
                source="bom_import",
                chunks=(command.content,),
                max_size_bytes=MAX_BOM_SIZE_BYTES,
            )
        )
        with self._transactions.atomic():
            bom = self._repository.create_draft(
                tenant_id=context.tenant_id,
                project_id=project.id,
                version_number=command.version_number,
                source_attachment_id=attachment.id,
                mapping=command.mapping,
                created_by_membership_id=context.membership_id,
                lines=lines,
            )
            self._record(
                context=context,
                action="bom.imported",
                bom=bom,
                summary={"line_count": bom.line_count, "error_count": bom.error_count},
            )
        return bom

    def assign_line_material(
        self,
        *,
        context: TenantContext,
        bom_id: UUID,
        line_id: UUID,
        material_id: UUID,
    ) -> BomSnapshot:
        """为无编码或未匹配草稿行人工确认同租户物料。"""
        with self._transactions.atomic():
            bom = self._require_editable_bom(
                context=context, bom_id=bom_id, permission=PermissionCode.BOM_EDIT
            )
            updated = self._repository.assign_line_material(
                tenant_id=context.tenant_id,
                bom_id=bom.id,
                line_id=line_id,
                material_id=material_id,
            )
            self._record(context=context, action="bom.line_material_assigned", bom=updated)
        return updated

    def confirm_duplicate(
        self, *, context: TenantContext, bom_id: UUID, line_id: UUID
    ) -> BomSnapshot:
        """确认保留一条疑似重复行，不自动合并数量。"""
        with self._transactions.atomic():
            bom = self._require_editable_bom(
                context=context, bom_id=bom_id, permission=PermissionCode.BOM_EDIT
            )
            updated = self._repository.confirm_duplicate(
                tenant_id=context.tenant_id, bom_id=bom.id, line_id=line_id
            )
            self._record(context=context, action="bom.duplicate_confirmed", bom=updated)
        return updated

    def publish_bom(self, *, context: TenantContext, bom_id: UUID) -> BomSnapshot:
        """原子发布草稿，并把同项目此前发布版本标记为 SUPERSEDED。"""
        with self._transactions.atomic():
            bom = self._repository.get_for_update(tenant_id=context.tenant_id, bom_id=bom_id)
            if bom is None:
                raise BomNotFoundError("当前租户中不存在该 BOM。")
            project = self._require_project(
                context=context,
                project_id=bom.project_id,
                permission=PermissionCode.BOM_PUBLISH,
            )
            if project.status is not ProjectStatus.ACTIVE:
                raise BomImportError("只有活动项目的 BOM 可以发布。")
            publish_bom(
                current=bom.status,
                line_count=bom.line_count,
                error_count=bom.error_count,
            )
            published = self._repository.publish(
                tenant_id=context.tenant_id,
                bom_id=bom.id,
                membership_id=context.membership_id,
            )
            self._record(context=context, action="bom.published", bom=published)
        return published

    def cancel_bom(self, *, context: TenantContext, bom_id: UUID, reason: str) -> BomSnapshot:
        """取消没有有效投产引用的 BOM，并保存原因、操作者和时间。"""
        normalized_reason = " ".join(reason.split())
        with self._transactions.atomic():
            bom = self._repository.get_for_update(tenant_id=context.tenant_id, bom_id=bom_id)
            if bom is None:
                raise BomNotFoundError("当前租户中不存在该 BOM。")
            self._require_project(
                context=context,
                project_id=bom.project_id,
                permission=PermissionCode.BOM_CANCEL,
            )
            cancel_bom(
                current=bom.status,
                has_active_production=self._downstream.has_active_production(
                    tenant_id=context.tenant_id, bom_id=bom.id
                ),
                reason=normalized_reason,
            )
            cancelled = self._repository.cancel(
                tenant_id=context.tenant_id,
                bom_id=bom.id,
                membership_id=context.membership_id,
                reason=normalized_reason,
            )
            self._record(
                context=context,
                action="bom.cancelled",
                bom=cancelled,
                summary={"reason": normalized_reason},
            )
        return cancelled

    def compare_versions(self, *, context: TenantContext, left_id: UUID, right_id: UUID) -> BomDiff:
        """返回同租户两个版本的新增、移除和数量变化键。"""
        # compare 仓储先执行 tenant 条件；授权使用两个版本所属项目的可信
        # 关系，避免通过差异结果推断其他租户物料。
        left = self._repository.get_for_update(tenant_id=context.tenant_id, bom_id=left_id)
        right = self._repository.get_for_update(tenant_id=context.tenant_id, bom_id=right_id)
        if left is None or right is None or left.project_id != right.project_id:
            raise BomNotFoundError("待比较 BOM 不存在或不属于同一项目。")
        self._require_project(
            context=context,
            project_id=left.project_id,
            permission=PermissionCode.BOM_VIEW,
        )
        return self._repository.compare(
            tenant_id=context.tenant_id, left_id=left.id, right_id=right.id
        )

    def _validate_rows(
        self, *, context: TenantContext, rows: list[ParsedSpreadsheetRow]
    ) -> list[DraftBomLine]:
        material_codes = {
            row.values.get("material_code", "").strip().upper()
            for row in rows
            if row.values.get("material_code", "").strip()
        }
        unit_keys = {
            row.values.get("unit", "").strip().casefold()
            for row in rows
            if row.values.get("unit", "").strip()
        }
        materials = self._repository.find_materials(
            tenant_id=context.tenant_id, codes=material_codes
        )
        units = self._repository.find_units(tenant_id=context.tenant_id, keys=unit_keys)
        duplicate_keys = [self._duplicate_key(row) for row in rows]
        duplicate_counts = Counter(key for key in duplicate_keys if key)
        validated: list[DraftBomLine] = []
        for row, duplicate_key in zip(rows, duplicate_keys, strict=True):
            values = row.values
            error_codes: list[BomLineErrorCode] = []
            if row.formula_fields:
                error_codes.append(BomLineErrorCode.FORMULA_NOT_ALLOWED)
            material_code = values.get("material_code", "").strip().upper()
            material_name = values.get("material_name", "").strip()
            if not material_name:
                error_codes.append(BomLineErrorCode.MATERIAL_NAME_REQUIRED)
            quantity = parse_positive_quantity(values.get("quantity_per_unit", ""))
            if quantity is None:
                error_codes.append(BomLineErrorCode.INVALID_QUANTITY)
            unit_text = values.get("unit", "").strip()
            unit = units.get(unit_text.casefold())
            if unit is None:
                error_codes.append(BomLineErrorCode.UNKNOWN_UNIT)
            material = materials.get(material_code) if material_code else None
            if material_code and material is None:
                error_codes.append(BomLineErrorCode.UNKNOWN_MATERIAL)
            if not material_code:
                error_codes.append(BomLineErrorCode.MATERIAL_CONFIRMATION_REQUIRED)
            if material is not None and unit is not None and material.unit_id != unit.id:
                error_codes.append(BomLineErrorCode.UNIT_MISMATCH)
            if duplicate_key and duplicate_counts[duplicate_key] > 1:
                error_codes.append(BomLineErrorCode.SUSPECTED_DUPLICATE)
            validated.append(
                DraftBomLine(
                    source_row_number=row.source_row_number,
                    level_path=values.get("level_path", "").strip(),
                    assembly_code=values.get("assembly_code", "").strip(),
                    assembly_name=values.get("assembly_name", "").strip(),
                    material_id=None if material is None else material.id,
                    material_code=material_code,
                    material_name=material_name,
                    specification=values.get("specification", "").strip(),
                    brand=values.get("brand", "").strip(),
                    quantity_per_unit=quantity,
                    unit_id=None if unit is None else unit.id,
                    unit_text=unit_text,
                    procurement_required=(
                        True if material is None else material.procurement_required
                    ),
                    remark=values.get("remark", "").strip(),
                    validation_errors=tuple(code.value for code in dict.fromkeys(error_codes)),
                    duplicate_key=duplicate_key,
                )
            )
        return validated

    @staticmethod
    def _duplicate_key(row: ParsedSpreadsheetRow) -> str:
        values = row.values
        material_code = values.get("material_code", "").strip().upper()
        if material_code:
            return f"code:{material_code}"
        name = values.get("material_name", "").strip().casefold()
        specification = values.get("specification", "").strip().casefold()
        return f"pending:{name}|{specification}" if name else ""

    def _require_editable_bom(
        self, *, context: TenantContext, bom_id: UUID, permission: PermissionCode
    ) -> BomSnapshot:
        bom = self._repository.get_for_update(tenant_id=context.tenant_id, bom_id=bom_id)
        if bom is None:
            raise BomNotFoundError("当前租户中不存在该 BOM。")
        self._require_project(context=context, project_id=bom.project_id, permission=permission)
        ensure_draft_editable(bom.status)
        return bom

    def _require_project(
        self, *, context: TenantContext, project_id: UUID, permission: PermissionCode
    ) -> ProjectAccess:
        project = self._repository.get_project_access(
            tenant_id=context.tenant_id,
            project_id=project_id,
            membership_id=context.membership_id,
        )
        if project is None:
            raise BomNotFoundError("当前租户中不存在该项目。")
        authorize(
            context=context,
            resource_tenant_id=context.tenant_id,
            permission=permission,
            is_related=project.is_related,
            lookup=self._grants,
        )
        return project

    def _record(
        self,
        *,
        context: TenantContext,
        action: str,
        bom: BomSnapshot,
        summary: Mapping[str, object] | None = None,
    ) -> None:
        safe_summary: dict[str, object] = {
            "version_number": bom.version_number,
            "status": bom.status.value,
        }
        if summary:
            safe_summary.update(summary)
        self._audit.record(
            AuditEvent(
                tenant_id=context.tenant_id,
                actor_id=context.user_id,
                membership_id=context.membership_id,
                action=action,
                object_type="bom_version",
                object_id=str(bom.id),
                result=AuditResult.SUCCESS,
                summary=safe_summary,
            )
        )


def issues_for_lines(lines: list[DraftBomLine]) -> list[BomLineIssue]:
    """把持久化前草稿错误转换为带来源行号的展示列表。"""
    return [
        BomLineIssue(
            source_row_number=line.source_row_number,
            code=code,
            message=ERROR_MESSAGES[code],
        )
        for line in lines
        for code in map(BomLineErrorCode, line.validation_errors)
    ]
