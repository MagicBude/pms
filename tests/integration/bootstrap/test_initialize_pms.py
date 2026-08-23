"""F-009 空安装、幂等和安全冲突边界测试。"""

from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from pms.audit.infrastructure.django.models import AuditLog
from pms.authorization.domain.default_matrix import (
    DEFAULT_PERMISSION_NAMES,
    DEFAULT_ROLE_GRANTS,
    DEFAULT_ROLE_NAMES,
)
from pms.authorization.domain.permissions import PermissionCode, PermissionScope, RoleCode
from pms.authorization.infrastructure.django.models import (
    MembershipRole,
    Permission,
    Role,
    RolePermission,
)
from pms.identity.infrastructure.django.models import User
from pms.tenancy.infrastructure.django.models import Membership, Tenant

STRONG_TEST_PASSWORD = "F009-ci-only!Different-5927"


@pytest.mark.django_db
def test_empty_installation_is_idempotent_and_does_not_echo_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PMS_INITIAL_ADMIN_PASSWORD", STRONG_TEST_PASSWORD)
    first_output = StringIO()

    call_command("initialize_pms", stdout=first_output, no_color=True)

    tenant = Tenant.objects.get(code="local")
    admin = User.objects.get(username="admin")
    membership = Membership.objects.get(tenant=tenant, user=admin)
    assert tenant.name == "本机租户"
    assert admin.check_password(STRONG_TEST_PASSWORD)
    assert not admin.is_superuser
    assert not admin.is_staff
    assert Permission.objects.count() == len(DEFAULT_PERMISSION_NAMES)
    assert Role.objects.count() == len(DEFAULT_ROLE_NAMES)
    assert RolePermission.objects.count() == sum(map(len, DEFAULT_ROLE_GRANTS.values()))
    assert MembershipRole.objects.filter(
        membership=membership,
        role_id=RoleCode.TENANT_ADMIN,
    ).exists()
    assert STRONG_TEST_PASSWORD not in first_output.getvalue()

    monkeypatch.delenv("PMS_INITIAL_ADMIN_PASSWORD")
    second_output = StringIO()
    call_command("initialize_pms", stdout=second_output, no_color=True)

    assert Tenant.objects.count() == 1
    assert User.objects.count() == 1
    assert Membership.objects.count() == 1
    assert MembershipRole.objects.count() == 1
    assert AuditLog.objects.count() == 2
    latest_audit = AuditLog.objects.first()
    assert latest_audit is not None
    assert latest_audit.action == "platform.installation_initialized"
    assert latest_audit.actor_id is None
    assert latest_audit.membership_id is None
    assert latest_audit.summary["tenant_created"] == 0
    assert "租户新增 0" in second_output.getvalue()
    assert "管理员新增 0" in second_output.getvalue()
    assert "权限新增 0" in second_output.getvalue()
    assert "角色新增 0" in second_output.getvalue()
    assert "授权新增 0" in second_output.getvalue()


@pytest.mark.django_db
def test_reinitialization_repairs_default_catalog_without_deleting_custom_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PMS_INITIAL_ADMIN_PASSWORD", STRONG_TEST_PASSWORD)
    call_command("initialize_pms", no_color=True)
    Role.objects.filter(code=RoleCode.AUDITOR).update(name="漂移名称")
    Permission.objects.filter(code=PermissionCode.PROJECT_VIEW).update(name="漂移权限")
    RolePermission.objects.filter(
        role_id=RoleCode.AUDITOR,
        permission_id=PermissionCode.PROJECT_VIEW,
    ).update(scope=PermissionScope.TENANT)
    RolePermission.objects.create(
        role_id=RoleCode.AUDITOR,
        permission_id=PermissionCode.PROJECT_EDIT,
        scope=PermissionScope.TENANT,
    )
    Role.objects.create(code="custom_observer", name="自定义观察角色")

    call_command("initialize_pms", no_color=True)

    assert Role.objects.get(code=RoleCode.AUDITOR).name == DEFAULT_ROLE_NAMES[RoleCode.AUDITOR]
    assert (
        Permission.objects.get(code=PermissionCode.PROJECT_VIEW).name
        == DEFAULT_PERMISSION_NAMES[PermissionCode.PROJECT_VIEW]
    )
    actual_auditor_grants = {
        PermissionCode(grant.permission_id): PermissionScope(grant.scope)
        for grant in RolePermission.objects.filter(role_id=RoleCode.AUDITOR)
    }
    assert actual_auditor_grants == DEFAULT_ROLE_GRANTS[RoleCode.AUDITOR]
    assert Role.objects.filter(code="custom_observer").exists()


@pytest.mark.django_db
def test_missing_first_password_rolls_back_all_installation_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PMS_INITIAL_ADMIN_PASSWORD", raising=False)

    with pytest.raises(CommandError, match="PMS_INITIAL_ADMIN_PASSWORD"):
        call_command("initialize_pms", no_color=True)

    assert Tenant.objects.count() == 0
    assert User.objects.count() == 0
    assert Permission.objects.count() == 0
    assert Role.objects.count() == 0


@pytest.mark.django_db
def test_existing_unrelated_username_is_never_granted_admin_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    User.objects.create_user(username="admin", password=STRONG_TEST_PASSWORD)
    monkeypatch.setenv("PMS_INITIAL_ADMIN_PASSWORD", "another-secret-that-must-not-be-used")

    with pytest.raises(CommandError, match="防止权限误授"):
        call_command("initialize_pms", no_color=True)

    assert Tenant.objects.count() == 0
    assert Membership.objects.count() == 0
    assert MembershipRole.objects.count() == 0


@pytest.mark.django_db
def test_initialized_tenant_rejects_a_second_bootstrap_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PMS_INITIAL_ADMIN_PASSWORD", STRONG_TEST_PASSWORD)
    call_command("initialize_pms", no_color=True)

    with pytest.raises(CommandError, match="后续成员管理流程"):
        call_command(
            "initialize_pms",
            admin_username="admin-typo",
            no_color=True,
        )

    assert User.objects.count() == 1
    assert Membership.objects.count() == 1
    assert MembershipRole.objects.count() == 1
