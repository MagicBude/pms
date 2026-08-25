"""采购报价金额的 Decimal 舍入规则。"""

from decimal import Decimal

import pytest

from pms.procurement.domain.pricing import calculate_price_amounts


def test_calculates_tax_included_and_excluded_amounts() -> None:
    included = calculate_price_amounts(
        quantity=Decimal("3"),
        unit_price=Decimal("113"),
        tax_rate=Decimal("13"),
        tax_included=True,
    )
    excluded = calculate_price_amounts(
        quantity=Decimal("3"),
        unit_price=Decimal("100"),
        tax_rate=Decimal("13"),
        tax_included=False,
    )
    assert (included.net_amount, included.tax_amount, included.gross_amount) == (
        Decimal("300.00"),
        Decimal("39.00"),
        Decimal("339.00"),
    )
    assert excluded == included


@pytest.mark.parametrize(
    ("quantity", "price", "tax"),
    [
        ("0", "1", "13"),
        ("1", "0", "13"),
        ("1", "1", "-1"),
        ("1", "1", "101"),
    ],
)
def test_rejects_invalid_amount_inputs(quantity: str, price: str, tax: str) -> None:
    with pytest.raises(ValueError):
        calculate_price_amounts(
            quantity=Decimal(quantity),
            unit_price=Decimal(price),
            tax_rate=Decimal(tax),
            tax_included=True,
        )
