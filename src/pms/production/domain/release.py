"""投产状态机和需求数量公式。"""

from decimal import Decimal
from enum import StrEnum


class ProductionStatus(StrEnum):
    """投产批次的稳定状态。"""

    DRAFT = "DRAFT"
    RELEASED = "RELEASED"
    CANCELLED = "CANCELLED"


class InvalidProductionError(ValueError):
    """表示投产字段、状态或下游历史不允许请求动作。"""


def validate_production_units(value: int) -> int:
    """验证投产台数必须是正整数，首版不接受小数批次。"""
    if isinstance(value, bool) or value <= 0:
        raise InvalidProductionError("投产台数必须是大于零的整数。")
    return value


def calculate_required_quantity(*, quantity_per_unit: Decimal, production_units: int) -> Decimal:
    """计算并返回投产需求数量。

    公式来自 D-S005：投产需求数量 = BOM 单台数量 × 投产台数。
    两个输入都必须为正，结果保持 Decimal 精度，不做单位换算或浮点运算。
    """
    validate_production_units(production_units)
    if not quantity_per_unit.is_finite() or quantity_per_unit <= 0:
        raise InvalidProductionError("BOM 单台数量必须是大于零的有限十进制数。")
    return quantity_per_unit * Decimal(production_units)


def release_production(current: ProductionStatus) -> ProductionStatus:
    """把投产草稿发布为不可变需求快照。"""
    if current is not ProductionStatus.DRAFT:
        raise InvalidProductionError("只有投产草稿可以发布。")
    return ProductionStatus.RELEASED


def cancel_production(
    current: ProductionStatus, *, has_active_purchase_request: bool
) -> ProductionStatus:
    """取消尚未产生有效请购的草稿或已发布投产批次。"""
    if current not in {ProductionStatus.DRAFT, ProductionStatus.RELEASED}:
        raise InvalidProductionError("该投产批次当前不能取消。")
    if has_active_purchase_request:
        raise InvalidProductionError("投产批次已有未取消请购，不能取消。")
    return ProductionStatus.CANCELLED
