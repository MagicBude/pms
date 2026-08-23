"""BOM 行值解析和稳定错误代码。"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum


class BomLineErrorCode(StrEnum):
    """可持久化、可翻译并能回溯来源行的校验代码。"""

    FORMULA_NOT_ALLOWED = "FORMULA_NOT_ALLOWED"
    MATERIAL_NAME_REQUIRED = "MATERIAL_NAME_REQUIRED"
    INVALID_QUANTITY = "INVALID_QUANTITY"
    UNKNOWN_UNIT = "UNKNOWN_UNIT"
    UNKNOWN_MATERIAL = "UNKNOWN_MATERIAL"
    MATERIAL_CONFIRMATION_REQUIRED = "MATERIAL_CONFIRMATION_REQUIRED"
    UNIT_MISMATCH = "UNIT_MISMATCH"
    SUSPECTED_DUPLICATE = "SUSPECTED_DUPLICATE"


ERROR_MESSAGES: dict[BomLineErrorCode, str] = {
    BomLineErrorCode.FORMULA_NOT_ALLOWED: "映射字段包含公式；BOM 只接受静态值。",
    BomLineErrorCode.MATERIAL_NAME_REQUIRED: "物料名称不能为空。",
    BomLineErrorCode.INVALID_QUANTITY: "单台数量必须是大于零的十进制定点数。",
    BomLineErrorCode.UNKNOWN_UNIT: "单位为空或当前租户不存在该单位。",
    BomLineErrorCode.UNKNOWN_MATERIAL: "当前租户不存在该物料编码。",
    BomLineErrorCode.MATERIAL_CONFIRMATION_REQUIRED: "无物料编码行必须人工确认或新建物料。",
    BomLineErrorCode.UNIT_MISMATCH: "BOM 单位与已确认物料单位不一致。",
    BomLineErrorCode.SUSPECTED_DUPLICATE: "存在疑似重复行，必须人工确认处理方式。",
}


@dataclass(frozen=True, slots=True)
class BomLineIssue:
    """一条可展示的来源行错误。"""

    source_row_number: int
    code: BomLineErrorCode
    message: str


def parse_positive_quantity(value: str) -> Decimal | None:
    """解析 BOM 单台数量，不使用二进制浮点数也不静默接受零值。"""
    try:
        quantity = Decimal(value.strip())
    except InvalidOperation, AttributeError:
        return None
    if not quantity.is_finite() or quantity <= 0:
        return None
    return quantity
