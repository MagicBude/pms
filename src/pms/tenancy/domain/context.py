"""可信租户上下文值对象。"""

from dataclasses import dataclass
from typing import NewType
from uuid import UUID

TenantId = NewType("TenantId", UUID)
UserId = NewType("UserId", UUID)
MembershipId = NewType("MembershipId", UUID)


@dataclass(frozen=True, slots=True)
class TenantContext:
    """一次业务操作经过服务端确认的用户、成员关系和租户。

    三个 ID 同时保留，是为了让应用服务、审计和数据访问能够追踪“谁以
    哪个成员身份代表哪家公司操作”。该对象只由解析用例创建，不能直接
    从请求体或 URL 反序列化。
    """

    tenant_id: TenantId
    user_id: UserId
    membership_id: MembershipId
