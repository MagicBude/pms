"""Office Open XML BOM 安全解析边界测试。"""

from io import BytesIO

import pytest
from openpyxl import Workbook

from pms.bom.application.service import MAX_BOM_SIZE_BYTES, BomImportError
from pms.bom.infrastructure.spreadsheet import OpenPyxlBomSpreadsheetParser

MAPPING = {
    "material_code": "物料编码",
    "material_name": "物料名称",
    "quantity_per_unit": "单台数量",
    "unit": "单位",
}


def workbook_bytes(*, formula: bool = False) -> bytes:
    workbook = Workbook()
    worksheet = workbook.active
    assert worksheet is not None
    worksheet.append(list(MAPPING.values()))
    worksheet.append(["MAT-001", "示例电机", "=1+1" if formula else "2.5", "PCS"])
    stream = BytesIO()
    workbook.save(stream)
    workbook.close()
    return stream.getvalue()


@pytest.mark.unit
@pytest.mark.parametrize("filename", ["demo.xlsx", "demo.xlsm"])
def test_parser_accepts_xlsx_and_xlsm_as_read_only_static_values(filename: str) -> None:
    rows = OpenPyxlBomSpreadsheetParser().parse(
        filename=filename,
        content=workbook_bytes(),
        mapping=MAPPING,
    )

    assert rows[0].source_row_number == 2
    assert rows[0].values["quantity_per_unit"] == "2.5"
    assert rows[0].formula_fields == ()


@pytest.mark.unit
def test_parser_preserves_formula_marker_so_application_can_block_publication() -> None:
    """AC-S001-012：公式不求值，也不能退化为缓存值悄悄进入 BOM。"""
    rows = OpenPyxlBomSpreadsheetParser().parse(
        filename="formula.xlsx",
        content=workbook_bytes(formula=True),
        mapping=MAPPING,
    )

    assert rows[0].formula_fields == ("quantity_per_unit",)


@pytest.mark.unit
def test_parser_rejects_extension_size_content_and_mapping_boundaries() -> None:
    parser = OpenPyxlBomSpreadsheetParser()
    content = workbook_bytes()
    with pytest.raises(BomImportError, match="只支持"):
        parser.parse(filename="legacy.xls", content=content, mapping=MAPPING)
    with pytest.raises(BomImportError, match="25 MiB"):
        parser.parse(filename="huge.xlsx", content=b"x" * (MAX_BOM_SIZE_BYTES + 1), mapping=MAPPING)
    with pytest.raises(BomImportError, match="不是有效"):
        parser.parse(filename="fake.xlsx", content=b"not-a-zip", mapping=MAPPING)
    with pytest.raises(BomImportError, match="缺少必填"):
        parser.parse(
            filename="demo.xlsx",
            content=content,
            mapping={"material_name": "物料名称"},
        )
