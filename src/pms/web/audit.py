"""记录工作台边界的失败、拒绝和受保护读取审计。

应用服务把成功业务动作与业务数据放在同一事务中审计；页面捕获到的
权限拒绝或业务规则失败已经离开该事务，因此由本模块追加结果记录。
这里不保存异常消息、表单内容或文件路径，只保存稳定结果类别。
"""

from uuid import UUID

from pms.audit.domain.events import AuditEvent, AuditResult
from pms.audit.infrastructure.django.recorder import DjangoAuditRecorder
from pms.tenancy.domain.context import TenantContext


def record_expected_error(
    *,
    context: TenantContext,
    action: str,
    object_type: str,
    object_id: UUID | str | None,
    error: Exception,
) -> None:
    """把服务端已处理异常归类为拒绝或业务失败，不记录异常原文。"""
    denied = isinstance(error, (PermissionError, LookupError))
    _record(
        context=context,
        action=action,
        object_type=object_type,
        object_id=object_id,
        result=AuditResult.DENIED if denied else AuditResult.FAILURE,
        reason_category="permission_or_scope" if denied else "business_rule",
    )


def record_denied_access(
    *,
    context: TenantContext,
    action: str,
    object_type: str,
    object_id: UUID | str | None,
) -> None:
    """记录被租户或对象范围过滤的直接访问，不泄露目标是否存在。"""
    _record(
        context=context,
        action=action,
        object_type=object_type,
        object_id=object_id,
        result=AuditResult.DENIED,
        reason_category="object_unavailable",
    )


def record_protected_read(
    *,
    context: TenantContext,
    action: str,
    object_type: str,
    object_id: UUID | str,
) -> None:
    """记录已经通过租户、对象范围和权限检查的受保护读取。"""
    _record(
        context=context,
        action=action,
        object_type=object_type,
        object_id=object_id,
        result=AuditResult.SUCCESS,
        reason_category="authorized",
    )


def _record(
    *,
    context: TenantContext,
    action: str,
    object_type: str,
    object_id: UUID | str | None,
    result: AuditResult,
    reason_category: str,
) -> None:
    DjangoAuditRecorder().record(
        AuditEvent(
            tenant_id=context.tenant_id,
            actor_id=context.user_id,
            membership_id=context.membership_id,
            action=action,
            object_type=object_type,
            object_id="" if object_id is None else str(object_id),
            result=result,
            summary={"channel": "web", "reason_category": reason_category},
        )
    )
