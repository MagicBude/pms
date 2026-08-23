"""所有部署形态共享且不降低安全性的 Django 配置。"""

from pathlib import Path

from pms.platform.logging import build_logging_config
from pms.settings.environment import require

BASE_DIR = Path(__file__).resolve().parents[3]

SECRET_KEY = require("PMS_SECRET_KEY")
DEBUG = False
ALLOWED_HOSTS: list[str] = []

# 自有用户必须先于其他业务迁移存在；所有外键都通过 AUTH_USER_MODEL 引用，
# 禁止重新引入默认 auth_user，否则会破坏既有身份、租户和审计迁移历史。
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "pms.identity.apps.IdentityConfig",
    "pms.tenancy.apps.TenancyConfig",
    "pms.authorization.apps.AuthorizationConfig",
    "pms.audit.apps.AuditConfig",
    "pms.attachments.apps.AttachmentsConfig",
]
MIDDLEWARE = [
    "pms.platform.middleware.RequestContextMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "pms.urls"
TEMPLATES: list[dict[str, object]] = []
WSGI_APPLICATION = None
ASGI_APPLICATION = "pms.asgi.application"
AUTH_USER_MODEL = "identity.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True

SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

LOGGING = build_logging_config(app_level="INFO")
