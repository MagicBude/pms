"""角色、权限、授权范围和成员角色的 ORM 映射。"""

from django.db import models

from pms.authorization.domain.permissions import PermissionScope
from pms.tenancy.infrastructure.django.models import Membership


class Permission(models.Model):
    """全局稳定能力代码；租户不能改变代码语义。"""

    code = models.CharField(primary_key=True, max_length=64)
    name = models.CharField(max_length=100)

    class Meta:
        db_table = "authorization_permission"


class Role(models.Model):
    """默认角色模板，不作为业务代码的判断条件。"""

    code = models.CharField(primary_key=True, max_length=64)
    name = models.CharField(max_length=100)

    class Meta:
        db_table = "authorization_role"


class RolePermission(models.Model):
    """角色对单项权限的明确授权及对象范围。"""

    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="permission_grants")
    permission = models.ForeignKey(Permission, on_delete=models.CASCADE, related_name="role_grants")
    scope = models.CharField(
        max_length=16,
        choices=[(scope.value, scope.value) for scope in PermissionScope],
    )

    class Meta:
        db_table = "authorization_role_permission"
        constraints = [
            models.UniqueConstraint(
                fields=("role", "permission"), name="uq_authorization_role_permission"
            )
        ]


class MembershipRole(models.Model):
    """把一个或多个角色模板分配给可信 membership。"""

    membership = models.ForeignKey(
        Membership, on_delete=models.CASCADE, related_name="role_assignments"
    )
    role = models.ForeignKey(Role, on_delete=models.PROTECT, related_name="membership_assignments")

    class Meta:
        db_table = "authorization_membership_role"
        constraints = [
            models.UniqueConstraint(
                fields=("membership", "role"), name="uq_authorization_membership_role"
            )
        ]
