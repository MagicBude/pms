"""真实项目/BOM/投产/请购候选映射的结构与安全输出测试。"""

import json
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command

from pms.legacy_migration.raw_extraction import DEFAULT_LEGACY_DATASETS
from pms.legacy_migration.schema import load_legacy_slice_package
from pms.legacy_migration.slice_mapping import (
    LegacySliceMappingError,
    map_pending_real_slice,
    write_pending_slice_outputs,
)


def test_maps_one_consistent_request_group_and_preserves_source_rows(tmp_path: Path) -> None:
    """一个旧请购号形成一个批次，部套、零件属性和原始行号均不丢失。"""
    raw = make_raw_slice_package(tmp_path)
    result = map_pending_real_slice(raw)
    package_path = tmp_path / "real-slice.json"
    review_path = tmp_path / "real-slice.html"
    write_pending_slice_outputs(result, package_path=package_path, review_path=review_path)
    package = load_legacy_slice_package(package_path)

    assert package.sample.kind == "business_pending"
    assert package.project.customer_code == "LEG-C-00002"
    assert [row.source_row_number for row in package.bom.rows] == [20, 21, 22, 23, 24]
    assert {item.part_attribute for item in package.materials} == {"加工件", "采购件"}
    assert all(item.procurement_required for item in package.materials)
    assert package.production.production_unit == "台"
    review = review_path.read_text(encoding="utf-8")
    assert "&lt;测试物料1&gt;" in review
    assert "状态：待业务复核" in review


def test_formula_difference_removes_candidate_instead_of_accepting_it(tmp_path: Path) -> None:
    """旧投产数量不等于单台数量乘台数时必须停止，不能自动签收差异。"""
    raw = make_raw_slice_package(tmp_path)
    rows = (raw / "production_requirements.jsonl").read_text(encoding="utf-8").splitlines()
    changed = json.loads(rows[0])
    changed["cells"][18] = {"type": "number", "value": "999"}
    rows[0] = json.dumps(changed, ensure_ascii=False)
    (raw / "production_requirements.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")

    with pytest.raises(LegacySliceMappingError, match="没有满足"):
        map_pending_real_slice(raw)


def test_mapping_command_does_not_echo_real_business_values(tmp_path: Path) -> None:
    """命令行只报告候选和行数，真实内容仅进入受控输出文件。"""
    raw = make_raw_slice_package(tmp_path)
    stdout = StringIO()
    call_command(
        "map_legacy_slice",
        raw=raw,
        output=tmp_path / "output.json",
        review=tmp_path / "review.html",
        stdout=stdout,
        no_color=True,
    )

    message = stdout.getvalue()
    assert "合格候选 1" in message
    assert "选定明细 5 行" in message
    assert "TEST-2026-001" not in message
    assert "虚构客户" not in message


def make_raw_slice_package(tmp_path: Path) -> Path:
    """建立完全虚构的四数据集原始包，供单元和命令测试复用。"""
    root = tmp_path / "raw"
    root.mkdir()
    datasets: list[dict[str, object]] = []
    clients = [["测试客户", "虚构客户有限公司", "", "虚构地址", "", "", "", ""]]
    suppliers = [["测试供方", "虚构供应商有限公司", "", "", "虚构地址", "", "", "", "", "", "", ""]]
    sales_row = [""] * 21
    sales_row[0] = "1"
    sales_row[1] = "TEST-2026-001"
    sales_row[3] = "测试客户"
    production: list[list[str]] = []
    for offset in range(5):
        quantity = offset + 1
        row = [""] * 20
        row[0] = "TEST-2026-001"
        row[1] = "虚构设备"
        row[2] = f"ASM-{offset + 1}"
        row[3] = f"虚构部套{offset + 1}"
        row[4] = f"OLD-{offset + 1}"
        row[5] = "<测试物料1>" if offset == 0 else f"测试物料{offset + 1}"
        row[6] = f"规格{offset + 1}"
        row[7] = "测试品牌"
        row[8] = "件"
        row[9] = str(quantity)
        row[10] = "虚构备注"
        row[11] = "2026-08-01"
        row[12] = "2026-09-01"
        row[13] = "标配物料"
        row[14] = "加工件" if offset < 3 else "采购件"
        row[15] = "2"
        row[16] = "虚构投产组织"
        row[17] = "虚构接单部门"
        row[18] = str(quantity * 2)
        row[19] = "REQ-TEST-001"
        production.append(row)
    source_rows = {
        "clients": (2, clients),
        "suppliers": (2, suppliers),
        "sales_orders": (2, [sales_row]),
        "production_requirements": (20, production),
    }
    for dataset_id, (first_row, values) in source_rows.items():
        spec = next(item for item in DEFAULT_LEGACY_DATASETS if item.dataset_id == dataset_id)
        records = [
            {
                "source_row_number": first_row + index,
                "cells": [
                    None
                    if value == ""
                    else {
                        "type": "date"
                        if column in {11, 12} and dataset_id == "production_requirements"
                        else "text",
                        "value": value,
                    }
                    for column, value in enumerate(row)
                ],
            }
            for index, row in enumerate(values)
        ]
        (root / f"{dataset_id}.jsonl").write_text(
            "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
            encoding="utf-8",
        )
        datasets.append(_manifest_item(spec.dataset_id, list(spec.expected_headers), len(records)))
    manifest = {
        "schema_version": "pms-legacy-raw-v1",
        "extracted_at": "2026-08-25T00:00:00Z",
        "contains_real_business_data": True,
        "restricted_data_included": True,
        "datasets": datasets,
    }
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return root


def _manifest_item(dataset_id: str, headers: list[str], count: int) -> dict[str, object]:
    return {
        "dataset_id": dataset_id,
        "relative_path": f"Database/{dataset_id}.xlsb",
        "sheet_name": "Sheet1",
        "headers": headers,
        "source_size_bytes": 100,
        "source_sha256": "a" * 64,
        "record_count": count,
        "output_file": f"{dataset_id}.jsonl",
        "restricted": True,
    }
