"""`SLICE-001` 脱敏旧数据迁移包的严格输入契约。

迁移入口只接受人工导出的受控 JSON，不直接读取任意旧工作簿或运行宏。
这里尽早把无类型 JSON 转换为不可变数据类，避免错误字段、秘密或浮点
数量进入正式应用用例。
"""

import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import cast

MAX_INPUT_BYTES = 2 * 1024 * 1024
SCHEMA_VERSION = "pms-legacy-slice-v1"
SAMPLE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")


class LegacyPackageError(ValueError):
    """表示迁移包格式、字段或业务确认元数据不满足受控契约。"""


@dataclass(frozen=True, slots=True)
class SampleMetadata:
    """说明样例能否成为业务验收证据。

    `synthetic` 只证明迁移技术流程；`business_confirmed` 必须包含真实的
    接受人显示名，才能用于 AC-S001-043 的人工签收材料。
    """

    id: str
    kind: str
    confirmed_by: str | None


@dataclass(frozen=True, slots=True)
class CodeName:
    code: str
    name: str


@dataclass(frozen=True, slots=True)
class LegacyCustomer(CodeName):
    pass


@dataclass(frozen=True, slots=True)
class LegacyMaterial:
    code: str
    name: str
    specification: str
    brand: str
    unit_code: str
    category_code: str
    procurement_required: bool


@dataclass(frozen=True, slots=True)
class LegacyProject:
    number: str
    customer_code: str
    device_model: str
    owner_username: str
    start_date: date | None
    planned_completion_date: date | None


@dataclass(frozen=True, slots=True)
class LegacyBomRow:
    material_code: str
    material_name: str
    specification: str
    brand: str
    quantity_per_unit: Decimal
    unit_code: str
    remark: str


@dataclass(frozen=True, slots=True)
class LegacyBom:
    version_number: int
    rows: tuple[LegacyBomRow, ...]


@dataclass(frozen=True, slots=True)
class LegacyProduction:
    production_units: int
    production_unit: str
    receiving_department: str


@dataclass(frozen=True, slots=True)
class LegacyPurchaseCandidate:
    source_row_number: int
    material_code: str
    requested_quantity: Decimal
    unit_code: str


@dataclass(frozen=True, slots=True)
class AcceptedDifference:
    check_key: str
    rule_id: str
    reason: str
    accepted_by: str


@dataclass(frozen=True, slots=True)
class LegacySlicePackage:
    """完成首切片迁移与对账所需的最小、无秘密数据。"""

    sample: SampleMetadata
    customer: LegacyCustomer
    units: tuple[CodeName, ...]
    categories: tuple[CodeName, ...]
    materials: tuple[LegacyMaterial, ...]
    project: LegacyProject
    bom: LegacyBom
    production: LegacyProduction
    purchase_candidates: tuple[LegacyPurchaseCandidate, ...]
    accepted_differences: tuple[AcceptedDifference, ...]


def load_legacy_slice_package(path: Path) -> LegacySlicePackage:
    """从普通 UTF-8 JSON 文件读取受控包，不跟随符号链接或接受超限内容。"""
    if path.suffix.lower() != ".json" or not path.is_file() or path.is_symlink():
        raise LegacyPackageError("迁移输入必须是现有的普通 .json 文件。")
    try:
        size = path.stat().st_size
        content = path.read_bytes()
    except OSError as error:
        raise LegacyPackageError("迁移输入无法读取。") from error
    if size > MAX_INPUT_BYTES or len(content) > MAX_INPUT_BYTES:
        raise LegacyPackageError("迁移输入超过 2 MiB 上限。")
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LegacyPackageError("迁移输入必须是有效 UTF-8 JSON。") from error
    return parse_legacy_slice_package(payload)


def parse_legacy_slice_package(payload: object) -> LegacySlicePackage:
    """执行严格键集合、类型、日期和 Decimal 校验。"""
    root = _object(
        payload,
        label="root",
        required={
            "schema_version",
            "sample",
            "master_data",
            "project",
            "bom",
            "production",
            "legacy_purchase_candidates",
            "accepted_differences",
        },
    )
    if _text(root["schema_version"], "schema_version") != SCHEMA_VERSION:
        raise LegacyPackageError("不支持的迁移包 schema_version。")
    sample = _sample(root["sample"])
    master = _object(
        root["master_data"],
        label="master_data",
        required={"customer", "units", "categories", "materials"},
    )
    package = LegacySlicePackage(
        sample=sample,
        customer=_customer(master["customer"]),
        units=_code_names(master["units"], "units"),
        categories=_code_names(master["categories"], "categories"),
        materials=_materials(master["materials"]),
        project=_project(root["project"]),
        bom=_bom(root["bom"]),
        production=_production(root["production"]),
        purchase_candidates=_purchase_candidates(root["legacy_purchase_candidates"]),
        accepted_differences=_accepted_differences(root["accepted_differences"]),
    )
    _validate_cross_references(package)
    return package


def _sample(value: object) -> SampleMetadata:
    item = _object(value, label="sample", required={"id", "kind", "confirmed_by"})
    sample_id = _text(item["id"], "sample.id")
    if SAMPLE_ID_PATTERN.fullmatch(sample_id) is None:
        raise LegacyPackageError("sample.id 必须是 3 至 64 位小写 slug。")
    kind = _text(item["kind"], "sample.kind")
    if kind not in {"synthetic", "business_confirmed"}:
        raise LegacyPackageError("sample.kind 只允许 synthetic 或 business_confirmed。")
    confirmed_by = _optional_text(item["confirmed_by"], "sample.confirmed_by")
    if kind == "business_confirmed" and confirmed_by is None:
        raise LegacyPackageError("业务已确认样例必须填写 confirmed_by。")
    if kind == "synthetic" and confirmed_by is not None:
        raise LegacyPackageError("虚构技术样例不能填写业务 confirmed_by。")
    return SampleMetadata(id=sample_id, kind=kind, confirmed_by=confirmed_by)


def _customer(value: object) -> LegacyCustomer:
    code_name = _code_name(value, "customer")
    return LegacyCustomer(code=code_name.code, name=code_name.name)


def _code_names(value: object, label: str) -> tuple[CodeName, ...]:
    items = _array(value, label)
    result = tuple(_code_name(item, f"{label}[]") for item in items)
    if not result:
        raise LegacyPackageError(f"{label} 至少包含一项。")
    if len({item.code.casefold() for item in result}) != len(result):
        raise LegacyPackageError(f"{label} 存在重复 code。")
    return result


def _code_name(value: object, label: str) -> CodeName:
    item = _object(value, label=label, required={"code", "name"})
    return CodeName(
        code=_text(item["code"], f"{label}.code"), name=_text(item["name"], f"{label}.name")
    )


def _materials(value: object) -> tuple[LegacyMaterial, ...]:
    result: list[LegacyMaterial] = []
    for raw in _array(value, "materials"):
        item = _object(
            raw,
            label="materials[]",
            required={
                "code",
                "name",
                "specification",
                "brand",
                "unit_code",
                "category_code",
                "procurement_required",
            },
        )
        procurement_required = item["procurement_required"]
        if not isinstance(procurement_required, bool):
            raise LegacyPackageError("materials[].procurement_required 必须是布尔值。")
        result.append(
            LegacyMaterial(
                code=_text(item["code"], "materials[].code"),
                name=_text(item["name"], "materials[].name"),
                specification=_optional_text(item["specification"], "materials[].specification")
                or "",
                brand=_optional_text(item["brand"], "materials[].brand") or "",
                unit_code=_text(item["unit_code"], "materials[].unit_code"),
                category_code=_text(item["category_code"], "materials[].category_code"),
                procurement_required=procurement_required,
            )
        )
    if not result or len({item.code.casefold() for item in result}) != len(result):
        raise LegacyPackageError("materials 必须非空且 code 不重复。")
    return tuple(result)


def _project(value: object) -> LegacyProject:
    item = _object(
        value,
        label="project",
        required={
            "number",
            "customer_code",
            "device_model",
            "owner_username",
            "start_date",
            "planned_completion_date",
        },
    )
    return LegacyProject(
        number=_text(item["number"], "project.number"),
        customer_code=_text(item["customer_code"], "project.customer_code"),
        device_model=_text(item["device_model"], "project.device_model"),
        owner_username=_text(item["owner_username"], "project.owner_username"),
        start_date=_optional_date(item["start_date"], "project.start_date"),
        planned_completion_date=_optional_date(
            item["planned_completion_date"], "project.planned_completion_date"
        ),
    )


def _bom(value: object) -> LegacyBom:
    item = _object(value, label="bom", required={"version_number", "rows"})
    version_number = _positive_integer(item["version_number"], "bom.version_number")
    rows: list[LegacyBomRow] = []
    for raw in _array(item["rows"], "bom.rows"):
        row = _object(
            raw,
            label="bom.rows[]",
            required={
                "material_code",
                "material_name",
                "specification",
                "brand",
                "quantity_per_unit",
                "unit_code",
                "remark",
            },
        )
        rows.append(
            LegacyBomRow(
                material_code=_text(row["material_code"], "bom.rows[].material_code"),
                material_name=_text(row["material_name"], "bom.rows[].material_name"),
                specification=_optional_text(row["specification"], "bom.rows[].specification")
                or "",
                brand=_optional_text(row["brand"], "bom.rows[].brand") or "",
                quantity_per_unit=_positive_decimal(
                    row["quantity_per_unit"], "bom.rows[].quantity_per_unit"
                ),
                unit_code=_text(row["unit_code"], "bom.rows[].unit_code"),
                remark=_optional_text(row["remark"], "bom.rows[].remark") or "",
            )
        )
    if not rows:
        raise LegacyPackageError("bom.rows 至少包含一行。")
    return LegacyBom(version_number=version_number, rows=tuple(rows))


def _production(value: object) -> LegacyProduction:
    item = _object(
        value,
        label="production",
        required={"production_units", "production_unit", "receiving_department"},
    )
    return LegacyProduction(
        production_units=_positive_integer(item["production_units"], "production.production_units"),
        production_unit=_text(item["production_unit"], "production.production_unit"),
        receiving_department=_text(item["receiving_department"], "production.receiving_department"),
    )


def _purchase_candidates(value: object) -> tuple[LegacyPurchaseCandidate, ...]:
    result: list[LegacyPurchaseCandidate] = []
    for raw in _array(value, "legacy_purchase_candidates"):
        item = _object(
            raw,
            label="legacy_purchase_candidates[]",
            required={
                "source_row_number",
                "material_code",
                "requested_quantity",
                "unit_code",
            },
        )
        result.append(
            LegacyPurchaseCandidate(
                source_row_number=_positive_integer(
                    item["source_row_number"], "legacy_purchase_candidates[].source_row_number"
                ),
                material_code=_text(
                    item["material_code"], "legacy_purchase_candidates[].material_code"
                ),
                requested_quantity=_positive_decimal(
                    item["requested_quantity"],
                    "legacy_purchase_candidates[].requested_quantity",
                ),
                unit_code=_text(item["unit_code"], "legacy_purchase_candidates[].unit_code"),
            )
        )
    if not result:
        raise LegacyPackageError("legacy_purchase_candidates 至少包含一行。")
    if len({item.source_row_number for item in result}) != len(result):
        raise LegacyPackageError("legacy_purchase_candidates 的 source_row_number 不得重复。")
    return tuple(sorted(result, key=lambda item: item.source_row_number))


def _accepted_differences(value: object) -> tuple[AcceptedDifference, ...]:
    result: list[AcceptedDifference] = []
    for raw in _array(value, "accepted_differences"):
        item = _object(
            raw,
            label="accepted_differences[]",
            required={"check_key", "rule_id", "reason", "accepted_by"},
        )
        result.append(
            AcceptedDifference(
                check_key=_text(item["check_key"], "accepted_differences[].check_key"),
                rule_id=_text(item["rule_id"], "accepted_differences[].rule_id"),
                reason=_text(item["reason"], "accepted_differences[].reason"),
                accepted_by=_text(item["accepted_by"], "accepted_differences[].accepted_by"),
            )
        )
    if len({item.check_key for item in result}) != len(result):
        raise LegacyPackageError("accepted_differences.check_key 不得重复。")
    return tuple(result)


def _validate_cross_references(package: LegacySlicePackage) -> None:
    unit_codes = {item.code.casefold() for item in package.units}
    category_codes = {item.code.casefold() for item in package.categories}
    material_codes = {item.code.casefold() for item in package.materials}
    if package.project.customer_code.casefold() != package.customer.code.casefold():
        raise LegacyPackageError("project.customer_code 必须引用 master_data.customer。")
    for material in package.materials:
        if material.unit_code.casefold() not in unit_codes:
            raise LegacyPackageError("material.unit_code 引用了未声明单位。")
        if material.category_code.casefold() not in category_codes:
            raise LegacyPackageError("material.category_code 引用了未声明分类。")
    for row in package.bom.rows:
        if row.material_code.casefold() not in material_codes:
            raise LegacyPackageError("bom.rows[].material_code 引用了未声明物料。")
        if row.unit_code.casefold() not in unit_codes:
            raise LegacyPackageError("bom.rows[].unit_code 引用了未声明单位。")
    bom_source_rows = set(range(2, len(package.bom.rows) + 2))
    if any(item.source_row_number not in bom_source_rows for item in package.purchase_candidates):
        raise LegacyPackageError("请购候选 source_row_number 必须引用 BOM 数据行。")


def _object(value: object, *, label: str, required: set[str]) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise LegacyPackageError(f"{label} 必须是 JSON object。")
    item = cast(dict[str, object], value)
    actual = set(item)
    if actual != required:
        missing = ",".join(sorted(required - actual)) or "无"
        extra = ",".join(sorted(actual - required)) or "无"
        raise LegacyPackageError(f"{label} 字段不匹配；缺少={missing}，多余={extra}。")
    return item


def _array(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise LegacyPackageError(f"{label} 必须是 JSON array。")
    return cast(list[object], value)


def _text(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise LegacyPackageError(f"{label} 必须是字符串。")
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > 500:
        raise LegacyPackageError(f"{label} 不能为空且不能超过 500 字符。")
    return normalized


def _optional_text(value: object, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise LegacyPackageError(f"{label} 必须是字符串或 null。")
    normalized = " ".join(value.split())
    if len(normalized) > 500:
        raise LegacyPackageError(f"{label} 不能超过 500 字符。")
    return normalized or None


def _positive_integer(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise LegacyPackageError(f"{label} 必须是正整数。")
    return value


def _positive_decimal(value: object, label: str) -> Decimal:
    if not isinstance(value, str):
        raise LegacyPackageError(f"{label} 必须使用 JSON 字符串保存十进制数。")
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise LegacyPackageError(f"{label} 不是有效十进制数。") from error
    if not result.is_finite() or result <= 0:
        raise LegacyPackageError(f"{label} 必须是大于零的有限十进制数。")
    return result


def _optional_date(value: object, label: str) -> date | None:
    if value is None:
        return None
    text = _text(value, label)
    try:
        return date.fromisoformat(text)
    except ValueError as error:
        raise LegacyPackageError(f"{label} 必须是 YYYY-MM-DD 或 null。") from error
