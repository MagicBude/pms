"""BOM 版本状态与发布不变量。"""

from enum import StrEnum


class BomStatus(StrEnum):
    """BOM 版本状态；发布后内容只能通过新版本改变。"""

    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    SUPERSEDED = "SUPERSEDED"
    CANCELLED = "CANCELLED"


class InvalidBomTransitionError(ValueError):
    """表示 BOM 当前状态或行校验结果不允许请求动作。"""


def publish_bom(*, current: BomStatus, line_count: int, error_count: int) -> BomStatus:
    """验证草稿可以成为不可变发布版本。

    发布至少需要一行，且所有逐行错误必须先处理。错误包含无效数量、
    未知单位、未确认物料和未确认疑似重复；应用层负责保留其来源行号。
    """
    if current is not BomStatus.DRAFT:
        raise InvalidBomTransitionError("只有 BOM 草稿可以发布。")
    if line_count <= 0:
        raise InvalidBomTransitionError("BOM 至少需要一条有效明细。")
    if error_count > 0:
        raise InvalidBomTransitionError(f"BOM 仍有 {error_count} 条逐行错误，不能发布。")
    return BomStatus.PUBLISHED


def ensure_draft_editable(current: BomStatus) -> None:
    """阻止发布、替代或取消版本被原地改写。"""
    if current is not BomStatus.DRAFT:
        raise InvalidBomTransitionError("已发布或已结束的 BOM 不能原地修改，请创建新版本。")
