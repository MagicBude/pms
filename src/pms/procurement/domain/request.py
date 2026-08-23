"""生产请购状态、剩余数量和编号格式规则。"""

from datetime import date
from decimal import Decimal
from enum import StrEnum


class PurchaseRequestStatus(StrEnum):
    """生产请购的稳定状态。"""

    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    CANCELLED = "CANCELLED"


class InvalidPurchaseRequestError(ValueError):
    """表示请购状态、数量、编号或取消原因不满足业务规则。"""


def remaining_quantity(*, required: Decimal, non_cancelled_requested: Decimal) -> Decimal:
    """计算来源需求的剩余可请购数量。

    公式来自 D-S007：剩余 = 投产需求 - 未取消请购数量。首版不主动
    拆分，调用方应一次请购全部剩余；负数意味着持久化历史已违反不变量。
    """
    remaining = required - non_cancelled_requested
    if remaining < 0:
        raise InvalidPurchaseRequestError("来源需求的已请购数量超过投产需求。")
    return remaining


def format_request_number(*, business_date: date, sequence: int) -> str:
    """生成租户业务日期内唯一的 ``YYYYMMDD-NNN`` 编号。"""
    if sequence <= 0:
        raise InvalidPurchaseRequestError("请购序号必须大于零。")
    return f"{business_date:%Y%m%d}-{sequence:03d}"


def submit_request(current: PurchaseRequestStatus) -> PurchaseRequestStatus:
    """把请购草稿提交；重复网络重试由应用服务返回既有已提交结果。"""
    if current is not PurchaseRequestStatus.DRAFT:
        raise InvalidPurchaseRequestError("只有请购草稿可以提交。")
    return PurchaseRequestStatus.SUBMITTED


def cancel_request(current: PurchaseRequestStatus, *, reason: str) -> PurchaseRequestStatus:
    """取消草稿或已提交请购，必须留下可审计原因。"""
    if current not in {PurchaseRequestStatus.DRAFT, PurchaseRequestStatus.SUBMITTED}:
        raise InvalidPurchaseRequestError("该请购当前不能取消。")
    if not reason.strip() or len(reason.strip()) > 500:
        raise InvalidPurchaseRequestError("取消原因必须为 1 至 500 个字符。")
    return PurchaseRequestStatus.CANCELLED
