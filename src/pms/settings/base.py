"""所有部署形态共享且不降低安全性的 Django 配置。"""

from pathlib import Path

from pms.settings.environment import require

BASE_DIR = Path(__file__).resolve().parents[3]

SECRET_KEY = require("PMS_SECRET_KEY")
DEBUG = False
ALLOWED_HOSTS: list[str] = []

# F-004 创建自有用户模型以前不能启用 auth、sessions 或 admin，避免默认
# 用户表进入迁移历史。当前中间件也不依赖数据库或会话。
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "pms.identity.apps.IdentityConfig",
    "pms.tenancy.apps.TenancyConfig",
]
MIDDLEWARE = [
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
