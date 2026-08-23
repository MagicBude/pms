"""请购剩余数量、编号和状态规则测试。"""

from datetime import date
from decimal import Decimal

import pytest

from pms.procurement.domain.request import (
    InvalidPurchaseRequestError,
    PurchaseRequestStatus,
    cancel_request,
    format_request_number,
    remaining_quantity,
)


@pytest.mark.unit
def test_remaining_quantity_excludes_cancelled_history_from_input_total() -> None:
    assert remaining_quantity(
        required=Decimal("6"), non_cancelled_requested=Decimal("2")
    ) == Decimal("4")
    with pytest.raises(InvalidPurchaseRequestError, match="超过"):
        remaining_quantity(required=Decimal("6"), non_cancelled_requested=Decimal("7"))


@pytest.mark.unit
def test_number_uses_business_date_and_minimum_three_digit_sequence() -> None:
    assert format_request_number(business_date=date(2026, 8, 25), sequence=1) == "20260825-001"
    assert format_request_number(business_date=date(2026, 8, 25), sequence=1000) == "20260825-1000"


@pytest.mark.unit
def test_cancellation_requires_reason_and_keeps_explicit_cancelled_state() -> None:
    assert (
        cancel_request(PurchaseRequestStatus.SUBMITTED, reason="项目调整")
        is PurchaseRequestStatus.CANCELLED
    )
    with pytest.raises(InvalidPurchaseRequestError, match="取消原因"):
        cancel_request(PurchaseRequestStatus.DRAFT, reason="  ")
