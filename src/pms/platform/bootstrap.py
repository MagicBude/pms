"""首次安装所需默认身份、租户与授权数据的显式编排。

初始化属于受信任的运维边界，不在 Web 启动、模型 ``save()`` 或 signal 中
隐式执行。这样空库部署可以明确提供初始秘密并审查写入结果，普通请求也
不会因为服务重启而获得额外权限。
"""

from dataclasses import dataclass

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.validators import validate_slug
from django.db import transaction

from pms.audit.domain.events import AuditEvent, AuditResult
from pms.audit.infrastructure.django.recorder import DjangoAuditRecorder
from pms.authorization.domain.default_matrix import (
    DEFAULT_PERMISSION_NAMES,
    DEFAULT_ROLE_GRANTS,
    DEFAULT_ROLE_NAMES,
)
from pms.authorization.domain.permissions import RoleCode
from pms.authorization.infrastructure.django.models import (
    MembershipRole,
    Permission,
    Role,
    RolePermission,
)
from pms.identity.infrastructure.django.models import User
from pms.tenancy.domain.context import TenantId
from pms.tenancy.infrastructure.django.models import Membership, Tenant


class InitializationError(Exception):
    """表示安装参数或已有数据不允许安全地继续初始化。"""


@dataclass(frozen=True, slots=True)
class InitializationResult:
    """只报告新增记录数量，不包含密码、主键或部署路径。"""

    tenant_created: int
    admin_created: int
    membership_created: int
    permissions_created: int
    roles_created: int
    role_grants_created: int
    admin_role_created: int


def initialize_installation(
    *,
    tenant_code: str,
    tenant_name: str,
    admin_username: str,
    initial_password: str | None,
) -> InitializationResult:
    """在单一事务中建立或校准可重复的安装基线。

    密码只在管理员首次创建时使用；重复初始化不会重置密码。若指定用户
    已经存在却不属于指定租户，本函数拒绝自动授予管理员角色，避免一个
    拼写错误把现有身份意外提升为租户管理员。默认角色及其授权属于版本化
    产品基线，因此重复执行会修复名称、范围和多余授权的漂移。

    Args:
        tenant_code: 稳定租户代码，默认本机安装使用 ``local``。
        tenant_name: 仅在租户首次创建时使用的显示名称。
        admin_username: 全局唯一的初始管理员登录名。
        initial_password: 首次创建管理员时使用的明文；调用方不得记录它。

    Returns:
        本次实际新增的各类记录数量；已存在且正确的数据记为零。

    Raises:
        InitializationError: 参数无效、缺少首次密码、密码不合规，或已有
            身份关系存在可能导致权限误授的冲突。

    Side Effects:
        写入 identity、tenancy、authorization 和追加式 audit 表。任何失败
        都会回滚全部数据库写入；本函数不创建文件、不启动服务，也不写秘密。
    """
    _validate_identifiers(
        tenant_code=tenant_code,
        tenant_name=tenant_name,
        admin_username=admin_username,
    )

    with transaction.atomic():
        tenant = Tenant.objects.select_for_update().filter(code=tenant_code).first()
        admin = User.objects.select_for_update().filter(username=admin_username).first()
        membership = None
        if tenant is not None and admin is not None:
            membership = (
                Membership.objects.select_for_update().filter(tenant=tenant, user=admin).first()
            )

        if admin is not None and membership is None:
            raise InitializationError(
                "指定管理员用户名已存在，但尚未属于目标租户；为防止权限误授，初始化已停止。"
            )
        tenant_already_has_admin = (
            tenant is not None
            and MembershipRole.objects.filter(
                membership__tenant=tenant,
                role_id=RoleCode.TENANT_ADMIN,
            ).exists()
        )
        requested_membership_is_admin = (
            membership is not None
            and MembershipRole.objects.filter(
                membership=membership,
                role_id=RoleCode.TENANT_ADMIN,
            ).exists()
        )
        if tenant_already_has_admin and not requested_membership_is_admin:
            raise InitializationError(
                "目标租户已经完成管理员初始化；新增管理员必须使用后续成员管理流程。"
            )
        _require_active_existing_records(tenant=tenant, admin=admin, membership=membership)

        tenant_created = 0
        if tenant is None:
            tenant = Tenant.objects.create(code=tenant_code, name=tenant_name)
            tenant_created = 1

        admin_created = 0
        if admin is None:
            password = _validate_initial_password(initial_password, admin_username=admin_username)
            admin = User.objects.create_user(username=admin_username, password=password)
            admin_created = 1

        membership_created = 0
        if membership is None:
            membership = Membership.objects.create(tenant=tenant, user=admin)
            membership_created = 1

        catalog_counts = _synchronize_default_authorization_catalog()
        tenant_admin_role = Role.objects.get(code=RoleCode.TENANT_ADMIN)
        _, admin_role_was_created = MembershipRole.objects.get_or_create(
            membership=membership,
            role=tenant_admin_role,
        )
        result = InitializationResult(
            tenant_created=tenant_created,
            admin_created=admin_created,
            membership_created=membership_created,
            permissions_created=catalog_counts[0],
            roles_created=catalog_counts[1],
            role_grants_created=catalog_counts[2],
            admin_role_created=int(admin_role_was_created),
        )
        _record_initialization(tenant=tenant, result=result)

    return result


def _validate_identifiers(*, tenant_code: str, tenant_name: str, admin_username: str) -> None:
    """在写库前验证命令边界，避免依赖数据库截断或宽松类型行为。"""
    if not tenant_code or len(tenant_code) > 64:
        raise InitializationError("租户代码必须为 1 至 64 个字符。")
    try:
        validate_slug(tenant_code)
    except ValidationError as error:
        raise InitializationError("租户代码只能包含 ASCII 字母、数字、下划线或连字符。") from error
    if not tenant_name or len(tenant_name) > 200:
        raise InitializationError("租户名称必须为 1 至 200 个字符。")
    if not admin_username or len(admin_username) > 150:
        raise InitializationError("管理员用户名必须为 1 至 150 个字符。")
    try:
        User.username_validator(admin_username)
    except ValidationError as error:
        raise InitializationError("管理员用户名包含不允许的字符。") from error


def _require_active_existing_records(
    *, tenant: Tenant | None, admin: User | None, membership: Membership | None
) -> None:
    """拒绝重新激活已停用对象，停用决策必须由显式管理流程撤销。"""
    if tenant is not None and not tenant.is_active:
        raise InitializationError("目标租户已停用；初始化不会自动重新启用租户。")
    if admin is not None and not admin.is_active:
        raise InitializationError("指定管理员已停用；初始化不会自动重新启用用户。")
    if membership is not None and not membership.is_active:
        raise InitializationError("指定管理员的成员关系已停用；初始化不会自动重新启用成员。")


def _validate_initial_password(initial_password: str | None, *, admin_username: str) -> str:
    """对只存在于进程内存的初始秘密执行 Django 完整密码策略。"""
    if not initial_password:
        raise InitializationError(
            "首次创建管理员必须通过 PMS_INITIAL_ADMIN_PASSWORD 提供初始密码。"
        )
    candidate = User(username=admin_username)
    try:
        validate_password(initial_password, user=candidate)
    except ValidationError as error:
        raise InitializationError("初始管理员密码不符合当前密码策略。") from error
    return initial_password


def _synchronize_default_authorization_catalog() -> tuple[int, int, int]:
    """把已接受矩阵同步为精确的默认角色模板，同时保留自定义角色。"""
    permissions_created = 0
    for permission_code, permission_name in DEFAULT_PERMISSION_NAMES.items():
        _, was_created = Permission.objects.update_or_create(
            code=permission_code,
            defaults={"name": permission_name},
        )
        permissions_created += int(was_created)

    roles_created = 0
    for role_code, role_name in DEFAULT_ROLE_NAMES.items():
        _, was_created = Role.objects.update_or_create(
            code=role_code,
            defaults={"name": role_name},
        )
        roles_created += int(was_created)

    role_grants_created = 0
    for role_code, grants in DEFAULT_ROLE_GRANTS.items():
        desired_permission_codes = [permission_code.value for permission_code in grants]
        RolePermission.objects.filter(role_id=role_code).exclude(
            permission_id__in=desired_permission_codes
        ).delete()
        for permission_code, scope in grants.items():
            _, was_created = RolePermission.objects.update_or_create(
                role_id=role_code,
                permission_id=permission_code,
                defaults={"scope": scope},
            )
            role_grants_created += int(was_created)

    return permissions_created, roles_created, role_grants_created


def _record_initialization(*, tenant: Tenant, result: InitializationResult) -> None:
    """追加运维审计；actor 留空以免把命令操作者误写成新管理员。"""
    DjangoAuditRecorder().record(
        AuditEvent(
            tenant_id=TenantId(tenant.id),
            actor_id=None,
            membership_id=None,
            action="platform.installation_initialized",
            object_type="tenant",
            object_id=tenant.code,
            result=AuditResult.SUCCESS,
            summary={
                "tenant_created": result.tenant_created,
                "admin_created": result.admin_created,
                "membership_created": result.membership_created,
                "permissions_created": result.permissions_created,
                "roles_created": result.roles_created,
                "role_grants_created": result.role_grants_created,
                "admin_role_created": result.admin_role_created,
            },
        )
    )
