"""默认拒绝且租户优先的授权策略。"""

from typing import Protocol

from pms.authorization.domain.permissions import PermissionCode, PermissionScope
from pms.tenancy.domain.context import MembershipId, TenantContext, TenantId


class PermissionDeniedError(PermissionError):
    """表示当前可信上下文无权执行动作，不泄露目标是否存在。"""


class PermissionGrantLookup(Protocol):
    """查询当前 membership 聚合后的单项权限范围。"""

    def find_scope(
        self, *, membership_id: MembershipId, permission: PermissionCode
    ) -> PermissionScope | None:
        """没有明确授权时返回 None，由应用策略默认拒绝。"""


def authorize(
    *,
    context: TenantContext,
    resource_tenant_id: TenantId,
    permission: PermissionCode,
    is_related: bool,
    lookup: PermissionGrantLookup,
) -> None:
    """验证当前 membership 对当前租户对象的稳定权限代码。

    租户边界先于角色和对象范围判断；即使 tenant_admin 获得 tenant 范围
    权限，也不能访问其他租户。RELATED 授权还要求调用方已经使用可信的
    对象关系查询证明当前 membership 与对象相关。
    """
    if resource_tenant_id != context.tenant_id:
        raise PermissionDeniedError("当前成员无权执行该操作。")
    scope = lookup.find_scope(membership_id=context.membership_id, permission=permission)
    if scope is None or (scope is PermissionScope.RELATED and not is_related):
        raise PermissionDeniedError("当前成员无权执行该操作。")
