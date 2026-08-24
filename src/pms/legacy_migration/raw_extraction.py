"""从旧 PMS 核心工作簿生成不改变业务含义的原始迁移包。

本模块只负责读取经过白名单声明的旧数据源，并把每个单元格连同类型、
来源行号和源文件摘要写入受控目录。它不解释项目、订单或付款规则，也不
写入新系统数据库；字段清洗与正式导入必须在后续版本化映射层完成。

旧目录包含宏、公式、真实客户资料和财务信息。读取使用 Calamine 的文件
解析接口，不启动 Excel、不执行 VBA、不计算公式，也不把业务值写入日志。
输出包含真实数据，必须放在 Git 忽略区或仓库外的受控目录。
"""

import hashlib
import json
import math
import os
import re
import tempfile
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path

from python_calamine import CalamineWorkbook

RAW_SCHEMA_VERSION = "pms-legacy-raw-v1"
MANIFEST_FILENAME = "manifest.json"
DATASET_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
type JSON_SCALAR = str | bool | None
type JSON_CELL = dict[str, JSON_SCALAR] | None


class LegacyRawExtractionError(RuntimeError):
    """表示旧源文件或安全输出边界不满足原始提取契约。"""


@dataclass(frozen=True, slots=True)
class LegacyDatasetSpec:
    """声明一个允许读取的旧数据集及其结构指纹。

    `relative_path` 必须是相对于用户明确提供的旧 PMS 根目录的固定路径。
    `expected_headers` 用于阻止误选备份、临时文件或结构已经漂移的工作簿。
    `restricted` 表示数据含身份证、银行卡或财务凭证等高敏感字段，命令行
    必须收到显式授权后才会提取。
    """

    dataset_id: str
    relative_path: Path
    sheet_name: str
    expected_headers: tuple[str, ...]
    restricted: bool = False


@dataclass(frozen=True, slots=True)
class ExtractedDataset:
    """记录单个数据集的非业务值清单信息。"""

    dataset_id: str
    relative_path: str
    sheet_name: str
    headers: tuple[str, ...]
    source_size_bytes: int
    source_sha256: str
    record_count: int
    output_file: str
    restricted: bool


DEFAULT_LEGACY_DATASETS: tuple[LegacyDatasetSpec, ...] = (
    LegacyDatasetSpec(
        dataset_id="clients",
        relative_path=Path("Database/ClientDatabase.xlsb"),
        sheet_name="Sheet1",
        expected_headers=(
            "简称",
            "客户名称",
            "客户税号",
            "客户地址",
            "电话",
            "开户行",
            "账号",
            "行号",
        ),
        restricted=True,
    ),
    LegacyDatasetSpec(
        dataset_id="suppliers",
        relative_path=Path("Database/ProvideDatabase.xlsb"),
        sheet_name="Sheet1",
        expected_headers=(
            "简称",
            "供应商",
            "联系人",
            "电话",
            "地址",
            "税号",
            "银行行号",
            "开户银行",
            "银行账号",
            "服务",
            "英文名",
            "英文地址",
        ),
        restricted=True,
    ),
    LegacyDatasetSpec(
        dataset_id="production_requirements",
        relative_path=Path("Database/Database.xlsb"),
        sheet_name="Sheet1",
        expected_headers=(
            "项目编号",
            "设备机型",
            "部套代号",
            "部套名称",
            "件号/编码",
            "名称",
            "规格/型号",
            "品牌",
            "单位",
            "单台数量",
            "备注",
            "项目开始时间",
            "计划完成时间",
            "物料分类",
            "零件属性",
            "投产台数",
            "投产单位",
            "接单部门",
            "投产数量",
            "请购单号",
        ),
    ),
    LegacyDatasetSpec(
        dataset_id="purchase_orders",
        relative_path=Path("Database/OutsourceDatabase.xlsb"),
        sheet_name="Sheet1",
        expected_headers=(
            "项目编号",
            "设备机型",
            "件号/编码",
            "名称",
            "下单数量",
            "规格/型号",
            "品牌",
            "单位",
            "备注",
            "项目开始时间",
            "计划完成时间",
            "接单部门",
            "请购单号",
            "单价",
            "总价",
            "承接方",
            "订单编号",
            "投产单位",
            "零件属性",
            "物料分类",
        ),
        restricted=True,
    ),
    LegacyDatasetSpec(
        dataset_id="goods_receipts",
        relative_path=Path("Database/GodownInDatabase.xlsb"),
        sheet_name="Sheet1",
        expected_headers=(
            "项目编号",
            "设备机型",
            "件号/编码",
            "名称",
            "下单数量",
            "规格/型号",
            "品牌",
            "单位",
            "备注",
            "项目开始时间",
            "计划完成时间",
            "接单部门",
            "请购单号",
            "单价",
            "总价",
            "承接方",
            "订单编号",
            "投产单位",
            "零件属性",
            "物料分类",
            "入库数量",
            "入库时间",
        ),
        restricted=True,
    ),
    LegacyDatasetSpec(
        dataset_id="goods_issues",
        relative_path=Path("Database/GodownOutDatabase.xlsb"),
        sheet_name="Sheet1",
        expected_headers=(
            "项目编号",
            "设备机型",
            "部套代号",
            "部套名称",
            "件号/编码",
            "名称",
            "规格/型号",
            "品牌",
            "单位",
            "备注",
            "项目开始时间",
            "计划完成时间",
            "物料分类",
            "零件属性",
            "请购单号",
            "接单部门",
            "投产台数",
            "单台数量",
            "下单数量",
            "入库数",
            "领料数量",
            "剩余库存",
            "申请单号",
        ),
        restricted=True,
    ),
    LegacyDatasetSpec(
        dataset_id="payments",
        relative_path=Path("Database/Payment ledger.xlsb"),
        sheet_name="Sheet1",
        expected_headers=(
            "项目编号",
            "承接方",
            "订单编号",
            "订单金额",
            "付款日期",
            "付款金额",
            "备注",
            "付款凭证",
            "备注",
        ),
        restricted=True,
    ),
    LegacyDatasetSpec(
        dataset_id="sales_orders",
        relative_path=Path("Database/Sales ordersDatabase.xlsb"),
        sheet_name="Sheet1",
        expected_headers=(
            "序号",
            "项目编号",
            "销售合同号",
            "客户",
            "产品名称",
            "数量",
            "税率",
            "含税单价",
            "含税总价",
            "预付比例",
            "日期",
            "预付款金额",
            "预付款到账\n日期",
            "销售发票号码",
            "付款1",
            "日期1",
            "付款2",
            "日期2",
            "付款3",
            "日期3",
            "备注",
        ),
        restricted=True,
    ),
    LegacyDatasetSpec(
        dataset_id="purchase_invoices",
        relative_path=Path("Database/billing information.xlsb"),
        sheet_name="Sheet1",
        expected_headers=(
            "项目编号",
            "承接方",
            "订单编号",
            "订单金额",
            "供应商",
            "开票日期",
            "发票号",
            "付款方",
            "提交日期",
        ),
        restricted=True,
    ),
    LegacyDatasetSpec(
        dataset_id="employees",
        relative_path=Path("Database/Employee roster.xlsb"),
        sheet_name="Sheet1",
        expected_headers=(
            "员工编号",
            "员工姓名",
            "身份证号",
            "家庭住址",
            "手机",
            "银行卡号",
            "开户行",
            "在职与否",
            "家庭成员1",
            "称谓1",
            "电话1",
            "家庭成员2",
            "称谓2",
            "电话2",
        ),
        restricted=True,
    ),
    LegacyDatasetSpec(
        dataset_id="expense_claims",
        relative_path=Path("Database/Claom expense.xlsb"),
        sheet_name="Sheet1",
        expected_headers=(
            "报销项目",
            "报销人",
            "报销人银行卡号",
            "报销人开户行",
            "报销分类",
            "报销金额",
            "报销凭证",
            "报销日期",
            "付款日期",
            "付款凭证",
            "备注",
        ),
        restricted=True,
    ),
)


def extract_legacy_raw_package(
    *,
    legacy_root: Path,
    output_directory: Path,
    include_restricted: bool,
    datasets: Sequence[LegacyDatasetSpec] = DEFAULT_LEGACY_DATASETS,
    clock: Callable[[], datetime] | None = None,
) -> tuple[ExtractedDataset, ...]:
    """原子生成旧数据原始包，并返回不含业务值的提取结果。

    Args:
        legacy_root: 旧 PMS 根目录。只允许读取数据集白名单中的相对路径。
        output_directory: 必须尚不存在。函数先在同一父目录写临时目录，全部
            数据集成功并落盘后再原子改名，避免把半份包误认为可导入证据。
        include_restricted: 是否明确允许提取高敏感数据。为 false 时相关数据集
            完全跳过，而不是只做不可靠的字段级遮盖。
        datasets: 受控源定义；参数主要用于虚构夹具测试和未来版本迁移。
        clock: 生成清单时间的可注入 UTC 时钟。

    Returns:
        每个实际提取数据集的来源摘要、记录数和输出文件名。

    Raises:
        LegacyRawExtractionError: 根目录、输出边界、源文件、工作表、表头或
            单元格类型不满足契约。失败不会发布目标目录。
    """
    source_root = _validated_directory(legacy_root, label="旧 PMS 根目录")
    target = _validated_new_output(output_directory)
    selected = tuple(item for item in datasets if include_restricted or not item.restricted)
    if not selected:
        raise LegacyRawExtractionError("没有可提取的数据集。")
    dataset_ids = tuple(item.dataset_id for item in selected)
    if any(DATASET_ID_PATTERN.fullmatch(item) is None for item in dataset_ids):
        raise LegacyRawExtractionError("数据集 ID 必须是安全的小写 snake_case。")
    if len(set(dataset_ids)) != len(dataset_ids):
        raise LegacyRawExtractionError("数据集 ID 不能重复。")

    now = clock or (lambda: datetime.now(UTC))
    with tempfile.TemporaryDirectory(prefix=".pms-legacy-extract-", dir=target.parent) as temporary:
        staging = Path(temporary)
        extracted = tuple(
            _extract_dataset(source_root=source_root, output_directory=staging, spec=spec)
            for spec in selected
        )
        manifest = {
            "schema_version": RAW_SCHEMA_VERSION,
            "extracted_at": _utc_timestamp(now()),
            "contains_real_business_data": True,
            "restricted_data_included": include_restricted,
            "datasets": [_manifest_item(item) for item in extracted],
        }
        _write_json(staging / MANIFEST_FILENAME, manifest)
        os.replace(staging, target)
    return extracted


def _extract_dataset(
    *, source_root: Path, output_directory: Path, spec: LegacyDatasetSpec
) -> ExtractedDataset:
    source = _validated_source(source_root, spec.relative_path)
    output_name = f"{spec.dataset_id}.jsonl"
    output_path = output_directory / output_name
    workbook: CalamineWorkbook | None = None
    try:
        workbook = CalamineWorkbook.from_path(source)
        if spec.sheet_name not in workbook.sheet_names:
            raise LegacyRawExtractionError(
                f"数据集 {spec.dataset_id} 缺少工作表 {spec.sheet_name}。"
            )
        sheet = workbook.get_sheet_by_name(spec.sheet_name)
        rows = iter(sheet.iter_rows())
        header_row = next(rows, None)
        actual_headers = tuple(_header_text(value) for value in (header_row or []))
        if actual_headers != spec.expected_headers:
            raise LegacyRawExtractionError(f"数据集 {spec.dataset_id} 的表头与受控定义不一致。")
        sheet_start = sheet.start
        if sheet_start is None:
            raise LegacyRawExtractionError(f"数据集 {spec.dataset_id} 没有可读取区域。")
        source_start_row = sheet_start[0] + 1
        record_count = _write_rows(
            output_path=output_path,
            rows=rows,
            header_width=len(actual_headers),
            first_data_row=source_start_row + 1,
            dataset_id=spec.dataset_id,
        )
    except LegacyRawExtractionError:
        raise
    except Exception as error:
        raise LegacyRawExtractionError(f"无法安全读取数据集 {spec.dataset_id}。") from error
    finally:
        if workbook is not None:
            workbook.close()

    return ExtractedDataset(
        dataset_id=spec.dataset_id,
        relative_path=spec.relative_path.as_posix(),
        sheet_name=spec.sheet_name,
        headers=spec.expected_headers,
        source_size_bytes=source.stat().st_size,
        source_sha256=_sha256(source),
        record_count=record_count,
        output_file=output_name,
        restricted=spec.restricted,
    )


def _write_rows(
    *,
    output_path: Path,
    rows: Iterator[Sequence[object]],
    header_width: int,
    first_data_row: int,
    dataset_id: str,
) -> int:
    count = 0
    try:
        with output_path.open("x", encoding="utf-8", newline="\n") as stream:
            for source_row_number, row in enumerate(rows, start=first_data_row):
                normalized = [_cell(value, dataset_id=dataset_id) for value in row]
                if len(normalized) < header_width:
                    normalized.extend([None] * (header_width - len(normalized)))
                if len(normalized) > header_width:
                    raise LegacyRawExtractionError(
                        f"数据集 {dataset_id} 第 {source_row_number} 行超过表头列数。"
                    )
                if all(value is None for value in normalized):
                    continue
                record = {"source_row_number": source_row_number, "cells": normalized}
                stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
                stream.write("\n")
                count += 1
    except OSError as error:
        raise LegacyRawExtractionError(f"无法写入数据集 {dataset_id}。") from error
    return count


def _cell(value: object, *, dataset_id: str) -> JSON_CELL:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return {"type": "boolean", "value": value}
    if isinstance(value, str):
        return {"type": "text", "value": value}
    if isinstance(value, int):
        return {"type": "number", "value": str(value)}
    if isinstance(value, float):
        if not math.isfinite(value):
            raise LegacyRawExtractionError(f"数据集 {dataset_id} 包含非有限数值。")
        return {"type": "number", "value": format(Decimal(str(value)), "f")}
    if isinstance(value, datetime):
        return {"type": "datetime", "value": value.isoformat()}
    if isinstance(value, date):
        return {"type": "date", "value": value.isoformat()}
    if isinstance(value, time):
        return {"type": "time", "value": value.isoformat()}
    raise LegacyRawExtractionError(
        f"数据集 {dataset_id} 包含不支持的单元格类型 {type(value).__name__}。"
    )


def _validated_directory(path: Path, *, label: str) -> Path:
    if path.is_symlink():
        raise LegacyRawExtractionError(f"{label}必须是真实目录，不能是符号链接。")
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise LegacyRawExtractionError(f"{label}不存在或无法访问。") from error
    if not resolved.is_dir():
        raise LegacyRawExtractionError(f"{label}必须是真实目录，不能是符号链接。")
    return resolved


def _validated_new_output(path: Path) -> Path:
    if path.exists() or path.is_symlink():
        raise LegacyRawExtractionError("输出目录必须尚不存在，禁止覆盖既有迁移证据。")
    if path.parent.is_symlink():
        raise LegacyRawExtractionError("输出目录的父路径必须是真实目录。")
    try:
        parent = path.parent.resolve(strict=True)
    except OSError as error:
        raise LegacyRawExtractionError("输出目录的父目录不存在或无法访问。") from error
    if not parent.is_dir():
        raise LegacyRawExtractionError("输出目录的父路径必须是真实目录。")
    return parent / path.name


def _validated_source(source_root: Path, relative_path: Path) -> Path:
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise LegacyRawExtractionError("旧数据源路径必须是根目录内的固定相对路径。")
    candidate = source_root / relative_path
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise LegacyRawExtractionError(f"缺少旧数据源 {relative_path.as_posix()}。") from error
    try:
        resolved.relative_to(source_root)
    except ValueError as error:
        raise LegacyRawExtractionError("旧数据源越过了授权根目录。") from error
    if not resolved.is_file() or resolved.is_symlink() or candidate.is_symlink():
        raise LegacyRawExtractionError("旧数据源必须是根目录内的普通文件。")
    return resolved


def _header_text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LegacyRawExtractionError("旧数据表头必须是非空文本。")
    return value.strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    except OSError as error:
        raise LegacyRawExtractionError("无法计算旧数据源摘要。") from error
    return digest.hexdigest()


def _utc_timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise LegacyRawExtractionError("提取清单时钟必须包含时区。")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _manifest_item(item: ExtractedDataset) -> dict[str, object]:
    return {
        "dataset_id": item.dataset_id,
        "relative_path": item.relative_path,
        "sheet_name": item.sheet_name,
        "headers": list(item.headers),
        "source_size_bytes": item.source_size_bytes,
        "source_sha256": item.source_sha256,
        "record_count": item.record_count,
        "output_file": item.output_file,
        "restricted": item.restricted,
    }


def _write_json(path: Path, payload: object) -> None:
    try:
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    except OSError as error:
        raise LegacyRawExtractionError("无法写入原始提取清单。") from error
