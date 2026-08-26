"""旧采购订单原始证据到版本化规范包的严格映射。

模块只读取 ``pms-legacy-raw-v1`` 中经过白名单提取的 JSONL，不接触工作簿、
不运行宏，也不写业务数据库。它保留每个来源行的旧单价和旧保存总价，同时用
Decimal 重新计算金额，供后续隔离导入和差异签收使用。
"""

import hashlib
import json
import os
import tempfile
import unicodedata
from dataclasses import asdict, dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import cast

from pms.legacy_migration.raw_extraction import (
    DEFAULT_LEGACY_DATASETS,
    MANIFEST_FILENAME,
    RAW_SCHEMA_VERSION,
)

PURCHASE_ORDER_SCHEMA_VERSION = "pms-legacy-purchase-orders-v1"
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_DATASET_BYTES = 8 * 1024 * 1024
MAX_PACKAGE_BYTES = 8 * 1024 * 1024
CENT = Decimal("0.01")


class LegacyPurchaseOrderPackageError(ValueError):
    """原始证据或规范包违反字段、类型、分组或完整性契约。"""


@dataclass(frozen=True, slots=True)
class LegacyPurchaseOrderLine:
    """一条旧订单明细及其独立金额证据；所有金额单位均为元。"""

    source_row_number: int
    project_code: str
    device_model: str
    material_code: str
    material_name: str
    quantity: str
    specification: str
    brand: str
    unit_name: str
    remark: str
    project_start_date: str
    planned_completion_date: str
    receiving_department: str
    request_number: str
    legacy_unit_price: str
    legacy_saved_total: str
    recalculated_total: str
    production_unit: str
    part_attribute: str
    material_category: str


@dataclass(frozen=True, slots=True)
class LegacyPurchaseOrder:
    """以旧订单号为稳定边界、供应商唯一的历史订单。"""

    order_number: str
    supplier_name: str
    line_count: int
    legacy_saved_total: str
    recalculated_total: str
    difference: str
    has_amount_difference: bool
    lines: tuple[LegacyPurchaseOrderLine, ...]


@dataclass(frozen=True, slots=True)
class LegacyPurchaseOrderPackage:
    """映射结果；真实业务值只能写入 Git 忽略的受控路径。"""

    source_manifest_sha256: str
    source_record_count: int
    difference_order_count: int
    orders: tuple[LegacyPurchaseOrder, ...]


def map_legacy_purchase_orders(raw_directory: Path) -> LegacyPurchaseOrderPackage:
    """验证原始清单和类型，并按订单号形成确定顺序的规范包。"""
    root = _real_directory(raw_directory)
    manifest_path = root / MANIFEST_FILENAME
    manifest = _object(
        _read_json(manifest_path, MAX_MANIFEST_BYTES, "原始清单"),
        "原始清单",
        {
            "schema_version",
            "extracted_at",
            "contains_real_business_data",
            "restricted_data_included",
            "datasets",
        },
    )
    if manifest["schema_version"] != RAW_SCHEMA_VERSION:
        raise LegacyPurchaseOrderPackageError("原始清单版本不受支持。")
    if manifest["contains_real_business_data"] is not True:
        raise LegacyPurchaseOrderPackageError("原始清单缺少真实业务数据声明。")
    if manifest["restricted_data_included"] is not True:
        raise LegacyPurchaseOrderPackageError("采购订单映射要求显式提取受限数据。")
    dataset = _purchase_order_manifest(manifest["datasets"])
    rows = _read_rows(root, dataset)
    grouped: dict[str, list[tuple[str, LegacyPurchaseOrderLine]]] = {}
    for source_row, cells in rows:
        supplier, order_number, line = _map_line(source_row, cells)
        grouped.setdefault(order_number, []).append((supplier, line))
    orders = tuple(_build_order(number, grouped[number]) for number in sorted(grouped))
    return LegacyPurchaseOrderPackage(
        source_manifest_sha256=_sha256(manifest_path),
        source_record_count=len(rows),
        difference_order_count=sum(order.has_amount_difference for order in orders),
        orders=orders,
    )


def write_legacy_purchase_order_package(package: LegacyPurchaseOrderPackage, output: Path) -> None:
    """独占且原子发布规范包，避免覆盖已经复核过的迁移证据。"""
    target = _new_json_target(output)
    payload = {
        "schema_version": PURCHASE_ORDER_SCHEMA_VERSION,
        "source": {
            "raw_manifest_sha256": package.source_manifest_sha256,
            "record_count": package.source_record_count,
        },
        "summary": {
            "order_count": len(package.orders),
            "difference_order_count": package.difference_order_count,
        },
        "orders": [asdict(order) for order in package.orders],
    }
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if len(encoded) > MAX_PACKAGE_BYTES:
        raise LegacyPurchaseOrderPackageError("规范采购订单包超过 8 MiB 上限。")
    temporary: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=".pms-purchase-orders-", dir=target.parent)
        os.close(descriptor)
        temporary = Path(name)
        temporary.write_bytes(encoded)
        os.replace(temporary, target)
    except OSError as error:
        raise LegacyPurchaseOrderPackageError("无法安全写入规范采购订单包。") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_legacy_purchase_order_package(path: Path) -> LegacyPurchaseOrderPackage:
    """严格重载规范包，并重新验证金额摘要和订单分组。"""
    root = _object(
        _read_json(path, MAX_PACKAGE_BYTES, "规范采购订单包"),
        "规范采购订单包",
        {"schema_version", "source", "summary", "orders"},
    )
    if root["schema_version"] != PURCHASE_ORDER_SCHEMA_VERSION:
        raise LegacyPurchaseOrderPackageError("规范采购订单包版本不受支持。")
    source = _object(root["source"], "source", {"raw_manifest_sha256", "record_count"})
    summary = _object(root["summary"], "summary", {"order_count", "difference_order_count"})
    raw_orders = _array(root["orders"], "orders")
    orders = tuple(_parse_order(item) for item in raw_orders)
    record_count = _nonnegative_int(source["record_count"], "source.record_count")
    difference_count = _nonnegative_int(
        summary["difference_order_count"], "summary.difference_order_count"
    )
    if _nonnegative_int(summary["order_count"], "summary.order_count") != len(orders):
        raise LegacyPurchaseOrderPackageError("规范包订单数摘要不一致。")
    if record_count != sum(order.line_count for order in orders):
        raise LegacyPurchaseOrderPackageError("规范包来源行数摘要不一致。")
    if difference_count != sum(order.has_amount_difference for order in orders):
        raise LegacyPurchaseOrderPackageError("规范包差异订单数摘要不一致。")
    numbers = [order.order_number.casefold() for order in orders]
    if len(set(numbers)) != len(numbers):
        raise LegacyPurchaseOrderPackageError("规范包包含重复订单号。")
    return LegacyPurchaseOrderPackage(
        _sha256_text(source["raw_manifest_sha256"]), record_count, difference_count, orders
    )


def _purchase_order_manifest(value: object) -> dict[str, object]:
    for raw in _array(value, "datasets"):
        item = _object(
            raw,
            "datasets[]",
            {
                "dataset_id",
                "relative_path",
                "sheet_name",
                "headers",
                "source_size_bytes",
                "source_sha256",
                "record_count",
                "output_file",
                "restricted",
            },
        )
        if item["dataset_id"] == "purchase_orders":
            spec = next(
                candidate
                for candidate in DEFAULT_LEGACY_DATASETS
                if candidate.dataset_id == "purchase_orders"
            )
            headers = tuple(_text(item) for item in _array(item["headers"], "headers"))
            if headers != spec.expected_headers:
                raise LegacyPurchaseOrderPackageError("采购订单表头与映射版本不一致。")
            if item["output_file"] != "purchase_orders.jsonl" or item["restricted"] is not True:
                raise LegacyPurchaseOrderPackageError("采购订单清单的文件或敏感标记无效。")
            return item
    raise LegacyPurchaseOrderPackageError("原始清单缺少 purchase_orders 数据集。")


def _read_rows(
    root: Path, manifest: dict[str, object]
) -> tuple[tuple[int, tuple[object, ...]], ...]:
    expected = _nonnegative_int(manifest["record_count"], "purchase_orders.record_count")
    path = _ordinary_file(root / "purchase_orders.jsonl", MAX_DATASET_BYTES, "purchase_orders")
    rows: list[tuple[int, tuple[object, ...]]] = []
    try:
        with path.open(encoding="utf-8") as stream:
            for ordinal, line in enumerate(stream, 1):
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LegacyPurchaseOrderPackageError(
                        f"purchase_orders 第 {ordinal} 个记录不是有效 JSON。"
                    ) from error
                item = _object(raw, "purchase_orders[]", {"source_row_number", "cells"})
                source_row = _source_row(item["source_row_number"])
                cells = tuple(_array(item["cells"], "purchase_orders.cells"))
                if len(cells) != 20:
                    raise LegacyPurchaseOrderPackageError(
                        f"purchase_orders 来源第 {source_row} 行列数不正确。"
                    )
                rows.append((source_row, cells))
    except OSError as error:
        raise LegacyPurchaseOrderPackageError("无法读取 purchase_orders 数据集。") from error
    if len(rows) != expected or len({row for row, _ in rows}) != len(rows):
        raise LegacyPurchaseOrderPackageError("采购订单行数或来源行号不一致。")
    return tuple(rows)


def _map_line(
    source_row: int, cells: tuple[object, ...]
) -> tuple[str, str, LegacyPurchaseOrderLine]:
    def text(index: int) -> str:
        """按列规则读取规范化文本，并让类型检查保留明确返回类型。"""
        return _cell_text(cells[index], source_row, index)

    quantity = _positive_decimal(cells[4], source_row, "下单数量")
    unit_price = _nonnegative_decimal(cells[13], source_row, "单价")
    saved_total = _decimal(cells[14], source_row, "总价")
    supplier = _required(text(15), source_row, "承接方")
    order_number = _required(text(16), source_row, "订单编号")
    material_code = _required(text(2), source_row, "件号/编码")
    recalculated = (quantity * unit_price).quantize(CENT, rounding=ROUND_HALF_UP)
    line = LegacyPurchaseOrderLine(
        source_row_number=source_row,
        project_code=_required(text(0), source_row, "项目编号"),
        device_model=text(1),
        material_code=material_code,
        material_name=text(3),
        quantity=_decimal_text(quantity),
        specification=text(5),
        brand=text(6),
        unit_name=text(7),
        remark=text(8),
        project_start_date=_cell_date(cells[9], source_row, "项目开始时间"),
        planned_completion_date=_cell_date(cells[10], source_row, "计划完成时间"),
        receiving_department=text(11),
        request_number=_required(text(12), source_row, "请购单号"),
        legacy_unit_price=_decimal_text(unit_price),
        # 旧保存总价可能带有分以下精度；迁移证据必须保留完整 Decimal，
        # 否则展示前的提前舍入会漏掉真实存在的厘级差异。
        legacy_saved_total=_decimal_text(saved_total),
        recalculated_total=_money_text(recalculated),
        production_unit=text(17),
        part_attribute=text(18),
        material_category=text(19),
    )
    return supplier, order_number, line


def _build_order(
    order_number: str, records: list[tuple[str, LegacyPurchaseOrderLine]]
) -> LegacyPurchaseOrder:
    suppliers = {supplier.casefold(): supplier for supplier, _ in records}
    if len(suppliers) != 1:
        rows = ",".join(str(line.source_row_number) for _, line in records)
        raise LegacyPurchaseOrderPackageError(f"订单来源行 {rows} 出现多个承接方。")
    lines = tuple(line for _, line in sorted(records, key=lambda item: item[1].source_row_number))
    saved = sum((Decimal(line.legacy_saved_total) for line in lines), Decimal())
    recalculated = sum((Decimal(line.recalculated_total) for line in lines), Decimal())
    difference = (saved - recalculated).quantize(CENT, rounding=ROUND_HALF_UP)
    # 行差异可能在订单汇总时正负抵消。迁移验收关心旧数据是否曾出现不一致，
    # 因而按“任一行有差异”标记订单，同时另存整单净差额供财务对账。
    has_amount_difference = any(
        Decimal(line.legacy_saved_total) != Decimal(line.quantity) * Decimal(line.legacy_unit_price)
        for line in lines
    )
    return LegacyPurchaseOrder(
        order_number=order_number,
        supplier_name=next(iter(suppliers.values())),
        line_count=len(lines),
        legacy_saved_total=_money_text(saved),
        recalculated_total=_money_text(recalculated),
        difference=_money_text(difference),
        has_amount_difference=has_amount_difference,
        lines=lines,
    )


def _parse_order(value: object) -> LegacyPurchaseOrder:
    fields = set(LegacyPurchaseOrder.__dataclass_fields__)
    item = _object(value, "orders[]", fields)
    raw_lines = _array(item["lines"], "orders[].lines")
    lines = tuple(_parse_line(line) for line in raw_lines)
    if not lines:
        raise LegacyPurchaseOrderPackageError("规范包订单明细不能为空。")
    rebuilt = _build_order(
        _required(_text(item["order_number"]), lines[0].source_row_number, "订单编号"),
        [(_text(item["supplier_name"]), line) for line in lines],
    )
    for field in fields - {"lines"}:
        if getattr(rebuilt, field) != item[field]:
            raise LegacyPurchaseOrderPackageError("规范包订单摘要与明细不一致。")
    return rebuilt


def _parse_line(value: object) -> LegacyPurchaseOrderLine:
    fields = tuple(LegacyPurchaseOrderLine.__dataclass_fields__)
    item = _object(value, "orders[].lines[]", set(fields))
    source_row = _source_row(item["source_row_number"])
    kwargs = {field: _text(item[field]) for field in fields[1:]}
    quantity = _parse_decimal_text(kwargs["quantity"], source_row, "quantity", positive=True)
    unit_price = _parse_decimal_text(
        kwargs["legacy_unit_price"], source_row, "legacy_unit_price", positive=False
    )
    saved = _parse_decimal_text(
        kwargs["legacy_saved_total"], source_row, "legacy_saved_total", positive=None
    )
    recalculated = (quantity * unit_price).quantize(CENT, rounding=ROUND_HALF_UP)
    if (
        _decimal_text(saved) != kwargs["legacy_saved_total"]
        or _money_text(recalculated) != kwargs["recalculated_total"]
    ):
        raise LegacyPurchaseOrderPackageError("规范包明细金额格式或重算结果不一致。")
    return LegacyPurchaseOrderLine(source_row_number=source_row, **kwargs)


def _cell_text(cell: object, row: int, column: int) -> str:
    if cell is None:
        return ""
    item = _object(cell, "purchase_orders.cells[]", {"type", "value"})
    kind, value = item["type"], item["value"]
    allowed = {"text", "number"} if column in {2, 5, 6} else {"text"}
    if kind is None and value is None:
        return ""
    if kind not in allowed or not isinstance(value, str):
        raise LegacyPurchaseOrderPackageError(f"purchase_orders 来源第 {row} 行文本类型无效。")
    return " ".join(unicodedata.normalize("NFKC", value).split())


def _cell_date(cell: object, row: int, label: str) -> str:
    item = _object(cell, "purchase_orders.cells[]", {"type", "value"})
    if item["type"] != "date" or not isinstance(item["value"], str):
        raise LegacyPurchaseOrderPackageError(f"purchase_orders 来源第 {row} 行{label}类型无效。")
    try:
        return date.fromisoformat(item["value"]).isoformat()
    except ValueError as error:
        raise LegacyPurchaseOrderPackageError(
            f"purchase_orders 来源第 {row} 行{label}无效。"
        ) from error


def _positive_decimal(cell: object, row: int, label: str) -> Decimal:
    value = _number_cell(cell, row, label)
    if value <= 0:
        raise LegacyPurchaseOrderPackageError(f"purchase_orders 来源第 {row} 行{label}必须大于零。")
    return value


def _nonnegative_decimal(cell: object, row: int, label: str) -> Decimal:
    value = _number_cell(cell, row, label)
    if value < 0:
        raise LegacyPurchaseOrderPackageError(f"purchase_orders 来源第 {row} 行{label}不能为负。")
    return value


def _decimal(cell: object, row: int, label: str) -> Decimal:
    return _number_cell(cell, row, label)


def _number_cell(cell: object, row: int, label: str) -> Decimal:
    item = _object(cell, "purchase_orders.cells[]", {"type", "value"})
    if item["type"] != "number" or not isinstance(item["value"], str):
        raise LegacyPurchaseOrderPackageError(f"purchase_orders 来源第 {row} 行{label}类型无效。")
    return _parse_decimal_text(item["value"], row, label, positive=None)


def _parse_decimal_text(value: str, row: int, label: str, *, positive: bool | None) -> Decimal:
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise LegacyPurchaseOrderPackageError(
            f"purchase_orders 来源第 {row} 行{label}不是十进制数。"
        ) from error
    if (
        not result.is_finite()
        or (positive is True and result <= 0)
        or (positive is False and result < 0)
    ):
        raise LegacyPurchaseOrderPackageError(f"purchase_orders 来源第 {row} 行{label}数值无效。")
    return result


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _money_text(value: Decimal) -> str:
    return format(value.quantize(CENT, rounding=ROUND_HALF_UP), ".2f")


def _required(value: str, row: int, label: str) -> str:
    if not value:
        raise LegacyPurchaseOrderPackageError(f"purchase_orders 来源第 {row} 行缺少{label}。")
    return value


def _read_json(path: Path, maximum: int, label: str) -> object:
    ordinary = _ordinary_file(path, maximum, label)
    try:
        return json.loads(ordinary.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LegacyPurchaseOrderPackageError(f"{label}不是有效 UTF-8 JSON。") from error


def _ordinary_file(path: Path, maximum: int, label: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise LegacyPurchaseOrderPackageError(f"{label}必须是普通文件。")
    resolved = path.resolve(strict=True)
    if resolved.stat().st_size > maximum:
        raise LegacyPurchaseOrderPackageError(f"{label}超过大小上限。")
    return resolved


def _real_directory(path: Path) -> Path:
    if path.is_symlink():
        raise LegacyPurchaseOrderPackageError("原始迁移包不能是符号链接。")
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise LegacyPurchaseOrderPackageError("原始迁移包不存在。") from error
    if not resolved.is_dir():
        raise LegacyPurchaseOrderPackageError("原始迁移包必须是目录。")
    return resolved


def _new_json_target(path: Path) -> Path:
    if path.suffix.lower() != ".json" or path.exists() or path.is_symlink():
        raise LegacyPurchaseOrderPackageError("输出必须是尚不存在的 .json 文件。")
    try:
        parent = path.parent.resolve(strict=True)
    except OSError as error:
        raise LegacyPurchaseOrderPackageError("输出父目录不存在。") from error
    if not parent.is_dir() or path.parent.is_symlink():
        raise LegacyPurchaseOrderPackageError("输出父路径必须是真实目录。")
    return parent / path.name


def _object(value: object, label: str, fields: set[str]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != fields:
        raise LegacyPurchaseOrderPackageError(f"{label}字段集合不正确。")
    return cast(dict[str, object], value)


def _array(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise LegacyPurchaseOrderPackageError(f"{label}必须是数组。")
    return cast(list[object], value)


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise LegacyPurchaseOrderPackageError("规范包文本字段类型无效。")
    return value


def _source_row(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 2:
        raise LegacyPurchaseOrderPackageError("来源行号无效。")
    return value


def _nonnegative_int(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise LegacyPurchaseOrderPackageError(f"{label}无效。")
    return value


def _sha256_text(value: object) -> str:
    text = _text(value)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise LegacyPurchaseOrderPackageError("原始清单摘要不是 SHA-256。")
    return text


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    except OSError as error:
        raise LegacyPurchaseOrderPackageError("无法计算原始清单摘要。") from error
    return digest.hexdigest()
