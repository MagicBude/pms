"""默认角色授权必须与已接受的产品矩阵保持一致。"""

import pytest

from pms.authorization.domain.default_matrix import (
    DEFAULT_PERMISSION_NAMES,
    DEFAULT_ROLE_GRANTS,
    DEFAULT_ROLE_NAMES,
)
from pms.authorization.domain.permissions import PermissionCode, PermissionScope, RoleCode


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
