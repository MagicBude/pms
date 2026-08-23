"""可信成员关系查询的 Django ORM 实现。"""

from pms.tenancy.application.resolve_context import ActiveMembership
from pms.tenancy.domain.context import MembershipId, TenantId, UserId
from pms.tenancy.infrastructure.django.models import Membership


class DjangoActiveMembershipLookup:
    """同时限定用户、membership 及双方启用状态，默认拒绝。"""

    def find(self, *, user_id: UserId, membership_id: MembershipId) -> ActiveMembership | None:
        row = (
            Membership.objects.filter(
                id=membership_id,
                user_id=user_id,
                is_active=True,
                tenant__is_active=True,
            )
            .values("id", "user_id", "tenant_id")
            .first()
        )
        if row is None:
            return None
        return ActiveMembership(
            tenant_id=TenantId(row["tenant_id"]),
            user_id=UserId(row["user_id"]),
            membership_id=MembershipId(row["id"]),
        )
