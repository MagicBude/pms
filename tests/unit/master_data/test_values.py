"""主数据稳定代码与名称规则测试。"""

import pytest

from pms.master_data.domain.values import (
    MasterDataValidationError,
    normalize_code,
    normalize_name,
)


@pytest.mark.unit
def test_code_normalization_is_database_collation_independent() -> None:
    """AC-S001-007/045：大小写不同不能绕过租户内稳定代码唯一性。"""
    assert normalize_code(" mat-001 ", field_name="物料编码") == "MAT-001"
    with pytest.raises(MasterDataValidationError):
        normalize_code("物料 001", field_name="物料编码")


@pytest.mark.unit
def test_name_preserves_display_but_builds_stable_comparison_key() -> None:
    display_name, comparison_key = normalize_name("  Demo   CUSTOMER  ", field_name="客户名称")

    assert display_name == "Demo CUSTOMER"
    assert comparison_key == "demo customer"
