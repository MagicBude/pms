"""项目创建与显式状态动作的应用服务。"""

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import date
from typing import Protocol
from uuid import UUID

from pms.audit.application.recorder import AuditRecorder
from pms.audit.domain.events import AuditEvent, AuditResult
from pms.authorization.application.authorize import PermissionGrantLookup, authorize
from pms.authorization.domain.permissions import PermissionCode
from pms.master_data.domain.values import normalize_code, normalize_name
from pms.projects.domain.lifecycle import (
    ProjectStatus,
    activate_project,
    cancel_project,
    close_project,
)
from pms.tenancy.domain.context import TenantContext


class ProjectNotFoundError(LookupError):
    """表示当前租户看不到目标项目，不泄露其他租户对象存在性。"""


class DuplicateProjectNumberError(ValueError):
    """表示当前租户内项目编号已经存在。"""


class InvalidProjectDataError(ValueError):
    """表示项目字段或关联主数据不满足创建规则。"""


class ProjectTransactionManager(Protocol):
    """项目写用例的原子事务端口。"""

    def atomic(self) -> AbstractContextManager[None]: ...


class ProjectDownstreamLookup(Protocol):
    """查询项目是否已形成不能被取消隐藏的下游历史。"""

    def has_records(self, *, tenant_id: UUID, project_id: UUID) -> bool: ...


@dataclass(frozen=True, slots=True)
class CreateProjectCommand:
    """创建项目的用户输入；tenant 和初始状态不允许由客户端提供。"""

    number: str
    customer_id: UUID
    device_model: str
    owner_membership_id: UUID
    start_date: date | None = None
    planned_completion_date: date | None = None


@dataclass(frozen=True, slots=True)
class ProjectSnapshot:
    """应用边界使用的项目快照，避免把 ORM 实体泄露到页面。"""

    id: UUID
    tenant_id: UUID
    number: str
    customer_id: UUID
    device_model: str
    owner_membership_id: UUID
    status: ProjectStatus


class ProjectRepository(Protocol):
    """项目模块拥有的数据访问端口。"""

    def create(
        self,
        *,
        tenant_id: UUID,
        number: str,
        customer_id: UUID,
        device_model: str,
        owner_membership_id: UUID,
        created_by_membership_id: UUID,
        start_date: date | None,
        planned_completion_date: date | None,
    ) -> ProjectSnapshot: ...

    def get_for_update(self, *, tenant_id: UUID, project_id: UUID) -> ProjectSnapshot | None: ...

    def set_status(
        self, *, tenant_id: UUID, project_id: UUID, status: ProjectStatus
    ) -> ProjectSnapshot: ...

    def cancel(
        self,
        *,
        tenant_id: UUID,
        project_id: UUID,
        membership_id: UUID,
        reason: str,
    ) -> ProjectSnapshot: ...


class ProjectService:
    """项目用例；状态只能通过命名动作迁移。"""

    def __init__(
        self,
        *,
        repository: ProjectRepository,
        grants: PermissionGrantLookup,
        audit: AuditRecorder,
        transactions: ProjectTransactionManager,
        downstream: ProjectDownstreamLookup,
    ) -> None:
        self._repository = repository
        self._grants = grants
        self._audit = audit
        self._transactions = transactions
        self._downstream = downstream

    def create_project(
        self, *, context: TenantContext, command: CreateProjectCommand
    ) -> ProjectSnapshot:
        """创建状态固定为 DRAFT 的租户项目。"""
        self._authorize(
            context=context,
            permission=PermissionCode.PROJECT_CREATE,
            is_related=True,
        )
        number = normalize_code(command.number, field_name="项目编号")
        device_model, _normalized = normalize_name(
            command.device_model, field_name="设备机型", maximum_length=200
        )
        if (
            command.start_date is not None
            and command.planned_completion_date is not None
            and command.planned_completion_date < command.start_date
        ):
            raise InvalidProjectDataError("计划完成日期不能早于开始日期。")
        with self._transactions.atomic():
            project = self._repository.create(
                tenant_id=context.tenant_id,
                number=number,
                customer_id=command.customer_id,
                device_model=device_model,
                owner_membership_id=command.owner_membership_id,
                created_by_membership_id=context.membership_id,
                start_date=command.start_date,
                planned_completion_date=command.planned_completion_date,
            )
            self._record(context=context, action="project.created", project=project)
        return project

    def activate_project(self, *, context: TenantContext, project_id: UUID) -> ProjectSnapshot:
        """把当前租户草稿项目启用。"""
        return self._transition(
            context=context,
            project_id=project_id,
            permission=PermissionCode.PROJECT_ACTIVATE,
            action="project.activated",
            decide=lambda project: activate_project(project.status),
        )

    def close_project(self, *, context: TenantContext, project_id: UUID) -> ProjectSnapshot:
        """关闭活动项目，保留全部历史。"""
        return self._transition(
            context=context,
            project_id=project_id,
            permission=PermissionCode.PROJECT_CLOSE,
            action="project.closed",
            decide=lambda project: close_project(project.status),
        )

    def cancel_project(
        self, *, context: TenantContext, project_id: UUID, reason: str
    ) -> ProjectSnapshot:
        """取消无下游记录的草稿或活动项目，并保存原因与操作者。"""
        normalized_reason = self._required_reason(reason)
        with self._transactions.atomic():
            project = self._repository.get_for_update(
                tenant_id=context.tenant_id, project_id=project_id
            )
            if project is None:
                raise ProjectNotFoundError("当前租户中不存在该项目。")
            self._authorize(
                context=context,
                permission=PermissionCode.PROJECT_CANCEL,
                is_related=project.owner_membership_id == context.membership_id,
            )
            cancel_project(
                project.status,
                has_downstream_records=self._downstream.has_records(
                    tenant_id=context.tenant_id, project_id=project.id
                ),
            )
            updated = self._repository.cancel(
                tenant_id=context.tenant_id,
                project_id=project.id,
                membership_id=context.membership_id,
                reason=normalized_reason,
            )
            self._record(
                context=context,
                action="project.cancelled",
                project=updated,
                extra={"reason": normalized_reason},
            )
        return updated

    def _transition(
        self,
        *,
        context: TenantContext,
        project_id: UUID,
        permission: PermissionCode,
        action: str,
        decide: Callable[[ProjectSnapshot], ProjectStatus],
    ) -> ProjectSnapshot:
        """在锁定项目的事务内执行授权、领域决策、保存和审计。"""
        with self._transactions.atomic():
            project = self._repository.get_for_update(
                tenant_id=context.tenant_id, project_id=project_id
            )
            if project is None:
                raise ProjectNotFoundError("当前租户中不存在该项目。")
            is_related = project.owner_membership_id == context.membership_id
            self._authorize(context=context, permission=permission, is_related=is_related)
            target_status = decide(project)
            updated = self._repository.set_status(
                tenant_id=context.tenant_id,
                project_id=project.id,
                status=target_status,
            )
            self._record(context=context, action=action, project=updated)
        return updated

    def _authorize(
        self,
        *,
        context: TenantContext,
        permission: PermissionCode,
        is_related: bool,
    ) -> None:
        authorize(
            context=context,
            resource_tenant_id=context.tenant_id,
            permission=permission,
            is_related=is_related,
            lookup=self._grants,
        )

    @staticmethod
    def _required_reason(value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized or len(normalized) > 500:
            raise InvalidProjectDataError("取消原因必须为 1 至 500 个字符。")
        return normalized

    def _record(
        self,
        *,
        context: TenantContext,
        action: str,
        project: ProjectSnapshot,
        extra: dict[str, object] | None = None,
    ) -> None:
        summary: dict[str, object] = {
            "number": project.number,
            "status": project.status.value,
        }
        if extra:
            summary.update(extra)
        self._audit.record(
            AuditEvent(
                tenant_id=context.tenant_id,
                actor_id=context.user_id,
                membership_id=context.membership_id,
                action=action,
                object_type="project",
                object_id=str(project.id),
                result=AuditResult.SUCCESS,
                summary=summary,
            )
        )
