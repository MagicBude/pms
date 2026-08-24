"""默认角色授权必须与已接受的产品矩阵保持一致。"""

import pytest

from pms.authorization.domain.default_matrix import (
    DEFAULT_PERMISSION_NAMES,
    DEFAULT_ROLE_GRANTS,
    DEFAULT_ROLE_NAMES,
)
from pms.authorization.domain.permissions import PermissionCode, PermissionScope, RoleCode

WRITE_PERMISSIONS = frozenset(
    {
        PermissionCode.CONFIGURATION_MANAGE,
        PermissionCode.MEMBERSHIP_MANAGE,
        PermissionCode.CUSTOMER_MANAGE,
        PermissionCode.SUPPLIER_MANAGE,
        PermissionCode.MATERIAL_MANAGE,
        PermissionCode.PROJECT_CREATE,
        PermissionCode.PROJECT_EDIT,
        PermissionCode.PROJECT_ACTIVATE,
        PermissionCode.PROJECT_CLOSE,
        PermissionCode.PROJECT_CANCEL,
        PermissionCode.BOM_IMPORT,
        PermissionCode.BOM_EDIT,
        PermissionCode.BOM_PUBLISH,
        PermissionCode.BOM_CANCEL,
        PermissionCode.PRODUCTION_RELEASE_CREATE,
        PermissionCode.PRODUCTION_RELEASE_RELEASE,
        PermissionCode.PRODUCTION_RELEASE_CANCEL,
        PermissionCode.PURCHASE_REQUEST_CREATE,
        PermissionCode.PURCHASE_REQUEST_SUBMIT,
        PermissionCode.PURCHASE_REQUEST_CANCEL,
    }
)

EXPECTED_WRITE_GRANTS: dict[RoleCode, dict[PermissionCode, PermissionScope]] = {
    RoleCode.TENANT_ADMIN: dict.fromkeys(WRITE_PERMISSIONS, PermissionScope.TENANT),
    RoleCode.PROJECT_MANAGER: {
        PermissionCode.CUSTOMER_MANAGE: PermissionScope.TENANT,
        PermissionCode.PROJECT_CREATE: PermissionScope.TENANT,
        PermissionCode.PROJECT_EDIT: PermissionScope.TENANT,
        PermissionCode.PROJECT_ACTIVATE: PermissionScope.TENANT,
        PermissionCode.PROJECT_CLOSE: PermissionScope.TENANT,
        PermissionCode.PROJECT_CANCEL: PermissionScope.TENANT,
        PermissionCode.PRODUCTION_RELEASE_CREATE: PermissionScope.RELATED,
        PermissionCode.PRODUCTION_RELEASE_RELEASE: PermissionScope.RELATED,
        PermissionCode.PRODUCTION_RELEASE_CANCEL: PermissionScope.RELATED,
    },
    RoleCode.BOM_ENGINEER: {
        PermissionCode.MATERIAL_MANAGE: PermissionScope.TENANT,
        PermissionCode.BOM_IMPORT: PermissionScope.RELATED,
        PermissionCode.BOM_EDIT: PermissionScope.RELATED,
        PermissionCode.BOM_PUBLISH: PermissionScope.RELATED,
        PermissionCode.BOM_CANCEL: PermissionScope.RELATED,
    },
    RoleCode.REQUESTER: {
        PermissionCode.SUPPLIER_MANAGE: PermissionScope.TENANT,
        PermissionCode.PRODUCTION_RELEASE_CREATE: PermissionScope.TENANT,
        PermissionCode.PRODUCTION_RELEASE_RELEASE: PermissionScope.TENANT,
        PermissionCode.PRODUCTION_RELEASE_CANCEL: PermissionScope.TENANT,
        PermissionCode.PURCHASE_REQUEST_CREATE: PermissionScope.TENANT,
        PermissionCode.PURCHASE_REQUEST_SUBMIT: PermissionScope.TENANT,
        PermissionCode.PURCHASE_REQUEST_CANCEL: PermissionScope.TENANT,
    },
    RoleCode.AUDITOR: {},
}


@pytest.mark.unit
def test_tenant_admin_has_every_permission_with_tenant_scope() -> None:
    assert DEFAULT_ROLE_GRANTS[RoleCode.TENANT_ADMIN] == dict.fromkeys(
        PermissionCode, PermissionScope.TENANT
    )


@pytest.mark.unit
def test_auditor_is_read_only_and_limited_to_related_objects() -> None:
    auditor_grants = DEFAULT_ROLE_GRANTS[RoleCode.AUDITOR]

    assert auditor_grants
    assert set(auditor_grants.values()) == {PermissionScope.RELATED}
    assert PermissionCode.AUDIT_VIEW_RELATED in auditor_grants
    assert PermissionCode.AUDIT_VIEW_ALL not in auditor_grants
    assert PermissionCode.PROJECT_EDIT not in auditor_grants
    assert PermissionCode.BOM_EDIT not in auditor_grants


@pytest.mark.unit
def test_role_templates_cover_every_accepted_role_code() -> None:
    assert set(DEFAULT_ROLE_GRANTS) == set(RoleCode)


@pytest.mark.unit
def test_display_names_cover_every_stable_code() -> None:
    assert set(DEFAULT_PERMISSION_NAMES) == set(PermissionCode)
    assert set(DEFAULT_ROLE_NAMES) == set(RoleCode)


@pytest.mark.acceptance
@pytest.mark.parametrize("role", list(RoleCode))
def test_every_write_permission_matches_the_accepted_role_matrix(role: RoleCode) -> None:
    """AC-S001-037：五类角色的每个写权限都与已接受矩阵完全一致。"""
    actual = {
        permission: scope
        for permission, scope in DEFAULT_ROLE_GRANTS[role].items()
        if permission in WRITE_PERMISSIONS
    }
    assert actual == EXPECTED_WRITE_GRANTS[role]
