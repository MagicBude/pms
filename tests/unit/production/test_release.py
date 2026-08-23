"""投产状态和数量公式测试。"""

from decimal import Decimal

import pytest

from pms.production.domain.release import (
    InvalidProductionError,
    ProductionStatus,
    calculate_required_quantity,
    cancel_production,
    validate_production_units,
)


@pytest.mark.unit
def test_required_quantity_is_decimal_bom_quantity_times_integer_units() -> None:
    """AC-S001-023/024：投产公式不使用浮点数，台数必须是正整数。"""
    assert calculate_required_quantity(
        quantity_per_unit=Decimal("2.500000"), production_units=3
    ) == Decimal("7.500000")
    with pytest.raises(InvalidProductionError):
        validate_production_units(0)
    with pytest.raises(InvalidProductionError):
        validate_production_units(True)


@pytest.mark.unit
def test_production_with_active_request_cannot_be_cancelled() -> None:
    with pytest.raises(InvalidProductionError, match="未取消请购"):
        cancel_production(
            ProductionStatus.RELEASED,
            has_active_purchase_request=True,
        )
