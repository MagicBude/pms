"""从旧生产需求原始集选择并映射一条待业务复核的真实业务链。

旧 ``production_requirements`` 并不是“一项目一张 BOM”：同一项目可能
包含多次投产和多个旧请购单号。因此本模块以 ``项目编号 + 请购单号``
分组，只选择字段内部一致、客户可由销售订单精确关联、数量公式成立且
不存在重复物料身份的中等规模批次。

输出是严格的 ``pms-legacy-slice-v2``，状态固定为 ``business_pending``。
它保留旧数据来源行号、部套、零件属性和业务值，必须写入 Git 忽略区。
普通命令输出只显示候选数量和行数；真实内容仅进入用户指定的包与 HTML
复核页。在业务人员签收前，正式迁移命令默认拒绝导入该包。
"""

import hashlib
import html
import json
import os
import tempfile
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import cast

from pms.legacy_migration.master_data_package import (
    LegacyCustomerRecord,
    map_legacy_master_data,
)
from pms.legacy_migration.raw_extraction import (
    DEFAULT_LEGACY_DATASETS,
    MANIFEST_FILENAME,
    RAW_SCHEMA_VERSION,
)
from pms.legacy_migration.schema import parse_legacy_slice_package
from pms.master_data.domain.values import MasterDataValidationError, normalize_code

SLICE_SCHEMA_VERSION = "pms-legacy-slice-v2"
MAX_RAW_FILE_BYTES = 16 * 1024 * 1024


class LegacySliceMappingError(ValueError):
    """表示真实业务链无法在不猜测业务含义的前提下安全映射。"""


@dataclass(frozen=True, slots=True)
class RawRow:
    """保留旧工作表来源行号的只读文本行。"""

    source_row_number: int
    cells: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SliceMappingResult:
    """规范包、复核页所需元数据及不含业务值的统计。"""

    payload: dict[str, object]
    review: dict[str, object]
    eligible_candidate_count: int
    mapped_line_count: int


def map_pending_real_slice(raw_directory: Path) -> SliceMappingResult:
    """选择唯一最佳真实批次并生成待确认包，不写数据库。

    候选限定为 5 至 12 行，优先覆盖更多单位、分类和零件属性，再优先
    行数较多者；若最高分并列则停止，避免输入顺序暗中决定业务样例。
    """
    root = _real_directory(raw_directory)
    manifest, datasets = _load_manifest(root)
    production = _load_dataset(root, datasets, "production_requirements")
    sales = _load_dataset(root, datasets, "sales_orders")
    master_data = map_legacy_master_data(root)
    customers = _customer_lookup(master_data.customers)
    sales_customers: dict[str, set[str]] = defaultdict(set)
    for row in sales:
        project_number, customer_name = row.cells[1], row.cells[3]
        if project_number and customer_name:
            sales_customers[project_number].add(customer_name)

    grouped: dict[tuple[str, str], list[RawRow]] = defaultdict(list)
    for row in production:
        grouped[(row.cells[0], row.cells[19])].append(row)
    candidates: list[tuple[tuple[int, int], list[RawRow], LegacyCustomerRecord]] = []
    for (project_number, request_number), rows in grouped.items():
        customer = _eligible_customer(
            project_number=project_number,
            request_number=request_number,
            rows=rows,
            sales_customers=sales_customers,
            customers=customers,
        )
        if customer is None or not 5 <= len(rows) <= 12:
            continue
        if not _eligible_rows(rows):
            continue
        coverage = sum(len({row.cells[index].casefold() for row in rows}) for index in (8, 13, 14))
        candidates.append(((coverage, len(rows)), rows, customer))
    if not candidates:
        raise LegacySliceMappingError("没有满足真实案例完整性规则的候选批次。")
    best_score = max(item[0] for item in candidates)
    best = [item for item in candidates if item[0] == best_score]
    if len(best) != 1:
        raise LegacySliceMappingError("最高评分真实候选不唯一，需要业务人员先指定案例。")
    _score, rows, customer = best[0]
    rows = sorted(rows, key=lambda item: item.source_row_number)
    payload, review = _build_payload(
        rows=rows,
        customer=customer,
        source_manifest_sha256=_sha256(root / MANIFEST_FILENAME),
        extracted_at=cast(str, manifest["extracted_at"]),
    )
    # 复用正式 schema 做最后一道交叉引用和 Decimal/日期验证。这里失败
    # 表示映射器生成了自身无法导入的包，不能把半成品发布给使用者。
    parse_legacy_slice_package(payload)
    return SliceMappingResult(
        payload=payload,
        review=review,
        eligible_candidate_count=len(candidates),
        mapped_line_count=len(rows),
    )


def write_pending_slice_outputs(
    result: SliceMappingResult, *, package_path: Path, review_path: Path
) -> None:
    """独占写入真实 JSON 包与 HTML 复核页，任一目标已存在即拒绝覆盖。"""
    package_target = _new_target(package_path, ".json")
    review_target = _new_target(review_path, ".html")
    package_bytes = (json.dumps(result.payload, ensure_ascii=False, indent=2) + "\n").encode(
        "utf-8"
    )
    review_bytes = _review_html(result.review).encode("utf-8")
    _atomic_write(package_target, package_bytes)
    try:
        _atomic_write(review_target, review_bytes)
    except LegacySliceMappingError:
        package_target.unlink(missing_ok=True)
        raise


def _build_payload(
    *,
    rows: list[RawRow],
    customer: LegacyCustomerRecord,
    source_manifest_sha256: str,
    extracted_at: str,
) -> tuple[dict[str, object], dict[str, object]]:
    first = rows[0].cells
    project_number = normalize_code(first[0], field_name="旧项目编号")
    production_units = _positive_integer(first[15], label="投产台数")
    start_date = _date(first[11], label="项目开始时间")
    planned_date = _date(first[12], label="计划完成时间")
    if planned_date < start_date:
        raise LegacySliceMappingError("真实候选的计划完成日期早于开始日期。")
    unit_codes = {name: _stable_code("LEG-U", name, 10) for name in {row.cells[8] for row in rows}}
    category_codes = {
        name: _stable_code("LEG-CAT", name, 10) for name in {row.cells[13] for row in rows}
    }
    materials: list[dict[str, object]] = []
    bom_rows: list[dict[str, object]] = []
    purchase_candidates: list[dict[str, object]] = []
    review_rows: list[dict[str, object]] = []
    for row in rows:
        cells = row.cells
        identity = _material_identity(cells)
        material_code = _stable_code("LEG-M", "\x1f".join(identity), 16)
        quantity = _positive_decimal(cells[9], label="单台数量")
        required_quantity = _positive_decimal(cells[18], label="投产数量")
        materials.append(
            {
                "code": material_code,
                "name": cells[5],
                "specification": cells[6],
                "brand": cells[7],
                "part_attribute": cells[14],
                "unit_code": unit_codes[cells[8]],
                "category_code": category_codes[cells[13]],
                # 该原始集本身是已经生成请购单号的需求库，加工件和采购件
                # 都需要进入后续采购/外协处理，不能把“加工件”误判为自制。
                "procurement_required": True,
            }
        )
        bom_rows.append(
            {
                "source_row_number": row.source_row_number,
                "level_path": "",
                "assembly_code": cells[2],
                "assembly_name": cells[3],
                "material_code": material_code,
                "material_name": cells[5],
                "specification": cells[6],
                "brand": cells[7],
                "quantity_per_unit": str(quantity),
                "unit_code": unit_codes[cells[8]],
                "remark": cells[10],
            }
        )
        purchase_candidates.append(
            {
                "source_row_number": row.source_row_number,
                "material_code": material_code,
                "requested_quantity": str(required_quantity),
                "unit_code": unit_codes[cells[8]],
            }
        )
        review_rows.append(
            {
                "source_row_number": row.source_row_number,
                "assembly_code": cells[2],
                "assembly_name": cells[3],
                "legacy_material_code": cells[4],
                "material_name": cells[5],
                "specification": cells[6],
                "brand": cells[7],
                "unit": cells[8],
                "quantity_per_unit": str(quantity),
                "production_quantity": str(required_quantity),
                "category": cells[13],
                "part_attribute": cells[14],
                "remark": cells[10],
            }
        )
    sample_hash = hashlib.sha256(f"{project_number}\x1f{first[19]}".encode()).hexdigest()[:16]
    payload: dict[str, object] = {
        "schema_version": SLICE_SCHEMA_VERSION,
        "sample": {
            "id": f"real-pending-{sample_hash}",
            "kind": "business_pending",
            "confirmed_by": None,
        },
        "master_data": {
            "customer": {"code": customer.code, "name": customer.name},
            "units": [
                {"code": code, "name": name}
                for name, code in sorted(unit_codes.items(), key=lambda item: item[1])
            ],
            "categories": [
                {"code": code, "name": name}
                for name, code in sorted(category_codes.items(), key=lambda item: item[1])
            ],
            "materials": materials,
        },
        "project": {
            "number": project_number,
            "customer_code": customer.code,
            "device_model": first[1],
            "owner_username": "admin",
            "start_date": start_date.isoformat(),
            "planned_completion_date": planned_date.isoformat(),
        },
        "bom": {"version_number": 1, "rows": bom_rows},
        "production": {
            "production_units": production_units,
            # 旧“投产单位”保存的是组织名称，不是台数单位。新字段明确
            # 表达“投产台数”的计量单位，因此提出“台”供业务人员复核。
            "production_unit": "台",
            "receiving_department": first[17],
        },
        "legacy_purchase_candidates": purchase_candidates,
        "accepted_differences": [],
    }
    review: dict[str, object] = {
        "source_manifest_sha256": source_manifest_sha256,
        "source_extracted_at": extracted_at,
        "sample_id": f"real-pending-{sample_hash}",
        "project_number": project_number,
        "legacy_request_number": first[19],
        "customer_short_name": customer.short_name,
        "customer_name": customer.name,
        "device_model": first[1],
        "start_date": start_date.isoformat(),
        "planned_completion_date": planned_date.isoformat(),
        "production_units": production_units,
        "legacy_production_unit": first[16],
        "proposed_count_unit": "台",
        "receiving_department": first[17],
        "rows": review_rows,
        "questions": (
            "请确认销售订单中的客户与该项目确属同一客户。",
            "请确认旧“投产单位”是组织名称，而投产台数的计量单位应为“台”。",
            "请确认加工件与采购件都应进入采购/外协请购，而不是排除加工件。",
            "请确认所列 10 行、部套、单台数量和投产数量与旧请购单一致。",
        ),
    }
    return payload, review


def _eligible_customer(
    *,
    project_number: str,
    request_number: str,
    rows: list[RawRow],
    sales_customers: dict[str, set[str]],
    customers: dict[str, tuple[LegacyCustomerRecord, ...]],
) -> LegacyCustomerRecord | None:
    if not project_number or not request_number or not _stable_group(rows):
        return None
    try:
        normalize_code(project_number, field_name="旧项目编号")
    except MasterDataValidationError:
        return None
    labels = sales_customers.get(project_number, set())
    if len(labels) != 1:
        return None
    matched = customers.get(next(iter(labels)).casefold(), ())
    return matched[0] if len(matched) == 1 else None


def _stable_group(rows: list[RawRow]) -> bool:
    for index in (0, 1, 11, 12, 15, 16, 17, 19):
        values = {row.cells[index] for row in rows}
        if len(values) != 1 or not next(iter(values)):
            return False
    return True


def _eligible_rows(rows: list[RawRow]) -> bool:
    identities: list[tuple[str, ...]] = []
    try:
        production_units = _positive_integer(rows[0].cells[15], label="投产台数")
        start = _date(rows[0].cells[11], label="项目开始时间")
        planned = _date(rows[0].cells[12], label="计划完成时间")
        if planned < start:
            return False
        for row in rows:
            cells = row.cells
            if any(not cells[index] for index in (5, 8, 13, 14)):
                return False
            per_unit = _positive_decimal(cells[9], label="单台数量")
            total = _positive_decimal(cells[18], label="投产数量")
            if per_unit * production_units != total:
                return False
            identities.append(_material_identity(cells))
    except LegacySliceMappingError:
        return False
    return len(set(identities)) == len(identities)


def _material_identity(cells: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(_normalized(cells[index]).casefold() for index in (4, 5, 6, 7, 8, 13, 14))


def _customer_lookup(
    customers: tuple[LegacyCustomerRecord, ...],
) -> dict[str, tuple[LegacyCustomerRecord, ...]]:
    grouped: dict[str, list[LegacyCustomerRecord]] = defaultdict(list)
    for customer in customers:
        grouped[customer.short_name.casefold()].append(customer)
        grouped[customer.name.casefold()].append(customer)
    return {key: tuple(value) for key, value in grouped.items()}


def _load_manifest(root: Path) -> tuple[dict[str, object], dict[str, dict[str, object]]]:
    path = root / MANIFEST_FILENAME
    payload = _read_json(path, maximum_bytes=1024 * 1024)
    required = {
        "schema_version",
        "extracted_at",
        "contains_real_business_data",
        "restricted_data_included",
        "datasets",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise LegacySliceMappingError("原始清单字段集合不正确。")
    manifest = cast(dict[str, object], payload)
    if manifest["schema_version"] != RAW_SCHEMA_VERSION:
        raise LegacySliceMappingError("原始清单版本不受支持。")
    datasets: dict[str, dict[str, object]] = {}
    raw_datasets = manifest["datasets"]
    if not isinstance(raw_datasets, list):
        raise LegacySliceMappingError("原始清单 datasets 必须是数组。")
    for value in raw_datasets:
        if not isinstance(value, dict) or not isinstance(value.get("dataset_id"), str):
            raise LegacySliceMappingError("原始清单数据集条目无效。")
        item = cast(dict[str, object], value)
        datasets[cast(str, item["dataset_id"])] = item
    return manifest, datasets


def _load_dataset(
    root: Path, datasets: dict[str, dict[str, object]], dataset_id: str
) -> tuple[RawRow, ...]:
    item = datasets.get(dataset_id)
    if item is None:
        raise LegacySliceMappingError(f"原始清单缺少 {dataset_id}。")
    spec = next(value for value in DEFAULT_LEGACY_DATASETS if value.dataset_id == dataset_id)
    headers = item.get("headers")
    if headers != list(spec.expected_headers):
        raise LegacySliceMappingError(f"数据集 {dataset_id} 表头与映射版本不一致。")
    filename = item.get("output_file")
    expected_count = item.get("record_count")
    if (
        not isinstance(filename, str)
        or filename != f"{dataset_id}.jsonl"
        or not isinstance(expected_count, int)
    ):
        raise LegacySliceMappingError(f"数据集 {dataset_id} 清单元数据无效。")
    path = _ordinary_file(root / filename, maximum_bytes=MAX_RAW_FILE_BYTES)
    rows: list[RawRow] = []
    try:
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            raw = json.loads(line)
            if not isinstance(raw, dict) or set(raw) != {"source_row_number", "cells"}:
                raise LegacySliceMappingError(
                    f"数据集 {dataset_id} 第 {line_number} 个记录结构无效。"
                )
            source_row = raw["source_row_number"]
            cells = raw["cells"]
            if (
                not isinstance(source_row, int)
                or isinstance(source_row, bool)
                or not isinstance(cells, list)
                or len(cells) != len(spec.expected_headers)
            ):
                raise LegacySliceMappingError(f"数据集 {dataset_id} 行号或列数无效。")
            rows.append(
                RawRow(
                    source_row_number=source_row,
                    cells=tuple(_cell_text(cell, dataset_id, source_row) for cell in cells),
                )
            )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LegacySliceMappingError(f"无法安全读取数据集 {dataset_id}。") from error
    if len(rows) != expected_count or len({row.source_row_number for row in rows}) != len(rows):
        raise LegacySliceMappingError(f"数据集 {dataset_id} 行数或来源行号不一致。")
    return tuple(rows)


def _cell_text(value: object, dataset: str, source_row: int) -> str:
    if value is None:
        return ""
    if not isinstance(value, dict) or set(value) != {"type", "value"}:
        raise LegacySliceMappingError(f"数据集 {dataset} 来源第 {source_row} 行单元格无效。")
    cell_type = value["type"]
    raw = value["value"]
    if cell_type not in {"text", "number", "date", "datetime"} or not isinstance(raw, str):
        raise LegacySliceMappingError(f"数据集 {dataset} 来源第 {source_row} 行包含不支持的类型。")
    return _normalized(raw)


def _positive_decimal(value: str, *, label: str) -> Decimal:
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise LegacySliceMappingError(f"{label}不是有效十进制数。") from error
    if not result.is_finite() or result <= 0:
        raise LegacySliceMappingError(f"{label}必须大于零。")
    return result


def _positive_integer(value: str, *, label: str) -> int:
    decimal = _positive_decimal(value, label=label)
    if decimal != decimal.to_integral_value():
        raise LegacySliceMappingError(f"{label}必须是整数。")
    return int(decimal)


def _date(value: str, *, label: str) -> date:
    try:
        return date.fromisoformat(value[:10])
    except ValueError as error:
        raise LegacySliceMappingError(f"{label}不是有效日期。") from error


def _stable_code(prefix: str, value: str, length: int) -> str:
    digest = hashlib.sha256(_normalized(value).casefold().encode()).hexdigest()
    return f"{prefix}-{digest[:length].upper()}"


def _normalized(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


def _review_html(review: dict[str, object]) -> str:
    def esc(value: object) -> str:
        return html.escape(str(value))

    rows = cast(list[dict[str, object]], review["rows"])
    questions = cast(tuple[str, ...], review["questions"])
    body_rows = "".join(
        "<tr>"
        + "".join(
            f"<td>{esc(row[key])}</td>"
            for key in (
                "source_row_number",
                "assembly_code",
                "assembly_name",
                "legacy_material_code",
                "material_name",
                "specification",
                "brand",
                "unit",
                "quantity_per_unit",
                "production_quantity",
                "category",
                "part_attribute",
                "remark",
            )
        )
        + "</tr>"
        for row in rows
    )
    question_items = "".join(f"<li>{esc(item)}</li>" for item in questions)
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>PMS 真实案例待复核</title>
<style>body{{font:14px/1.6 system-ui,'Microsoft YaHei';margin:28px;color:#172033}}h1{{margin-bottom:4px}}.warn{{background:#fff4dc;padding:12px;border-radius:8px}}dl{{display:grid;grid-template-columns:160px 1fr;max-width:900px}}dt,dd{{padding:5px;margin:0;border-bottom:1px solid #eee}}table{{border-collapse:collapse;min-width:1600px}}th,td{{border:1px solid #ddd;padding:7px;white-space:nowrap}}th{{background:#f4f6fa}}.scroll{{overflow:auto}}</style></head><body>
<h1>真实项目/BOM/投产/请购案例</h1><p class="warn">状态：待业务复核。此页包含真实业务资料，只能在本机受控环境查看，不得提交 Git 或外传。</p>
<dl><dt>案例 ID</dt><dd>{esc(review["sample_id"])}</dd><dt>项目编号</dt><dd>{esc(review["project_number"])}</dd><dt>旧请购单号</dt><dd>{esc(review["legacy_request_number"])}</dd><dt>客户</dt><dd>{esc(review["customer_short_name"])} / {esc(review["customer_name"])}</dd><dt>设备机型</dt><dd>{esc(review["device_model"])}</dd><dt>项目日期</dt><dd>{esc(review["start_date"])} 至 {esc(review["planned_completion_date"])}</dd><dt>投产台数</dt><dd>{esc(review["production_units"])}</dd><dt>旧投产单位</dt><dd>{esc(review["legacy_production_unit"])}</dd><dt>建议台数单位</dt><dd>{esc(review["proposed_count_unit"])}</dd><dt>接单部门</dt><dd>{esc(review["receiving_department"])}</dd></dl>
<h2>需要你确认</h2><ol>{question_items}</ol><h2>BOM 与请购候选（{len(rows)} 行）</h2>
<div class="scroll"><table><thead><tr><th>旧来源行</th><th>部套代号</th><th>部套名称</th><th>旧件号</th><th>名称</th><th>规格</th><th>品牌</th><th>单位</th><th>单台数量</th><th>投产数量</th><th>分类</th><th>零件属性</th><th>备注</th></tr></thead><tbody>{body_rows}</tbody></table></div>
<p>原始清单 SHA-256：{esc(review["source_manifest_sha256"])}；提取时间：{esc(review["source_extracted_at"])}</p></body></html>"""


def _real_directory(path: Path) -> Path:
    if path.is_symlink():
        raise LegacySliceMappingError("原始迁移包不能是符号链接。")
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise LegacySliceMappingError("原始迁移包不存在。") from error
    if not resolved.is_dir():
        raise LegacySliceMappingError("原始迁移包必须是目录。")
    return resolved


def _ordinary_file(path: Path, *, maximum_bytes: int) -> Path:
    if path.is_symlink() or not path.is_file():
        raise LegacySliceMappingError("原始数据文件必须是普通文件。")
    resolved = path.resolve(strict=True)
    if resolved.stat().st_size > maximum_bytes:
        raise LegacySliceMappingError("原始数据文件超过大小上限。")
    return resolved


def _read_json(path: Path, *, maximum_bytes: int) -> object:
    ordinary = _ordinary_file(path, maximum_bytes=maximum_bytes)
    try:
        return json.loads(ordinary.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LegacySliceMappingError("原始清单不是有效 UTF-8 JSON。") from error


def _new_target(path: Path, suffix: str) -> Path:
    if path.suffix.lower() != suffix or path.exists() or path.is_symlink():
        raise LegacySliceMappingError(f"输出必须是尚不存在的 {suffix} 文件。")
    try:
        parent = path.parent.resolve(strict=True)
    except OSError as error:
        raise LegacySliceMappingError("输出父目录不存在。") from error
    if not parent.is_dir() or path.parent.is_symlink():
        raise LegacySliceMappingError("输出父路径必须是真实目录。")
    return parent / path.name


def _atomic_write(path: Path, content: bytes) -> None:
    temporary: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=".pms-real-slice-", dir=path.parent)
        os.close(descriptor)
        temporary = Path(name)
        temporary.write_bytes(content)
        os.replace(temporary, path)
    except OSError as error:
        raise LegacySliceMappingError("无法安全写入真实案例输出。") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
