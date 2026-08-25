"""正式采购/外协订单的状态和类型规则。"""

from enum import StrEnum


class PurchaseOrderStatus(StrEnum):
    """订单只允许草稿、已签发、已取消三个可审计状态。"""

    DRAFT = "DRAFT"
    ISSUED = "ISSUED"
    CANCELLED = "CANCELLED"


class PurchaseOrderKind(StrEnum):
    """按冻结明细的零件属性标识采购、外协或真实存在的混合订单。"""

    PURCHASE = "PURCHASE"
    OUTSOURCE = "OUTSOURCE"
    MIXED = "MIXED"


def derive_order_kind(part_attributes: set[str]) -> PurchaseOrderKind:
    """从一组零件属性推导单据类型，不把未知值静默当成采购件。"""
    normalized = {value.strip() for value in part_attributes}
    if normalized == {"采购件"}:
        return PurchaseOrderKind.PURCHASE
    if normalized == {"加工件"}:
        return PurchaseOrderKind.OUTSOURCE
    return PurchaseOrderKind.MIXED


def order_number_prefix(kind: PurchaseOrderKind) -> str:
    """返回稳定编号前缀；前缀不依赖可修改的供应商名称。"""
    return {
        PurchaseOrderKind.PURCHASE: "PO",
        PurchaseOrderKind.OUTSOURCE: "OS",
        PurchaseOrderKind.MIXED: "MX",
    }[kind]
