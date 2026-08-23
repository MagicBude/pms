"""从已认证用户和成员关系解析可信租户上下文。"""

from dataclasses import dataclass
from typing import Protocol

from pms.tenancy.domain.context import MembershipId, TenantContext, TenantId, UserId


class TenantContextUnavailableError(LookupError):
    """表示成员关系不存在、已停用或不属于当前用户。

    对外统一使用同一错误，避免泄露其他用户的 membership 是否存在。
    """


@dataclass(frozen=True, slots=True)
class ActiveMembership:
    """基础设施确认有效后返回给应用层的最小成员快照。"""

    tenant_id: TenantId
    user_id: UserId
    membership_id: MembershipId


class ActiveMembershipLookup(Protocol):
    """按当前用户范围查询有效 membership 的应用端口。"""

    def find(self, *, user_id: UserId, membership_id: MembershipId) -> ActiveMembership | None:
        """只返回用户、membership 和启用 tenant 同时匹配的结果。"""


def resolve_tenant_context(
    *, user_id: UserId, membership_id: MembershipId, lookup: ActiveMembershipLookup
) -> TenantContext:
    """解析可信上下文，不接受客户端提交的 tenant ID。

    Args:
        user_id: 已由 Django 会话认证的用户，不得来自请求参数。
        membership_id: 用户选择并存入受保护会话的成员关系标识。
        lookup: 负责同时校验用户、成员和 tenant 启用状态的查询端口。

    Raises:
        TenantContextUnavailableError: 关系不存在、不属于用户或任一边界停用。
    """
    membership = lookup.find(user_id=user_id, membership_id=membership_id)
    if membership is None:
        raise TenantContextUnavailableError("当前成员关系不可用。")
    return TenantContext(
        tenant_id=membership.tenant_id,
        user_id=membership.user_id,
        membership_id=membership.membership_id,
    )
