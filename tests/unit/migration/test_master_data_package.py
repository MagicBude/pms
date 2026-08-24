"""客户与供应商版本化映射包的安全边界测试。"""

import json
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command

from pms.legacy_migration.master_data_package import (
    LegacyMasterDataPackageError,
    load_legacy_master_data_package,
    map_legacy_master_data,
    write_legacy_master_data_package,
)


def test_maps_typed_raw_rows_to_stable_generated_codes(tmp_path: Path) -> None:
    """中文旧简称保留为业务字段，来源行号生成跨机器一致的 ASCII 代码。"""
    raw = _raw_package(tmp_path)
    package = map_legacy_master_data(raw)
    output = tmp_path / "master-data.json"
    write_legacy_master_data_package(package, output)
    loaded = load_legacy_master_data_package(output)

    assert loaded.customers[0].code == "LEG-C-00002"
    assert loaded.customers[0].short_name == "甲客户"
    assert loaded.suppliers[0].code == "LEG-S-00007"
    assert loaded.suppliers[0].bank_account == "TEST-ACCOUNT"
    assert loaded.source_manifest_sha256 == package.source_manifest_sha256


def test_rejects_non_textual_master_data_cell_without_publishing(tmp_path: Path) -> None:
    """日期等意外类型不能被悄悄字符串化为银行或组织字段。"""
    raw = _raw_package(tmp_path)
    supplier_path = raw / "suppliers.jsonl"
    row = json.loads(supplier_path.read_text(encoding="utf-8"))
    row["cells"][3] = {"type": "date", "value": "2026-08-24"}
    supplier_path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")

    with pytest.raises(LegacyMasterDataPackageError, match="不能映射为文本"):
        map_legacy_master_data(raw)


def test_mapping_command_reports_counts_without_business_values(tmp_path: Path) -> None:
    """命令行只显示记录数和敏感提示，不回显组织或账户内容。"""
    raw = _raw_package(tmp_path)
    output = tmp_path / "mapped.json"
    stdout = StringIO()

    call_command("map_legacy_master_data", raw=raw, output=output, stdout=stdout, no_color=True)

    message = stdout.getvalue()
    assert "客户 1，供应商 1" in message
    assert "TEST-ACCOUNT" not in message
    assert "虚构供应商" not in message


def _raw_package(tmp_path: Path) -> Path:
    raw = tmp_path / "raw"
    raw.mkdir()
    clients = [
        "甲客户",
        "虚构客户有限公司",
        "TEST-TAX",
        "虚构地址",
        "000-0000",
        "测试银行",
        "TEST-CUSTOMER-ACCOUNT",
        "TEST-ROUTING",
    ]
    suppliers = [
        "乙供方",
        "虚构供应商有限公司",
        "测试联系人",
        "000-0001",
        "虚构地址二",
        "TEST-SUP-TAX",
        "TEST-SUP-ROUTING",
        "测试银行二",
        "TEST-ACCOUNT",
        "加工",
        "Example Supplier",
        "Example Address",
    ]
    (raw / "clients.jsonl").write_text(_raw_row(2, clients), encoding="utf-8")
    (raw / "suppliers.jsonl").write_text(_raw_row(7, suppliers), encoding="utf-8")
    manifest = {
        "schema_version": "pms-legacy-raw-v1",
        "extracted_at": "2026-08-24T00:00:00Z",
        "contains_real_business_data": True,
        "restricted_data_included": True,
        "datasets": [
            _manifest_item(
                "clients",
                "Database/ClientDatabase.xlsb",
                ["简称", "客户名称", "客户税号", "客户地址", "电话", "开户行", "账号", "行号"],
            ),
            _manifest_item(
                "suppliers",
                "Database/ProvideDatabase.xlsb",
                [
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
                ],
            ),
        ],
    }
    (raw / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return raw


def _raw_row(source_row: int, values: list[str]) -> str:
    payload = {
        "source_row_number": source_row,
        "cells": [{"type": "text", "value": value} for value in values],
    }
    return json.dumps(payload, ensure_ascii=False) + "\n"


def _manifest_item(dataset_id: str, relative_path: str, headers: list[str]) -> dict[str, object]:
    return {
        "dataset_id": dataset_id,
        "relative_path": relative_path,
        "sheet_name": "Sheet1",
        "headers": headers,
        "source_size_bytes": 100,
        "source_sha256": "a" * 64,
        "record_count": 1,
        "output_file": f"{dataset_id}.jsonl",
        "restricted": True,
    }
