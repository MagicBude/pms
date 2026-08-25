"""采购报价值规则与金额核算。

本模块不依赖数据库或 Web。它把含税口径、税率单位和舍入规则集中在
一个可单元测试的位置，避免页面、订单和迁移各自算出不同金额。
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

MONEY_QUANTUM = Decimal("0.01")
UNIT_PRICE_QUANTUM = Decimal("0.000001")


class QuoteStatus(StrEnum):
    ACTIVE = "ACTIVE"
    WITHDRAWN = "WITHDRAWN"


class QuoteSource(StrEnum):
    SUPPLIER = "SUPPLIER"
    HISTORICAL = "HISTORICAL"
    ERP = "ERP"
    MANUAL = "MANUAL"


class Currency(StrEnum):
    CNY = "CNY"
    USD = "USD"
    EUR = "EUR"
    JPY = "JPY"
    GBP = "GBP"


@dataclass(frozen=True, slots=True)
class PriceAmounts:
    """按一条报价和申请数量计算出的可冻结金额。"""

    net_amount: Decimal
    tax_amount: Decimal
    gross_amount: Decimal


def calculate_price_amounts(
    *, quantity: Decimal, unit_price: Decimal, tax_rate: Decimal, tax_included: bool
) -> PriceAmounts:
    """计算未税、税额和含税金额，税率使用百分数而非小数比例。"""
    if quantity <= 0:
        raise ValueError("申请数量必须大于零。")
    if unit_price <= 0:
        raise ValueError("报价单价必须大于零。")
    if tax_rate < 0 or tax_rate > 100:
        raise ValueError("税率必须在 0 至 100 之间。")
    factor = Decimal("1") + tax_rate / Decimal("100")
    if tax_included:
        gross = (quantity * unit_price).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
        net = (quantity * unit_price / factor).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    else:
        net = (quantity * unit_price).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
        gross = (quantity * unit_price * factor).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    return PriceAmounts(net_amount=net, tax_amount=gross - net, gross_amount=gross)
