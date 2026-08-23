"""从已认证会话恢复可信租户上下文。"""

from uuid import UUID

from django.http import HttpRequest

from pms.identity.infrastructure.django.models import User
from pms.tenancy.application.resolve_context import (
    TenantContextUnavailableError,
    resolve_tenant_context,
)
from pms.tenancy.domain.context import MembershipId, TenantContext, UserId
from pms.tenancy.infrastructure.django.membership_lookup import DjangoActiveMembershipLookup

SESSION_MEMBERSHIP_KEY = "pms_membership_id"


def resolve_request_context(request: HttpRequest) -> TenantContext:
    """从 Django 已认证用户和受保护 session 恢复 tenant。

    tenant ID 从不读取 URL、表单或查询字符串。session 只保存 membership
    ID；每次请求仍重新核对用户、成员和 tenant 启用状态，使停用立即生效。
    """
    if not request.user.is_authenticated or not isinstance(request.user, User):
        raise TenantContextUnavailableError("当前会话尚未登录。")
    raw_membership_id = request.session.get(SESSION_MEMBERSHIP_KEY)
    if not isinstance(raw_membership_id, str):
        raise TenantContextUnavailableError("当前会话没有可用租户。")
    try:
        membership_id = MembershipId(UUID(raw_membership_id))
    except ValueError as error:
        raise TenantContextUnavailableError("当前会话租户标识无效。") from error
    return resolve_tenant_context(
        user_id=UserId(request.user.id),
        membership_id=membership_id,
        lookup=DjangoActiveMembershipLookup(),
    )
