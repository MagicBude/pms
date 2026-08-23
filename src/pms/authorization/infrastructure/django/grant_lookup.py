"""成员聚合权限范围的 Django ORM 查询。"""

from django.db.models import Case, IntegerField, Max, Value, When

from pms.authorization.domain.permissions import PermissionCode, PermissionScope
from pms.authorization.infrastructure.django.models import RolePermission
from pms.tenancy.domain.context import MembershipId


class DjangoPermissionGrantLookup:
    """聚合 membership 的所有角色，较宽的 tenant 范围优先。"""

    def find_scope(
        self, *, membership_id: MembershipId, permission: PermissionCode
    ) -> PermissionScope | None:
        rank = RolePermission.objects.filter(
            role__membership_assignments__membership_id=membership_id,
            role__membership_assignments__membership__is_active=True,
            role__membership_assignments__membership__tenant__is_active=True,
            permission_id=permission,
        ).aggregate(
            value=Max(
                Case(
                    When(scope=PermissionScope.TENANT, then=Value(2)),
                    When(scope=PermissionScope.RELATED, then=Value(1)),
                    default=Value(0),
                    output_field=IntegerField(),
                )
            )
        )["value"]
        if rank == 2:
            return PermissionScope.TENANT
        if rank == 1:
            return PermissionScope.RELATED
        return None
