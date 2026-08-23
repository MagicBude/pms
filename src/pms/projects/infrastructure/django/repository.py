"""项目应用端口的 Django ORM 实现。"""

from contextlib import AbstractContextManager
from datetime import date
from uuid import UUID

from django.db import IntegrityError, transaction

from pms.master_data.infrastructure.django.models import Customer
from pms.projects.application.service import (
    DuplicateProjectNumberError,
    InvalidProjectDataError,
    ProjectNotFoundError,
    ProjectSnapshot,
)
from pms.projects.domain.lifecycle import ProjectStatus
from pms.projects.infrastructure.django.models import Project
from pms.tenancy.infrastructure.django.models import Membership


class DjangoProjectTransactionManager:
    """把 Django 原子事务适配为项目应用端口。"""

    def atomic(self) -> AbstractContextManager[None]:
        return transaction.atomic()


class DjangoProjectRepository:
    """项目查询始终以 tenant 开头，关联主数据也执行同租户校验。"""

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
    ) -> ProjectSnapshot:
        customer = Customer.objects.filter(
            id=customer_id, tenant_id=tenant_id, is_active=True
        ).first()
        memberships = Membership.objects.filter(
            id__in=(owner_membership_id, created_by_membership_id),
            tenant_id=tenant_id,
            is_active=True,
        )
        membership_ids = set(memberships.values_list("id", flat=True))
        if (
            customer is None
            or {
                owner_membership_id,
                created_by_membership_id,
            }
            - membership_ids
        ):
            raise InvalidProjectDataError("客户或项目负责人不可用。")
        try:
            project = Project.objects.create(
                tenant_id=tenant_id,
                number=number,
                customer=customer,
                device_model=device_model,
                owner_membership_id=owner_membership_id,
                created_by_membership_id=created_by_membership_id,
                start_date=start_date,
                planned_completion_date=planned_completion_date,
                status=ProjectStatus.DRAFT,
            )
        except IntegrityError as error:
            raise DuplicateProjectNumberError("当前租户已存在相同项目编号。") from error
        return self._snapshot(project)

    def get_for_update(self, *, tenant_id: UUID, project_id: UUID) -> ProjectSnapshot | None:
        project = (
            Project.objects.select_for_update().filter(id=project_id, tenant_id=tenant_id).first()
        )
        return None if project is None else self._snapshot(project)

    def set_status(
        self, *, tenant_id: UUID, project_id: UUID, status: ProjectStatus
    ) -> ProjectSnapshot:
        updated = Project.objects.filter(id=project_id, tenant_id=tenant_id).update(status=status)
        if updated != 1:
            raise ProjectNotFoundError("当前租户中不存在该项目。")
        return self._snapshot(Project.objects.get(id=project_id, tenant_id=tenant_id))

    @staticmethod
    def _snapshot(project: Project) -> ProjectSnapshot:
        # BOM 模块建立前不存在下游表；P2-02 会把该事实替换为本模块拥有的
        # 查询端口组合结果，并增加“已有下游不能取消”的集成测试。
        return ProjectSnapshot(
            id=project.id,
            tenant_id=project.tenant_id,
            number=project.number,
            customer_id=project.customer_id,
            device_model=project.device_model,
            owner_membership_id=project.owner_membership_id,
            status=ProjectStatus(project.status),
            has_downstream_records=False,
        )
