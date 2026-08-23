"""与日志框架和数据库无关的审计事件。"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pms.tenancy.domain.context import MembershipId, TenantId, UserId


class AuditResult(StrEnum):
    """稳定审计结果代码。"""

    SUCCESS = "success"
    DENIED = "denied"
    FAILURE = "failure"


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """应用用例提交给审计端口的安全最小事件。"""

    tenant_id: TenantId
    actor_id: UserId | None
    membership_id: MembershipId | None
    action: str
    object_type: str
    object_id: str
    result: AuditResult
    summary: dict[str, Any] = field(default_factory=dict)
