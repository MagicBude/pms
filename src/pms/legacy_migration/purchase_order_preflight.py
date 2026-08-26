"""旧采购订单正式导入前的只读引用完整性预检。

规范包本身只能证明旧行结构和金额可解释；正式订单还必须引用当前租户内唯一的
供应商、项目、物料、单位和生产请购行。本模块只查询这些关系，不创建占位记录，
并且报告只包含计数与来源行号，避免泄露供应商、价格和备注。
"""

import json
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import UUID

from django.db.models import QuerySet

from pms.identity.infrastructure.django.models import User
from pms.legacy_migration.purchase_order_package import LegacyPurchaseOrderPackage
from pms.master_data.infrastructure.django.models import Material, Supplier, Unit
from pms.procurement.infrastructure.django.models import PurchaseRequestLine
from pms.projects.infrastructure.django.models import Project
from pms.tenancy.infrastructure.django.models import Membership

PREFLIGHT_SCHEMA_VERSION = "pms-legacy-purchase-order-preflight-v1"


class LegacyPurchaseOrderPreflightError(ValueError):
    """操作者、输出或引用状态不满足安全预检契约。"""


@dataclass(frozen=True, slots=True)
class ReferencePreflightResult:
    """某类引用的逐行唯一匹配结果，不保存被匹配的业务值。"""

    reference: str
    total_rows: int
    resolved_rows: int
    unresolved_source_rows: tuple[int, ...]
    ambiguous_source_rows: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class LegacyPurchaseOrderPreflightReport:
    """决定规范包是否具备正式导入条件的无敏感值报告。"""

    schema_version: str
    source_manifest_sha256: str
    source_record_count: int
    order_count: int
    difference_order_count: int
    ready_for_import: bool
    references: tuple[ReferencePreflightResult, ...]


def preflight_legacy_purchase_orders(
    *, package: LegacyPurchaseOrderPackage, actor_username: str
) -> LegacyPurchaseOrderPreflightReport:
    """在操作者唯一租户内逐行检查所有正式订单必需引用，且不写数据库。"""
    tenant_id = _actor_tenant(actor_username)
    supplier_keys = _supplier_keys(Supplier.objects.filter(tenant_id=tenant_id, is_active=True))
    project_keys = _single_key_map(
        Project.objects.filter(tenant_id=tenant_id), "number", normalize=True
    )
    material_keys = _single_key_map(
        Material.objects.filter(tenant_id=tenant_id, is_active=True), "code", normalize=True
    )
    unit_keys = _unit_keys(Unit.objects.filter(tenant_id=tenant_id, is_active=True))
    request_keys = _request_line_keys(
        PurchaseRequestLine.objects.filter(tenant_id=tenant_id).select_related(
            "purchase_request__project", "material"
        )
    )
    checks: dict[str, tuple[list[int], list[int]]] = {
        name: ([], []) for name in ("supplier", "project", "material", "unit", "request_line")
    }
    for order in package.orders:
        supplier_matches = supplier_keys.get(_normalize(order.supplier_name), ())
        for line in order.lines:
            _record_match(checks["supplier"], line.source_row_number, supplier_matches)
            project_matches = project_keys.get(_normalize(line.project_code), ())
            material_matches = material_keys.get(_normalize(line.material_code), ())
            unit_matches = unit_keys.get(_normalize(line.unit_name), ())
            _record_match(checks["project"], line.source_row_number, project_matches)
            _record_match(checks["material"], line.source_row_number, material_matches)
            _record_match(checks["unit"], line.source_row_number, unit_matches)
            request_matches = request_keys.get(
                (
                    _normalize(line.request_number),
                    _normalize(line.project_code),
                    _normalize(line.material_code),
                ),
                (),
            )
            _record_match(checks["request_line"], line.source_row_number, request_matches)
    references = tuple(
        _result(name, package.source_record_count, *checks[name])
        for name in ("supplier", "project", "material", "unit", "request_line")
    )
    return LegacyPurchaseOrderPreflightReport(
        schema_version=PREFLIGHT_SCHEMA_VERSION,
        source_manifest_sha256=package.source_manifest_sha256,
        source_record_count=package.source_record_count,
        order_count=len(package.orders),
        difference_order_count=package.difference_order_count,
        ready_for_import=all(
            not item.unresolved_source_rows and not item.ambiguous_source_rows
            for item in references
        ),
        references=references,
    )


def write_purchase_order_preflight_report(
    report: LegacyPurchaseOrderPreflightReport, output: Path
) -> None:
    """独占写入无敏感值报告，避免覆盖不同时间的预检证据。"""
    if output.suffix.lower() != ".json" or output.is_symlink() or not output.parent.is_dir():
        raise LegacyPurchaseOrderPreflightError("预检报告必须位于现有目录且使用 .json 扩展名。")
    payload = json.dumps(asdict(report), ensure_ascii=False, indent=2) + "\n"
    try:
        with output.open("x", encoding="utf-8") as stream:
            stream.write(payload)
    except FileExistsError as error:
        raise LegacyPurchaseOrderPreflightError("预检报告已存在，请使用新的文件名。") from error
    except OSError as error:
        raise LegacyPurchaseOrderPreflightError("无法写入预检报告。") from error


def _actor_tenant(username: str) -> UUID:
    users = User.objects.filter(username=username, is_active=True)
    if users.count() != 1:
        raise LegacyPurchaseOrderPreflightError("迁移操作者不存在或不可用。")
    memberships = list(
        Membership.objects.filter(user=users.get(), tenant__is_active=True, is_active=True)
        .values_list("tenant_id", flat=True)
        .order_by("created_at", "id")[:2]
    )
    if len(memberships) != 1:
        raise LegacyPurchaseOrderPreflightError(
            "迁移操作者必须恰好拥有一个活动 tenant membership。"
        )
    return memberships[0]


def _supplier_keys(queryset: QuerySet[Supplier]) -> dict[str, tuple[UUID, ...]]:
    keys: dict[str, list[UUID]] = {}
    for supplier in queryset.only("id", "name", "short_name"):
        for value in {supplier.name, supplier.short_name} - {""}:
            keys.setdefault(_normalize(value), []).append(supplier.id)
    return {key: tuple(dict.fromkeys(values)) for key, values in keys.items()}


def _unit_keys(queryset: QuerySet[Unit]) -> dict[str, tuple[UUID, ...]]:
    keys: dict[str, list[UUID]] = {}
    for unit in queryset.only("id", "code", "name"):
        for value in {unit.code, unit.name}:
            keys.setdefault(_normalize(value), []).append(unit.id)
    return {key: tuple(dict.fromkeys(values)) for key, values in keys.items()}


def _single_key_map(
    queryset: QuerySet[Project] | QuerySet[Material], field: str, *, normalize: bool
) -> dict[str, tuple[UUID, ...]]:
    result: dict[str, list[UUID]] = {}
    for identifier, value in queryset.values_list("id", field):
        key = _normalize(value) if normalize else value
        result.setdefault(key, []).append(identifier)
    return {key: tuple(values) for key, values in result.items()}


def _request_line_keys(
    queryset: QuerySet[PurchaseRequestLine],
) -> dict[tuple[str, str, str], tuple[UUID, ...]]:
    result: dict[tuple[str, str, str], list[UUID]] = {}
    for line in queryset:
        request_number = line.purchase_request.request_number
        if request_number is None:
            continue
        key = (
            _normalize(request_number),
            _normalize(line.purchase_request.project.number),
            _normalize(line.material.code),
        )
        result.setdefault(key, []).append(line.id)
    return {key: tuple(values) for key, values in result.items()}


def _record_match(
    result: tuple[list[int], list[int]], source_row: int, matches: tuple[UUID, ...]
) -> None:
    unresolved, ambiguous = result
    if not matches:
        unresolved.append(source_row)
    elif len(matches) > 1:
        ambiguous.append(source_row)


def _result(
    reference: str, total: int, unresolved: list[int], ambiguous: list[int]
) -> ReferencePreflightResult:
    return ReferencePreflightResult(
        reference=reference,
        total_rows=total,
        resolved_rows=total - len(unresolved) - len(ambiguous),
        unresolved_source_rows=tuple(unresolved),
        ambiguous_source_rows=tuple(ambiguous),
    )


def _normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()
