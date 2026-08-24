"""客户与供应商原始数据到版本化规范包的安全映射。

本模块是 ADR-0005 所定义迁移链的第二层。它只接受本项目原始提取器生成
的 ``pms-legacy-raw-v1`` 目录，验证清单、表头、行数和单元格类型后，
生成可由正式应用用例消费的 ``pms-legacy-master-data-v1`` JSON。

规范包仍包含真实税号、银行账户和联系方式，必须留在 Git 忽略区。普通
命令输出只报告记录数和摘要，任何异常也只引用数据集与来源行号。
"""

import hashlib
import json
import os
import tempfile
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

from pms.legacy_migration.raw_extraction import (
    DEFAULT_LEGACY_DATASETS,
    MANIFEST_FILENAME,
    RAW_SCHEMA_VERSION,
)

MASTER_DATA_SCHEMA_VERSION = "pms-legacy-master-data-v1"
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_DATASET_BYTES = 8 * 1024 * 1024
MAX_PACKAGE_BYTES = 4 * 1024 * 1024


class LegacyMasterDataPackageError(ValueError):
    """表示原始证据或规范主数据包不满足版本化契约。"""


@dataclass(frozen=True, slots=True)
class LegacyCustomerRecord:
    """客户映射记录；代码按来源行号稳定生成，简称原样单独保存。"""

    source_row_number: int
    code: str
    short_name: str
    name: str
    tax_identifier: str
    address: str
    phone: str
    bank_name: str
    bank_account: str
    bank_routing_number: str


@dataclass(frozen=True, slots=True)
class LegacySupplierRecord:
    """供应商映射记录；税务和银行字段不会进入导入报告。"""

    source_row_number: int
    code: str
    short_name: str
    name: str
    contact_person: str
    phone: str
    address: str
    tax_identifier: str
    bank_routing_number: str
    bank_name: str
    bank_account: str
    service_description: str
    english_name: str
    english_address: str


@dataclass(frozen=True, slots=True)
class LegacyMasterDataPackage:
    """正式导入客户与供应商所需的不可变规范包。"""

    source_manifest_sha256: str
    customers: tuple[LegacyCustomerRecord, ...]
    suppliers: tuple[LegacySupplierRecord, ...]


def map_legacy_master_data(raw_directory: Path) -> LegacyMasterDataPackage:
    """验证原始包并映射客户和供应商，不写数据库或猜测缺失字段。"""
    root = _real_directory(raw_directory, label="原始迁移包")
    manifest_path = root / MANIFEST_FILENAME
    manifest = _read_json(manifest_path, maximum_bytes=MAX_MANIFEST_BYTES, label="原始清单")
    manifest_object = _object(
        manifest,
        label="原始清单",
        required={
            "schema_version",
            "extracted_at",
            "contains_real_business_data",
            "restricted_data_included",
            "datasets",
        },
    )
    if manifest_object["schema_version"] != RAW_SCHEMA_VERSION:
        raise LegacyMasterDataPackageError("原始清单版本不受支持。")
    if manifest_object["contains_real_business_data"] is not True:
        raise LegacyMasterDataPackageError("原始清单缺少真实业务数据声明。")
    if manifest_object["restricted_data_included"] is not True:
        raise LegacyMasterDataPackageError("客户和供应商映射要求显式提取受限数据。")
    datasets = _dataset_manifest(manifest_object["datasets"])
    customer_rows = _read_dataset(root, datasets, "clients")
    supplier_rows = _read_dataset(root, datasets, "suppliers")
    customers = tuple(_map_customer(row_number, cells) for row_number, cells in customer_rows)
    suppliers = tuple(_map_supplier(row_number, cells) for row_number, cells in supplier_rows)
    _validate_unique(customers, label="客户")
    _validate_unique(suppliers, label="供应商")
    return LegacyMasterDataPackage(
        source_manifest_sha256=_sha256(manifest_path),
        customers=customers,
        suppliers=suppliers,
    )


def write_legacy_master_data_package(package: LegacyMasterDataPackage, output: Path) -> None:
    """独占并原子写入规范包，禁止覆盖已用于复核的迁移证据。"""
    target = _new_json_target(output)
    payload = {
        "schema_version": MASTER_DATA_SCHEMA_VERSION,
        "source": {
            "raw_manifest_sha256": package.source_manifest_sha256,
            "datasets": {"clients": len(package.customers), "suppliers": len(package.suppliers)},
        },
        "customers": [asdict(item) for item in package.customers],
        "suppliers": [asdict(item) for item in package.suppliers],
    }
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if len(encoded) > MAX_PACKAGE_BYTES:
        raise LegacyMasterDataPackageError("规范主数据包超过 4 MiB 上限。")
    temporary: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=".pms-master-data-", dir=target.parent)
        os.close(descriptor)
        temporary = Path(name)
        temporary.write_bytes(encoded)
        os.replace(temporary, target)
    except OSError as error:
        raise LegacyMasterDataPackageError("无法安全写入规范主数据包。") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_legacy_master_data_package(path: Path) -> LegacyMasterDataPackage:
    """严格加载规范包；未知字段、类型漂移和重复业务键均会失败。"""
    payload = _read_json(path, maximum_bytes=MAX_PACKAGE_BYTES, label="规范主数据包")
    root = _object(
        payload,
        label="规范主数据包",
        required={"schema_version", "source", "customers", "suppliers"},
    )
    if root["schema_version"] != MASTER_DATA_SCHEMA_VERSION:
        raise LegacyMasterDataPackageError("规范主数据包版本不受支持。")
    source = _object(root["source"], label="source", required={"raw_manifest_sha256", "datasets"})
    source_hash = _sha256_text(source["raw_manifest_sha256"], "source.raw_manifest_sha256")
    counts = _object(source["datasets"], label="source.datasets", required={"clients", "suppliers"})
    customer_items = _array(root["customers"], "customers")
    supplier_items = _array(root["suppliers"], "suppliers")
    if counts["clients"] != len(customer_items) or counts["suppliers"] != len(supplier_items):
        raise LegacyMasterDataPackageError("规范包记录数与来源摘要不一致。")
    customers = tuple(_parse_customer(item) for item in customer_items)
    suppliers = tuple(_parse_supplier(item) for item in supplier_items)
    _validate_unique(customers, label="客户")
    _validate_unique(suppliers, label="供应商")
    return LegacyMasterDataPackage(source_hash, customers, suppliers)


def _map_customer(source_row_number: int, cells: tuple[str, ...]) -> LegacyCustomerRecord:
    if len(cells) != 8:
        raise LegacyMasterDataPackageError("客户原始列数不正确。")
    short_name, name, tax_id, address, phone, bank_name, bank_account, routing = cells
    _required(short_name, dataset="clients", row=source_row_number)
    _required(name, dataset="clients", row=source_row_number)
    return LegacyCustomerRecord(
        source_row_number,
        f"LEG-C-{source_row_number:05d}",
        short_name,
        name,
        tax_id,
        address,
        phone,
        bank_name,
        bank_account,
        routing,
    )


def _map_supplier(source_row_number: int, cells: tuple[str, ...]) -> LegacySupplierRecord:
    if len(cells) != 12:
        raise LegacyMasterDataPackageError("供应商原始列数不正确。")
    short_name, name, contact, phone, address, tax_id, routing, bank, account, service, en, ea = (
        cells
    )
    _required(short_name, dataset="suppliers", row=source_row_number)
    _required(name, dataset="suppliers", row=source_row_number)
    return LegacySupplierRecord(
        source_row_number,
        f"LEG-S-{source_row_number:05d}",
        short_name,
        name,
        contact,
        phone,
        address,
        tax_id,
        routing,
        bank,
        account,
        service,
        en,
        ea,
    )


def _dataset_manifest(value: object) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for raw in _array(value, "datasets"):
        item = _object(
            raw,
            label="datasets[]",
            required={
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
        dataset_id = _text(item["dataset_id"], "datasets[].dataset_id")
        if dataset_id in result:
            raise LegacyMasterDataPackageError("原始清单包含重复数据集。")
        result[dataset_id] = item
    return result


def _read_dataset(
    root: Path, manifest: dict[str, dict[str, object]], dataset_id: str
) -> tuple[tuple[int, tuple[str, ...]], ...]:
    item = manifest.get(dataset_id)
    if item is None:
        raise LegacyMasterDataPackageError(f"原始清单缺少数据集 {dataset_id}。")
    spec = next(value for value in DEFAULT_LEGACY_DATASETS if value.dataset_id == dataset_id)
    headers = tuple(
        _text(value, f"{dataset_id}.headers") for value in _array(item["headers"], "headers")
    )
    if headers != spec.expected_headers:
        raise LegacyMasterDataPackageError(f"数据集 {dataset_id} 表头与映射版本不一致。")
    output_file = _text(item["output_file"], f"{dataset_id}.output_file")
    if output_file != f"{dataset_id}.jsonl":
        raise LegacyMasterDataPackageError(f"数据集 {dataset_id} 输出文件名不安全。")
    expected_count = item["record_count"]
    if (
        not isinstance(expected_count, int)
        or isinstance(expected_count, bool)
        or expected_count < 0
    ):
        raise LegacyMasterDataPackageError(f"数据集 {dataset_id} 记录数无效。")
    path = _ordinary_file(root / output_file, maximum_bytes=MAX_DATASET_BYTES, label=dataset_id)
    rows: list[tuple[int, tuple[str, ...]]] = []
    try:
        with path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LegacyMasterDataPackageError(
                        f"数据集 {dataset_id} 第 {line_number} 个记录不是有效 JSON。"
                    ) from error
                row = _object(
                    raw,
                    label=f"{dataset_id}[]",
                    required={"source_row_number", "cells"},
                )
                source_row = row["source_row_number"]
                if (
                    not isinstance(source_row, int)
                    or isinstance(source_row, bool)
                    or source_row < 2
                ):
                    raise LegacyMasterDataPackageError(f"数据集 {dataset_id} 来源行号无效。")
                cells = tuple(
                    _cell_text(cell, dataset=dataset_id, row=source_row)
                    for cell in _array(row["cells"], f"{dataset_id}.cells")
                )
                rows.append((source_row, cells))
    except OSError as error:
        raise LegacyMasterDataPackageError(f"无法读取数据集 {dataset_id}。") from error
    if len(rows) != expected_count or len({row[0] for row in rows}) != len(rows):
        raise LegacyMasterDataPackageError(f"数据集 {dataset_id} 行数或来源行号不一致。")
    return tuple(rows)


def _cell_text(value: object, *, dataset: str, row: int) -> str:
    if value is None:
        return ""
    cell = _object(value, label=f"{dataset}.cells[]", required={"type", "value"})
    cell_type = cell["type"]
    raw = cell["value"]
    if cell_type not in {"text", "number"} or not isinstance(raw, str):
        raise LegacyMasterDataPackageError(
            f"数据集 {dataset} 来源第 {row} 行包含不能映射为文本的类型。"
        )
    return " ".join(unicodedata.normalize("NFKC", raw).split())


def _parse_customer(value: object) -> LegacyCustomerRecord:
    fields = tuple(LegacyCustomerRecord.__dataclass_fields__)
    item = _object(value, label="customers[]", required=set(fields))
    return LegacyCustomerRecord(
        source_row_number=_positive_row(item["source_row_number"], "customers"),
        **{field: _text(item[field], f"customers[].{field}") for field in fields[1:]},
    )


def _parse_supplier(value: object) -> LegacySupplierRecord:
    fields = tuple(LegacySupplierRecord.__dataclass_fields__)
    item = _object(value, label="suppliers[]", required=set(fields))
    return LegacySupplierRecord(
        source_row_number=_positive_row(item["source_row_number"], "suppliers"),
        **{field: _text(item[field], f"suppliers[].{field}") for field in fields[1:]},
    )


def _validate_unique(records: tuple[Any, ...], *, label: str) -> None:
    if not records:
        raise LegacyMasterDataPackageError(f"{label}记录不能为空。")
    for field in ("code", "short_name", "name"):
        keys = [cast(str, getattr(item, field)).casefold() for item in records]
        if len(set(keys)) != len(keys):
            raise LegacyMasterDataPackageError(f"{label}{field}存在重复，必须人工复核。")


def _required(value: str, *, dataset: str, row: int) -> None:
    if not value:
        raise LegacyMasterDataPackageError(f"数据集 {dataset} 来源第 {row} 行缺少必填字段。")


def _read_json(path: Path, *, maximum_bytes: int, label: str) -> object:
    ordinary = _ordinary_file(path, maximum_bytes=maximum_bytes, label=label)
    try:
        return json.loads(ordinary.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LegacyMasterDataPackageError(f"{label}不是有效 UTF-8 JSON。") from error


def _ordinary_file(path: Path, *, maximum_bytes: int, label: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise LegacyMasterDataPackageError(f"{label}必须是普通文件。")
    try:
        resolved = path.resolve(strict=True)
        size = resolved.stat().st_size
    except OSError as error:
        raise LegacyMasterDataPackageError(f"{label}无法读取。") from error
    if size > maximum_bytes:
        raise LegacyMasterDataPackageError(f"{label}超过大小上限。")
    return resolved


def _real_directory(path: Path, *, label: str) -> Path:
    if path.is_symlink():
        raise LegacyMasterDataPackageError(f"{label}不能是符号链接。")
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise LegacyMasterDataPackageError(f"{label}不存在。") from error
    if not resolved.is_dir():
        raise LegacyMasterDataPackageError(f"{label}必须是目录。")
    return resolved


def _new_json_target(path: Path) -> Path:
    if path.suffix.lower() != ".json" or path.exists() or path.is_symlink():
        raise LegacyMasterDataPackageError("输出必须是尚不存在的 .json 文件。")
    try:
        parent = path.parent.resolve(strict=True)
    except OSError as error:
        raise LegacyMasterDataPackageError("输出父目录不存在。") from error
    if not parent.is_dir() or path.parent.is_symlink():
        raise LegacyMasterDataPackageError("输出父路径必须是真实目录。")
    return parent / path.name


def _object(value: object, *, label: str, required: set[str]) -> dict[str, object]:
    if (
        not isinstance(value, dict)
        or set(value) != required
        or not all(isinstance(key, str) for key in value)
    ):
        raise LegacyMasterDataPackageError(f"{label}字段集合不正确。")
    return cast(dict[str, object], value)


def _array(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise LegacyMasterDataPackageError(f"{label}必须是数组。")
    return cast(list[object], value)


def _text(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise LegacyMasterDataPackageError(f"{label}必须是文本。")
    return value


def _positive_row(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 2:
        raise LegacyMasterDataPackageError(f"{label}来源行号无效。")
    return value


def _sha256_text(value: object, label: str) -> str:
    text = _text(value, label)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise LegacyMasterDataPackageError(f"{label}不是 SHA-256。")
    return text


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    except OSError as error:
        raise LegacyMasterDataPackageError("无法计算原始清单摘要。") from error
    return digest.hexdigest()
