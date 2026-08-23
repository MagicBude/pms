"""项目状态机测试。"""

import pytest

from pms.projects.domain.lifecycle import (
    InvalidProjectTransitionError,
    ProjectStatus,
    activate_project,
    cancel_project,
    close_project,
)


@pytest.mark.unit
def test_project_happy_path_is_draft_active_closed() -> None:
    active = activate_project(ProjectStatus.DRAFT)
    assert active is ProjectStatus.ACTIVE
    assert close_project(active) is ProjectStatus.CLOSED


@pytest.mark.unit
def test_cancel_rejects_projects_with_downstream_history() -> None:
    """AC-S001-009：取消不能隐藏已经形成的下游业务历史。"""
    with pytest.raises(InvalidProjectTransitionError, match="已有下游记录"):
        cancel_project(ProjectStatus.ACTIVE, has_downstream_records=True)


@pytest.mark.unit
@pytest.mark.parametrize(
    "status", [ProjectStatus.ACTIVE, ProjectStatus.CLOSED, ProjectStatus.CANCELLED]
)
def test_activation_rejects_every_non_draft_status(status: ProjectStatus) -> None:
    with pytest.raises(InvalidProjectTransitionError):
        activate_project(status)
