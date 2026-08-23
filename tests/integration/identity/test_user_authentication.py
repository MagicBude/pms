"""自有用户模型的持久化与认证边界测试。"""

import uuid

import pytest
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import AnonymousUser
from django.db import IntegrityError
from django.test import Client


@pytest.mark.django_db
def test_user_manager_creates_uuid7_user_with_hashed_password() -> None:
    """创建用户时必须生成 UUIDv7，并且绝不明文保存密码。"""
    user_model = get_user_model()

    user = user_model.objects.create_user(username="learner", password="safe-test-password-2026")

    assert isinstance(user, AbstractBaseUser)
    assert isinstance(user.pk, uuid.UUID)
    assert user.pk.version == 7
    assert user.password != "safe-test-password-2026"
    assert user.check_password("safe-test-password-2026")


@pytest.mark.django_db
def test_active_user_can_authenticate_with_username_and_password() -> None:
    """当前登录标识是用户名，邮箱不参与认证。"""
    user_model = get_user_model()
    user = user_model.objects.create_user(
        username="active-user",
        email="optional@example.test",
        password="safe-test-password-2026",
    )

    authenticated = authenticate(username="active-user", password="safe-test-password-2026")

    assert authenticated == user


@pytest.mark.django_db
def test_inactive_user_cannot_authenticate_with_existing_session_credentials() -> None:
    """停用是服务端认证边界，正确密码也不能恢复访问。"""
    user_model = get_user_model()
    user_model.objects.create_user(
        username="inactive-user",
        password="safe-test-password-2026",
        is_active=False,
    )

    assert authenticate(username="inactive-user", password="safe-test-password-2026") is None


@pytest.mark.django_db
def test_login_and_logout_control_the_server_side_session() -> None:
    """会话只保存用户引用，退出后服务端必须恢复匿名身份。"""
    user_model = get_user_model()
    user_model.objects.create_user(
        username="session-user",
        password="safe-test-password-2026",
    )
    client = Client()

    assert client.login(username="session-user", password="safe-test-password-2026")
    assert "_auth_user_id" in client.session

    client.logout()

    assert "_auth_user_id" not in client.session
    response = client.get("/")
    assert isinstance(response.wsgi_request.user, AnonymousUser)


@pytest.mark.django_db
def test_duplicate_username_is_rejected_by_database() -> None:
    """登录标识的唯一性必须由数据库作为最后防线。"""
    user_model = get_user_model()
    user_model.objects.create_user(username="duplicate", password="safe-test-password-2026")

    with pytest.raises(IntegrityError) as exception_info:
        user_model.objects.create_user(username="duplicate", password="another-safe-password-2026")

    assert "another-safe-password-2026" not in str(exception_info.value)
