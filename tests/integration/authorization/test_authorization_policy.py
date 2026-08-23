"""默认拒绝、对象范围和跨租户权限策略测试。"""

import pytest
from django.contrib.auth import get_user_model

from pms.authorization.application.authorize import PermissionDeniedError, authorize
from pms.authorization.domain.permissions import PermissionCode, PermissionScope
from pms.authorization.infrastructure.django.grant_lookup import DjangoPermissionGrantLookup
from pms.authorization.infrastructure.django.models import (
    MembershipRole,
    Permission,
    Role,
    RolePermission,
)
from pms.tenancy.domain.context import MembershipId, TenantContext, TenantId, UserId
from pms.tenancy.infrastructure.django.models import Membership, Tenant


def build_context_with_grant(scope: PermissionScope) -> tuple[TenantContext, Tenant]:
    """创建最小授权数据，测试不依赖尚未实现的 F-009 初始化。"""
    user = get_user_model().objects.create_user(username=f"user-{scope}")
    tenant = Tenant.objects.create(code=f"tenant-{scope}", name="Permission Factory")
    membership = Membership.objects.create(user=user, tenant=tenant)
    permission = Permission.objects.create(code=PermissionCode.PROJECT_VIEW, name="查看项目")
    role = Role.objects.create(code=f"role-{scope}", name="测试角色")
    RolePermission.objects.create(role=role, permission=permission, scope=scope)
    MembershipRole.objects.create(membership=membership, role=role)
    return (
        TenantContext(
            tenant_id=TenantId(tenant.id),
            user_id=UserId(user.id),
            membership_id=MembershipId(membership.id),
        ),
        tenant,
    )


@pytest.mark.django_db
def test_tenant_scope_grant_allows_current_tenant_object() -> None:
    context, tenant = build_context_with_grant(PermissionScope.TENANT)

    authorize(
        context=context,
        resource_tenant_id=TenantId(tenant.id),
        permission=PermissionCode.PROJECT_VIEW,
        is_related=False,
        lookup=DjangoPermissionGrantLookup(),
    )


@pytest.mark.django_db
def test_related_scope_requires_trusted_object_relationship() -> None:
    context, tenant = build_context_with_grant(PermissionScope.RELATED)

    with pytest.raises(PermissionDeniedError):
        authorize(
            context=context,
            resource_tenant_id=TenantId(tenant.id),
            permission=PermissionCode.PROJECT_VIEW,
            is_related=False,
            lookup=DjangoPermissionGrantLookup(),
        )

    authorize(
        context=context,
        resource_tenant_id=TenantId(tenant.id),
        permission=PermissionCode.PROJECT_VIEW,
        is_related=True,
        lookup=DjangoPermissionGrantLookup(),
    )


@pytest.mark.django_db
def test_missing_permission_is_denied_by_default() -> None:
    context, tenant = build_context_with_grant(PermissionScope.TENANT)

    with pytest.raises(PermissionDeniedError):
        authorize(
            context=context,
            resource_tenant_id=TenantId(tenant.id),
            permission=PermissionCode.PROJECT_EDIT,
            is_related=True,
            lookup=DjangoPermissionGrantLookup(),
        )


@pytest.mark.django_db
def test_tenant_scope_cannot_cross_tenant_boundary() -> None:
    context, _tenant = build_context_with_grant(PermissionScope.TENANT)
    other_tenant = Tenant.objects.create(code="other-tenant", name="Other Factory")

    with pytest.raises(PermissionDeniedError):
        authorize(
            context=context,
            resource_tenant_id=TenantId(other_tenant.id),
            permission=PermissionCode.PROJECT_VIEW,
            is_related=True,
            lookup=DjangoPermissionGrantLookup(),
        )


@pytest.mark.django_db
def test_inactive_membership_invalidates_existing_context_grant() -> None:
    """停用成员后，即使调用方仍持有旧上下文，也必须立即失去权限。"""
    context, tenant = build_context_with_grant(PermissionScope.TENANT)
    Membership.objects.filter(id=context.membership_id).update(is_active=False)

    with pytest.raises(PermissionDeniedError):
        authorize(
            context=context,
            resource_tenant_id=TenantId(tenant.id),
            permission=PermissionCode.PROJECT_VIEW,
            is_related=True,
            lookup=DjangoPermissionGrantLookup(),
        )


@pytest.mark.django_db
def test_removed_role_assignment_invalidates_existing_context_grant() -> None:
    """解除角色后，旧上下文不能继续沿用先前聚合出的权限。"""
    context, tenant = build_context_with_grant(PermissionScope.TENANT)
    MembershipRole.objects.filter(membership_id=context.membership_id).delete()

    with pytest.raises(PermissionDeniedError):
        authorize(
            context=context,
            resource_tenant_id=TenantId(tenant.id),
            permission=PermissionCode.PROJECT_VIEW,
            is_related=True,
            lookup=DjangoPermissionGrantLookup(),
        )
