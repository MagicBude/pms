"""工作台登录、失败审计和租户 membership 选择。"""

from django.contrib.auth import authenticate
from django.db import transaction

from pms.audit.domain.events import AuditEvent, AuditResult
from pms.audit.infrastructure.django.recorder import DjangoAuditRecorder
from pms.identity.infrastructure.django.models import User
from pms.tenancy.domain.context import MembershipId, TenantContext, TenantId, UserId
from pms.tenancy.infrastructure.django.models import Membership, Tenant


def authenticate_local_user(*, username: str, password: str) -> tuple[User, TenantContext] | None:
    """认证用户并选择其第一个活动 membership。

    Phase 2 本机版通常只有一个 tenant。若未来一个用户属于多个 tenant，
    本函数仍按稳定创建顺序选取第一个；显式租户选择器在云端入口设计时
    增加，不能让登录表单直接提交任意 tenant ID。
    """
    user = authenticate(username=username, password=password)
    if not isinstance(user, User):
        _record_failed_login()
        return None
    membership = (
        Membership.objects.filter(
            user=user,
            user__is_active=True,
            tenant__is_active=True,
            is_active=True,
        )
        .order_by("created_at", "id")
        .first()
    )
    if membership is None:
        _record_failed_login()
        return None
    context = TenantContext(
        tenant_id=TenantId(membership.tenant_id),
        user_id=UserId(user.id),
        membership_id=MembershipId(membership.id),
    )
    with transaction.atomic():
        DjangoAuditRecorder().record(
            AuditEvent(
                tenant_id=context.tenant_id,
                actor_id=context.user_id,
                membership_id=context.membership_id,
                action="identity.login_succeeded",
                object_type="user",
                object_id=str(user.id),
                result=AuditResult.SUCCESS,
                summary={},
            )
        )
    return user, context


def record_logout(context: TenantContext) -> None:
    """在清除 session 前追加退出审计。"""
    DjangoAuditRecorder().record(
        AuditEvent(
            tenant_id=context.tenant_id,
            actor_id=context.user_id,
            membership_id=context.membership_id,
            action="identity.logout",
            object_type="user",
            object_id=str(context.user_id),
            result=AuditResult.SUCCESS,
            summary={},
        )
    )


def _record_failed_login() -> None:
    """记录本机 tenant 的失败认证，不保存尝试的用户名或密码。

    未知用户名没有可信 actor/membership，因此二者留空。云端按域名解析
    tenant 后可复用同一模式；当前若尚未初始化 tenant，则没有可安全关联
    的审计边界，登录页仍只返回统一失败消息。
    """
    tenant = Tenant.objects.filter(is_active=True).order_by("created_at", "id").first()
    if tenant is None:
        return
    DjangoAuditRecorder().record(
        AuditEvent(
            tenant_id=TenantId(tenant.id),
            actor_id=None,
            membership_id=None,
            action="identity.login_failed",
            object_type="user",
            object_id="unknown",
            result=AuditResult.DENIED,
            summary={},
        )
    )
