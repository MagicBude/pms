"""旧采购订单规范包的类型、分组、金额和输出安全测试。"""

import json
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command

from pms.legacy_migration.purchase_order_package import (
    LegacyPurchaseOrderPackageError,
    load_legacy_purchase_order_package,
    map_legacy_purchase_orders,
    write_legacy_purchase_order_package,
)
from pms.legacy_migration.raw_extraction import DEFAULT_LEGACY_DATASETS


def test_groups_cross_project_rows_and_preserves_amount_difference(tmp_path: Path) -> None:
    """订单号而非项目/请购号划分订单，旧合计和 Decimal 重算额并列保留。"""
    raw = _raw_package(
        tmp_path,
        [
            _row(2, order="OLD-001", project="PRJ-A", request="REQ-A", total="7.00"),
            _row(
                3,
                order="OLD-001",
                project="PRJ-B",
                request="REQ-B",
                quantity="2",
                price="3.335",
                total="6.66",
            ),
        ],
    )

    package = map_legacy_purchase_orders(raw)
    output = tmp_path / "purchase-orders.json"
    write_legacy_purchase_order_package(package, output)
    loaded = load_legacy_purchase_order_package(output)

    assert loaded.source_record_count == 2
    assert len(loaded.orders) == 1
    assert loaded.difference_order_count == 1
    order = loaded.orders[0]
    assert order.line_count == 2
    assert order.legacy_saved_total == "13.66"
    assert order.recalculated_total == "10.67"
    assert order.difference == "2.99"
    assert {line.project_code for line in order.lines} == {"PRJ-A", "PRJ-B"}
    assert all(line.target_material_code.startswith("LEG-M-") for line in order.lines)


def test_rejects_multiple_suppliers_in_one_order_without_output(tmp_path: Path) -> None:
    """同一旧订单混入多个承接方时不能猜测拆单或发布半成品。"""
    raw = _raw_package(
        tmp_path,
        [
            _row(2, order="OLD-001", supplier="供方甲"),
            _row(3, order="OLD-001", supplier="供方乙"),
        ],
    )
    output = tmp_path / "should-not-exist.json"

    with pytest.raises(LegacyPurchaseOrderPackageError, match="多个承接方"):
        package = map_legacy_purchase_orders(raw)
        write_legacy_purchase_order_package(package, output)

    assert not output.exists()


def test_rejects_tampered_recalculation_and_summary(tmp_path: Path) -> None:
    """规范包不能通过修改重算金额或摘要掩盖旧金额差异。"""
    raw = _raw_package(tmp_path, [_row(2, order="OLD-001")])
    output = tmp_path / "purchase-orders.json"
    write_legacy_purchase_order_package(map_legacy_purchase_orders(raw), output)
    payload = json.loads(output.read_text(encoding="utf-8"))
    payload["orders"][0]["lines"][0]["recalculated_total"] = "999.00"
    output.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(LegacyPurchaseOrderPackageError, match="重算结果"):
        load_legacy_purchase_order_package(output)


def test_marks_line_difference_even_when_order_net_difference_is_zero(tmp_path: Path) -> None:
    """正负行差额抵消仍属于需复核订单，不能因整单净差为零而漏报。"""
    raw = _raw_package(
        tmp_path,
        [
            _row(2, order="OLD-001", quantity="1", price="10", total="11.00"),
            _row(3, order="OLD-001", quantity="1", price="10", total="9.00"),
        ],
    )

    order = map_legacy_purchase_orders(raw).orders[0]

    assert order.difference == "0.00"
    assert order.has_amount_difference is True


def test_marks_sub_cent_source_difference_even_when_rounded_total_matches(tmp_path: Path) -> None:
    """旧合计与精确乘积有厘级差异时仍报告，分币重算匹配不能抹去原始异常。"""
    raw = _raw_package(
        tmp_path,
        [_row(2, order="OLD-001", quantity="1.2", price="3.335", total="4.00")],
    )

    order = map_legacy_purchase_orders(raw).orders[0]

    assert order.recalculated_total == "4.00"
    assert order.difference == "0.00"
    assert order.has_amount_difference is True


def test_preserves_sub_cent_legacy_saved_total_without_early_rounding(tmp_path: Path) -> None:
    """旧保存总价是迁移证据，厘级尾数必须留到规范包而非写入时舍入。"""
    raw = _raw_package(
        tmp_path,
        [_row(2, order="OLD-001", quantity="1", price="4", total="4.004")],
    )

    line = map_legacy_purchase_orders(raw).orders[0].lines[0]

    assert line.legacy_saved_total == "4.004"
    assert line.recalculated_total == "4.00"


def test_command_reports_only_counts_and_warning(tmp_path: Path) -> None:
    """普通命令输出不得泄露供应商、单价、总价或自由备注。"""
    raw = _raw_package(tmp_path, [_row(2, order="OLD-001", supplier="虚构秘密供方")])
    output = tmp_path / "purchase-orders.json"
    stdout = StringIO()

    call_command(
        "map_legacy_purchase_orders",
        raw=raw,
        output=output,
        stdout=stdout,
        no_color=True,
    )

    message = stdout.getvalue()
    assert "明细 1，订单 1，金额差异订单 1" in message
    assert "虚构秘密供方" not in message
    assert "3.335" not in message
    assert "内部备注" not in message


def _raw_package(tmp_path: Path, rows: list[dict[str, object]]) -> Path:
    root = tmp_path / "raw"
    root.mkdir()
    content = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    (root / "purchase_orders.jsonl").write_text(content, encoding="utf-8")
    spec = next(item for item in DEFAULT_LEGACY_DATASETS if item.dataset_id == "purchase_orders")
    manifest = {
        "schema_version": "pms-legacy-raw-v1",
        "extracted_at": "2026-08-26T00:00:00Z",
        "contains_real_business_data": True,
        "restricted_data_included": True,
        "datasets": [
            {
                "dataset_id": "purchase_orders",
                "relative_path": "Database/OutsourceDatabase.xlsb",
                "sheet_name": "Sheet1",
                "headers": list(spec.expected_headers),
                "source_size_bytes": 100,
                "source_sha256": "a" * 64,
                "record_count": len(rows),
                "output_file": "purchase_orders.jsonl",
                "restricted": True,
            }
        ],
    }
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return root


def _row(
    source_row: int,
    *,
    order: str,
    supplier: str = "虚构供方",
    project: str = "PRJ-A",
    request: str = "REQ-A",
    quantity: str = "1.2",
    price: str = "3.335",
    total: str = "7.00",
) -> dict[str, object]:
    values: list[tuple[str | None, str | None]] = [
        ("text", project),
        ("text", "虚构机型"),
        ("text", "MAT-001"),
        ("text", "虚构物料"),
        ("number", quantity),
        (None, None),
        (None, None),
        ("text", "件"),
        ("text", "内部备注"),
        ("date", "2026-01-01"),
        ("date", "2026-01-31"),
        ("text", "测试部门"),
        ("text", request),
        ("number", price),
        ("number", total),
        ("text", supplier),
        ("text", order),
        ("text", "台"),
        ("text", "加工件"),
        ("text", "测试分类"),
    ]
    return {
        "source_row_number": source_row,
        "cells": [{"type": kind, "value": value} for kind, value in values],
    }
