"""BOM 状态、数量和发布规则测试。"""

from decimal import Decimal

import pytest

from pms.bom.domain.lifecycle import (
    BomStatus,
    InvalidBomTransitionError,
    cancel_bom,
    ensure_draft_editable,
    publish_bom,
)
from pms.bom.domain.validation import parse_positive_quantity


@pytest.mark.unit
def test_quantity_uses_decimal_and_rejects_non_positive_or_non_finite_values() -> None:
    assert parse_positive_quantity("1.250") == Decimal("1.250")
    assert parse_positive_quantity("0") is None
    assert parse_positive_quantity("-1") is None
    assert parse_positive_quantity("NaN") is None
    assert parse_positive_quantity("not-a-number") is None


@pytest.mark.unit
def test_publish_requires_nonempty_error_free_draft() -> None:
    assert publish_bom(current=BomStatus.DRAFT, line_count=1, error_count=0) is BomStatus.PUBLISHED
    with pytest.raises(InvalidBomTransitionError, match="逐行错误"):
        publish_bom(current=BomStatus.DRAFT, line_count=1, error_count=2)
    with pytest.raises(InvalidBomTransitionError, match="至少需要"):
        publish_bom(current=BomStatus.DRAFT, line_count=0, error_count=0)


@pytest.mark.unit
@pytest.mark.parametrize("status", [BomStatus.PUBLISHED, BomStatus.SUPERSEDED, BomStatus.CANCELLED])
def test_every_non_draft_version_is_immutable(status: BomStatus) -> None:
    """AC-S001-020：发布或结束版本不能原地修改。"""
    with pytest.raises(InvalidBomTransitionError):
        ensure_draft_editable(status)


@pytest.mark.unit
def test_cancel_requires_reason_and_rejects_active_production_reference() -> None:
    assert (
        cancel_bom(current=BomStatus.DRAFT, has_active_production=False, reason="方案终止")
        is BomStatus.CANCELLED
    )
    with pytest.raises(InvalidBomTransitionError, match="取消原因"):
        cancel_bom(current=BomStatus.DRAFT, has_active_production=False, reason=" ")
    with pytest.raises(InvalidBomTransitionError, match="投产批次"):
        cancel_bom(current=BomStatus.PUBLISHED, has_active_production=True, reason="方案终止")
