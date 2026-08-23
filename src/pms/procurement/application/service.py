"""生产请购幂等创建、提交编号和取消用例。"""

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Protocol
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pms.audit.application.recorder import AuditRecorder
from pms.audit.domain.events import AuditEvent, AuditResult
from pms.authorization.application.authorize import PermissionGrantLookup, authorize
from pms.authorization.domain.permissions import PermissionCode
from pms.procurement.domain.request import (
    PurchaseRequestStatus,
    cancel_request,
    submit_request,
)
from pms.production.domain.release import ProductionStatus
from pms.projects.domain.lifecycle import ProjectStatus
from pms.tenancy.domain.context import TenantContext


class PurchaseRequestNotFoundError(LookupError):
    """表示当前租户看不到目标投产或请购。"""


class PurchaseRequestConflictError(ValueError):
    """表示来源已存在其他有效请购或幂等键被错误复用。"""


@dataclass(frozen=True, slots=True)
class PurchaseSource:
    """创建请购需要的投产与对象关系事实。"""

    production_id: UUID
    project_id: UUID
    status: ProductionStatus
    project_status: ProjectStatus
    is_related: bool


@dataclass(frozen=True, slots=True)
class PurchaseRequestSnapshot:
    """页面和对账使用的生产请购快照。"""

    id: UUID
    production_id: UUID
    project_id: UUID
    status: PurchaseRequestStatus
    request_number: str | None
    line_count: int
    idempotency_key: str


class ProcurementTransactionManager(Protocol):
    def atomic(self) -> AbstractContextManager[None]: ...


class ProcurementRepository(Protocol):
    """生产请购模块拥有的数据访问与编号端口。"""

    def get_source(
        self, *, tenant_id: UUID, production_id: UUID, membership_id: UUID
    ) -> PurchaseSource | None: ...

    def create_or_get_draft(
        self,
        *,
        tenant_id: UUID,
        production_id: UUID,
        project_id: UUID,
        idempotency_key: str,
        created_by_membership_id: UUID,
    ) -> tuple[PurchaseRequestSnapshot, bool]: ...

    def get_for_update(
        self, *, tenant_id: UUID, request_id: UUID, membership_id: UUID
    ) -> tuple[PurchaseRequestSnapshot, bool] | None: ...

    def submit(
        self,
        *,
        tenant_id: UUID,
        request_id: UUID,
        membership_id: UUID,
        business_date: date,
    ) -> PurchaseRequestSnapshot: ...

    def cancel(
        self,
        *,
        tenant_id: UUID,
        request_id: UUID,
        membership_id: UUID,
        reason: str,
    ) -> PurchaseRequestSnapshot: ...

    def get_tenant_timezone(self, *, tenant_id: UUID) -> str | None: ...


class ProcurementService:
    """以投产需求为唯一来源创建、提交和取消生产请购。"""

    def __init__(
        self,
        *,
        repository: ProcurementRepository,
        grants: PermissionGrantLookup,
        audit: AuditRecorder,
        transactions: ProcurementTransactionManager,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._grants = grants
        self._audit = audit
        self._transactions = transactions
        self._clock = clock or (lambda: datetime.now(tz=UTC))

    def create_draft(
        self,
        *,
        context: TenantContext,
        production_id: UUID,
        idempotency_key: str,
    ) -> PurchaseRequestSnapshot:
        """一次性请购全部可请购来源，重复键返回同一草稿。"""
        normalized_key = idempotency_key.strip()
        if not normalized_key or len(normalized_key) > 128:
            raise ValueError("幂等键必须为 1 至 128 个字符。")
        source = self._repository.get_source(
            tenant_id=context.tenant_id,
            production_id=production_id,
            membership_id=context.membership_id,
        )
        if source is None:
            raise PurchaseRequestNotFoundError("投产批次不存在。")
        self._authorize(
            context=context,
            permission=PermissionCode.PURCHASE_REQUEST_CREATE,
            is_related=source.is_related,
        )
        if source.status is not ProductionStatus.RELEASED:
            raise ValueError("只有已发布投产批次可以生成请购。")
        if source.project_status is not ProjectStatus.ACTIVE:
            raise PurchaseRequestConflictError("只有活动项目可以生成新的生产请购。")
        with self._transactions.atomic():
            request, created = self._repository.create_or_get_draft(
                tenant_id=context.tenant_id,
                production_id=source.production_id,
                project_id=source.project_id,
                idempotency_key=normalized_key,
                created_by_membership_id=context.membership_id,
            )
            if created:
                self._record(context=context, action="purchase_request.created", request=request)
        return request

    def submit(self, *, context: TenantContext, request_id: UUID) -> PurchaseRequestSnapshot:
        """原子复核来源数量、分配租户日期编号并提交。

        已提交请求的网络重试直接返回原结果，不消耗新序号。编号日期使用
        tenant 时区，而不是服务器本地时间或客户端日期。
        """
        with self._transactions.atomic():
            found = self._repository.get_for_update(
                tenant_id=context.tenant_id,
                request_id=request_id,
                membership_id=context.membership_id,
            )
            if found is None:
                raise PurchaseRequestNotFoundError("生产请购不存在。")
            request, is_related = found
            self._authorize(
                context=context,
                permission=PermissionCode.PURCHASE_REQUEST_SUBMIT,
                is_related=is_related,
            )
            if request.status is PurchaseRequestStatus.SUBMITTED:
                return request
            submit_request(request.status)
            timezone_name = self._repository.get_tenant_timezone(tenant_id=context.tenant_id)
            if timezone_name is None:
                raise PurchaseRequestNotFoundError("租户不存在。")
            try:
                tenant_timezone = ZoneInfo(timezone_name)
            except ZoneInfoNotFoundError as error:
                raise ValueError("租户时区配置无效。") from error
            business_date = self._clock().astimezone(tenant_timezone).date()
            submitted = self._repository.submit(
                tenant_id=context.tenant_id,
                request_id=request.id,
                membership_id=context.membership_id,
                business_date=business_date,
            )
            self._record(context=context, action="purchase_request.submitted", request=submitted)
        return submitted

    def cancel(
        self, *, context: TenantContext, request_id: UUID, reason: str
    ) -> PurchaseRequestSnapshot:
        """取消请购并使其来源数量重新可请购，历史行保持不变。"""
        normalized_reason = " ".join(reason.split())
        with self._transactions.atomic():
            found = self._repository.get_for_update(
                tenant_id=context.tenant_id,
                request_id=request_id,
                membership_id=context.membership_id,
            )
            if found is None:
                raise PurchaseRequestNotFoundError("生产请购不存在。")
            request, is_related = found
            self._authorize(
                context=context,
                permission=PermissionCode.PURCHASE_REQUEST_CANCEL,
                is_related=is_related,
            )
            cancel_request(request.status, reason=normalized_reason)
            cancelled = self._repository.cancel(
                tenant_id=context.tenant_id,
                request_id=request.id,
                membership_id=context.membership_id,
                reason=normalized_reason,
            )
            self._record(
                context=context,
                action="purchase_request.cancelled",
                request=cancelled,
                extra={"reason": normalized_reason},
            )
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

    def _record(
        self,
        *,
        context: TenantContext,
        action: str,
        request: PurchaseRequestSnapshot,
        extra: dict[str, object] | None = None,
    ) -> None:
        summary: dict[str, object] = {
            "status": request.status.value,
            "request_number": request.request_number or "",
            "line_count": request.line_count,
        }
        if extra:
            summary.update(extra)
        self._audit.record(
            AuditEvent(
                tenant_id=context.tenant_id,
                actor_id=context.user_id,
                membership_id=context.membership_id,
                action=action,
                object_type="purchase_request",
                object_id=str(request.id),
                result=AuditResult.SUCCESS,
                summary=summary,
            )
        )
