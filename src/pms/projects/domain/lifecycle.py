"""项目状态机及其稳定不变量。"""

from enum import StrEnum


class ProjectStatus(StrEnum):
    """项目生命周期状态；客户端不能直接指定目标状态。"""

    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class InvalidProjectTransitionError(ValueError):
    """表示项目当前状态不允许请求的业务动作。"""


def activate_project(current: ProjectStatus) -> ProjectStatus:
    """启用草稿项目，使其可以接收 BOM。"""
    if current is not ProjectStatus.DRAFT:
        raise InvalidProjectTransitionError("只有草稿项目可以启用。")
    return ProjectStatus.ACTIVE


def close_project(current: ProjectStatus) -> ProjectStatus:
    """关闭活动项目，阻止新 BOM、投产和请购。"""
    if current is not ProjectStatus.ACTIVE:
        raise InvalidProjectTransitionError("只有活动项目可以关闭。")
    return ProjectStatus.CLOSED


def cancel_project(current: ProjectStatus, *, has_downstream_records: bool) -> ProjectStatus:
    """取消尚未形成下游业务记录的草稿或活动项目。

    已存在 BOM、投产或请购的项目不能用取消隐藏历史；这类项目应走关闭
    流程。应用仓储负责以可信租户查询下游记录，再把布尔事实传入领域层。
    """
    if current not in {ProjectStatus.DRAFT, ProjectStatus.ACTIVE}:
        raise InvalidProjectTransitionError("只有草稿或活动项目可以取消。")
    if has_downstream_records:
        raise InvalidProjectTransitionError("项目已有下游记录，不能取消，请改为关闭。")
    return ProjectStatus.CANCELLED
