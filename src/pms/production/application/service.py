"""投产草稿、发布快照和取消用例。"""

from contextlib import AbstractContextManager
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from pms.audit.application.recorder import AuditRecorder
from pms.audit.domain.events import AuditEvent, AuditResult
from pms.authorization.application.authorize import PermissionGrantLookup, authorize
from pms.authorization.domain.permissions import PermissionCode
from pms.bom.domain.lifecycle import BomStatus
from pms.production.domain.release import (
    ProductionStatus,
    cancel_production,
    release_production,
    validate_production_units,
)
from pms.projects.domain.lifecycle import ProjectStatus
from pms.tenancy.domain.context import TenantContext


class ProductionNotFoundError(LookupError):
    """表示当前租户看不到目标项目、BOM 或投产批次。"""


@dataclass(frozen=True, slots=True)
class ProductionSource:
    """创建投产草稿所需的项目和 BOM 只读事实。"""

    project_id: UUID
    bom_id: UUID
    project_status: ProjectStatus
    bom_status: BomStatus
    is_related: bool


@dataclass(frozen=True, slots=True)
class ProductionSnapshot:
    """页面和请购模块使用的投产批次快照。"""

    id: UUID
    project_id: UUID
    bom_id: UUID
    production_units: int
    status: ProductionStatus
    requirement_count: int


@dataclass(frozen=True, slots=True)
class RequirementSnapshot:
    """发布时固化的单条物料需求，数量单位由 ``unit_id`` 定义。"""

    id: UUID
    material_id: UUID
    material_code: str
    material_name: str
    unit_id: UUID
    quantity_per_unit: Decimal
    required_quantity: Decimal
    procurement_required: bool


class ProductionTransactionManager(Protocol):
    def atomic(self) -> AbstractContextManager[None]: ...


class ProductionDownstreamLookup(Protocol):
    """查询投产批次是否已有不能被取消隐藏的有效请购。"""

    def has_active_purchase_request(self, *, tenant_id: UUID, production_id: UUID) -> bool: ...


class ProductionRepository(Protocol):
    """投产模块拥有的数据访问端口。"""

    def get_source(
        self, *, tenant_id: UUID, project_id: UUID, bom_id: UUID, membership_id: UUID
    ) -> ProductionSource | None: ...

    def create_draft(
        self,
        *,
        tenant_id: UUID,
        project_id: UUID,
        bom_id: UUID,
        production_units: int,
        production_unit: str,
        receiving_department: str,
        created_by_membership_id: UUID,
    ) -> ProductionSnapshot: ...

    def get_for_update(
        self, *, tenant_id: UUID, production_id: UUID, membership_id: UUID
    ) -> tuple[ProductionSnapshot, bool] | None: ...

    def release(
        self, *, tenant_id: UUID, production_id: UUID, membership_id: UUID
    ) -> ProductionSnapshot: ...

    def cancel(self, *, tenant_id: UUID, production_id: UUID) -> ProductionSnapshot: ...

    def list_requirements(
        self, *, tenant_id: UUID, production_id: UUID
    ) -> list[RequirementSnapshot]: ...


@dataclass(frozen=True, slots=True)
class CreateProductionCommand:
    """创建投产草稿的输入；状态和计算结果不允许由客户端提交。"""

    project_id: UUID
    bom_id: UUID
    production_units: int
    production_unit: str
    receiving_department: str


class ProductionService:
    """在可信租户边界中编排投产状态和需求快照。"""

    def __init__(
        self,
        *,
        repository: ProductionRepository,
        grants: PermissionGrantLookup,
        audit: AuditRecorder,
        transactions: ProductionTransactionManager,
        downstream: ProductionDownstreamLookup,
    ) -> None:
        self._repository = repository
        self._grants = grants
        self._audit = audit
        self._transactions = transactions
        self._downstream = downstream

    def create_draft(
        self, *, context: TenantContext, command: CreateProductionCommand
    ) -> ProductionSnapshot:
        """从当前项目的已发布 BOM 创建投产草稿。"""
        source = self._repository.get_source(
            tenant_id=context.tenant_id,
            project_id=command.project_id,
            bom_id=command.bom_id,
            membership_id=context.membership_id,
        )
        if source is None:
            raise ProductionNotFoundError("项目或 BOM 不存在。")
        self._authorize(
            context=context,
            permission=PermissionCode.PRODUCTION_RELEASE_CREATE,
            is_related=source.is_related,
        )
        if source.project_status is not ProjectStatus.ACTIVE:
            raise ValueError("只有活动项目可以创建投产批次。")
        if source.bom_status is not BomStatus.PUBLISHED:
            raise ValueError("投产批次必须引用当前已发布 BOM。")
        units = validate_production_units(command.production_units)
        production_unit = self._required_text(command.production_unit, field="投产单位", maximum=64)
        department = self._required_text(
            command.receiving_department, field="接单部门", maximum=100
        )
        with self._transactions.atomic():
            production = self._repository.create_draft(
                tenant_id=context.tenant_id,
                project_id=source.project_id,
                bom_id=source.bom_id,
                production_units=units,
                production_unit=production_unit,
                receiving_department=department,
                created_by_membership_id=context.membership_id,
            )
            self._record(context=context, action="production.created", production=production)
        return production

    def release(self, *, context: TenantContext, production_id: UUID) -> ProductionSnapshot:
        """发布投产并在同一事务内固化每条 BOM 需求数量。"""
        with self._transactions.atomic():
            found = self._repository.get_for_update(
                tenant_id=context.tenant_id,
                production_id=production_id,
                membership_id=context.membership_id,
            )
            if found is None:
                raise ProductionNotFoundError("投产批次不存在。")
            production, is_related = found
            self._authorize(
                context=context,
                permission=PermissionCode.PRODUCTION_RELEASE_RELEASE,
                is_related=is_related,
            )
            release_production(production.status)
            released = self._repository.release(
                tenant_id=context.tenant_id,
                production_id=production.id,
                membership_id=context.membership_id,
            )
            self._record(context=context, action="production.released", production=released)
        return released

    def cancel(self, *, context: TenantContext, production_id: UUID) -> ProductionSnapshot:
        """取消没有有效请购的投产批次，并保留需求历史。"""
        with self._transactions.atomic():
            found = self._repository.get_for_update(
                tenant_id=context.tenant_id,
                production_id=production_id,
                membership_id=context.membership_id,
            )
            if found is None:
                raise ProductionNotFoundError("投产批次不存在。")
            production, is_related = found
            self._authorize(
                context=context,
                permission=PermissionCode.PRODUCTION_RELEASE_CANCEL,
                is_related=is_related,
            )
            cancel_production(
                production.status,
                has_active_purchase_request=self._downstream.has_active_purchase_request(
                    tenant_id=context.tenant_id, production_id=production.id
                ),
            )
            cancelled = self._repository.cancel(
                tenant_id=context.tenant_id, production_id=production.id
            )
            self._record(context=context, action="production.cancelled", production=cancelled)
        return cancelled

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
    def _required_text(value: str, *, field: str, maximum: int) -> str:
        normalized = " ".join(value.split())
        if not normalized or len(normalized) > maximum:
            raise ValueError(f"{field}必须为 1 至 {maximum} 个字符。")
        return normalized

    def _record(
        self, *, context: TenantContext, action: str, production: ProductionSnapshot
    ) -> None:
        self._audit.record(
            AuditEvent(
                tenant_id=context.tenant_id,
                actor_id=context.user_id,
                membership_id=context.membership_id,
                action=action,
                object_type="production_release",
                object_id=str(production.id),
                result=AuditResult.SUCCESS,
                summary={
                    "status": production.status.value,
                    "production_units": production.production_units,
                    "requirement_count": production.requirement_count,
                },
            )
        )
