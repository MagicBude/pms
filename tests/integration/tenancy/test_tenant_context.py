"""tenant、membership 与可信上下文的数据库边界测试。"""

import inspect

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError

from pms.tenancy.application.resolve_context import (
    TenantContextUnavailableError,
    resolve_tenant_context,
)
from pms.tenancy.domain.context import MembershipId, TenantId, UserId
from pms.tenancy.infrastructure.django.membership_lookup import DjangoActiveMembershipLookup
from pms.tenancy.infrastructure.django.models import Membership, Tenant


@pytest.mark.django_db
def test_active_membership_resolves_trusted_context() -> None:
    user = get_user_model().objects.create_user(username="member")
    tenant = Tenant.objects.create(code="alpha", name="Alpha Factory")
    membership = Membership.objects.create(user=user, tenant=tenant)

    context = resolve_tenant_context(
        user_id=UserId(user.id),
        membership_id=MembershipId(membership.id),
        lookup=DjangoActiveMembershipLookup(),
    )

    assert context.tenant_id == TenantId(tenant.id)
    assert context.user_id == UserId(user.id)
    assert context.membership_id == MembershipId(membership.id)


@pytest.mark.django_db
def test_user_cannot_resolve_another_users_membership() -> None:
    user_model = get_user_model()
    owner = user_model.objects.create_user(username="owner")
    attacker = user_model.objects.create_user(username="attacker")
    tenant = Tenant.objects.create(code="beta", name="Beta Factory")
    membership = Membership.objects.create(user=owner, tenant=tenant)

    with pytest.raises(TenantContextUnavailableError):
        resolve_tenant_context(
            user_id=UserId(attacker.id),
            membership_id=MembershipId(membership.id),
            lookup=DjangoActiveMembershipLookup(),
        )


@pytest.mark.django_db
@pytest.mark.parametrize("disable_target", ["membership", "tenant"])
def test_disabled_membership_or_tenant_cannot_resolve_context(disable_target: str) -> None:
    user = get_user_model().objects.create_user(username=f"disabled-{disable_target}")
    tenant = Tenant.objects.create(code=f"disabled-{disable_target}", name="Disabled Factory")
    membership = Membership.objects.create(user=user, tenant=tenant)
    target = membership if disable_target == "membership" else tenant
    target.is_active = False
    target.save(update_fields=("is_active",))

    with pytest.raises(TenantContextUnavailableError):
        resolve_tenant_context(
            user_id=UserId(user.id),
            membership_id=MembershipId(membership.id),
            lookup=DjangoActiveMembershipLookup(),
        )


@pytest.mark.django_db
def test_membership_is_unique_per_tenant_and_user_but_user_can_join_two_tenants() -> None:
    user = get_user_model().objects.create_user(username="multi-tenant")
    first_tenant = Tenant.objects.create(code="first", name="First Factory")
    second_tenant = Tenant.objects.create(code="second", name="Second Factory")
    Membership.objects.create(user=user, tenant=first_tenant)
    Membership.objects.create(user=user, tenant=second_tenant)

    with pytest.raises(IntegrityError):
        Membership.objects.create(user=user, tenant=first_tenant)


def test_context_resolver_does_not_accept_claimed_tenant_id() -> None:
    """客户端没有可传入 tenant_id 的参数，归属只能来自 membership 查询。"""
    parameters = inspect.signature(resolve_tenant_context).parameters

    assert "tenant_id" not in parameters
    assert "claimed_tenant_id" not in parameters
