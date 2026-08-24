"""旧工作簿只读原始提取边界的单元测试。"""

import json
from datetime import UTC, date, datetime
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.management import CommandError, call_command
from openpyxl import Workbook

from pms.legacy_migration.raw_extraction import (
    ExtractedDataset,
    LegacyDatasetSpec,
    LegacyRawExtractionError,
    extract_legacy_raw_package,
)


def test_extract_preserves_source_rows_and_typed_cells_without_absolute_paths(
    tmp_path: Path,
) -> None:
    """原始包保留日期和 Decimal 语义，但清单不泄露本机绝对路径。"""
    legacy_root = tmp_path / "legacy"
    source_directory = legacy_root / "Database"
    source_directory.mkdir(parents=True)
    source = source_directory / "Sample.xlsx"
    _write_workbook(source)
    output = tmp_path / "export"

    extracted = extract_legacy_raw_package(
        legacy_root=legacy_root,
        output_directory=output,
        include_restricted=True,
        datasets=(_sample_spec(restricted=True),),
        clock=lambda: datetime(2026, 8, 24, 12, 0, tzinfo=UTC),
    )

    assert extracted[0].record_count == 2
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "pms-legacy-raw-v1"
    assert manifest["extracted_at"] == "2026-08-24T12:00:00Z"
    assert manifest["restricted_data_included"] is True
    assert str(tmp_path) not in json.dumps(manifest, ensure_ascii=False)
    lines = (output / "sample.jsonl").read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    second = json.loads(lines[1])
    assert first == {
        "source_row_number": 2,
        "cells": [
            {"type": "text", "value": "PRJ-001"},
            {"type": "number", "value": "2.5"},
            {"type": "date", "value": "2026-08-24"},
        ],
    }
    assert second["source_row_number"] == 4
    assert second["cells"][1] == {"type": "number", "value": "3.0"}


def test_extract_refuses_header_drift_and_does_not_publish_partial_directory(
    tmp_path: Path,
) -> None:
    """源结构变化时必须先更新映射，不能发布可能错列的原始包。"""
    legacy_root = tmp_path / "legacy"
    source_directory = legacy_root / "Database"
    source_directory.mkdir(parents=True)
    source = source_directory / "Sample.xlsx"
    _write_workbook(source)
    output = tmp_path / "export"
    changed = LegacyDatasetSpec(
        dataset_id="sample",
        relative_path=Path("Database/Sample.xlsx"),
        sheet_name="Sheet1",
        expected_headers=("项目编号", "错误数量", "日期"),
    )

    with pytest.raises(LegacyRawExtractionError, match="表头"):
        extract_legacy_raw_package(
            legacy_root=legacy_root,
            output_directory=output,
            include_restricted=False,
            datasets=(changed,),
        )

    assert not output.exists()
    assert list(tmp_path.glob(".pms-legacy-extract-*")) == []


def test_extract_requires_explicit_permission_for_restricted_only_selection(
    tmp_path: Path,
) -> None:
    """高敏感数据不会因为用户只提供了路径就被默认导出。"""
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()

    with pytest.raises(LegacyRawExtractionError, match="没有可提取"):
        extract_legacy_raw_package(
            legacy_root=legacy_root,
            output_directory=tmp_path / "export",
            include_restricted=False,
            datasets=(_sample_spec(restricted=True),),
        )


def test_management_command_reports_counts_without_business_values(tmp_path: Path) -> None:
    """命令输出只汇总数据集和行数，并把敏感授权显式传给提取服务。"""
    stdout = StringIO()
    result = ExtractedDataset(
        dataset_id="sample",
        relative_path="Database/Sample.xlsx",
        sheet_name="Sheet1",
        headers=("项目编号",),
        source_size_bytes=100,
        source_sha256="a" * 64,
        record_count=2,
        output_file="sample.jsonl",
        restricted=True,
    )
    with patch(
        "pms.platform.management.commands.extract_legacy_data.extract_legacy_raw_package",
        return_value=(result,),
    ) as extractor:
        call_command(
            "extract_legacy_data",
            "--legacy-root",
            str(tmp_path),
            "--output",
            str(tmp_path / "output"),
            "--include-restricted",
            stdout=stdout,
        )

    assert "1 个数据集，2 条记录" in stdout.getvalue()
    assert "真实业务数据" in stdout.getvalue()
    assert extractor.call_args.kwargs["include_restricted"] is True


def test_management_command_converts_safe_extraction_error_to_command_error(
    tmp_path: Path,
) -> None:
    """命令只显示提取层的受控错误，不泄露底层解析异常。"""
    with (
        patch(
            "pms.platform.management.commands.extract_legacy_data.extract_legacy_raw_package",
            side_effect=LegacyRawExtractionError("受控失败"),
        ),
        pytest.raises(CommandError, match="受控失败"),
    ):
        call_command(
            "extract_legacy_data",
            "--legacy-root",
            str(tmp_path),
            "--output",
            str(tmp_path / "output"),
        )


def _sample_spec(*, restricted: bool) -> LegacyDatasetSpec:
    return LegacyDatasetSpec(
        dataset_id="sample",
        relative_path=Path("Database/Sample.xlsx"),
        sheet_name="Sheet1",
        expected_headers=("项目编号", "数量", "日期"),
        restricted=restricted,
    )


def _write_workbook(path: Path) -> None:
    workbook = Workbook()
    worksheet = workbook.active
    assert worksheet is not None
    worksheet.title = "Sheet1"
    worksheet.append(["项目编号", "数量", "日期"])
    worksheet.append(["PRJ-001", 2.5, date(2026, 8, 24)])
    worksheet.append([None, None, None])
    worksheet.append(["PRJ-002", 3, None])
    workbook.save(path)
    workbook.close()
