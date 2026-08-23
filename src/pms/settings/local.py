"""单机 localhost 档案。

本档案允许 SQLite，但浏览器仍只能通过应用服务访问它。监听地址必须
保持 loopback；需要局域网访问时应切换到 ``lan`` 档案。
"""

import ipaddress
import os
from pathlib import Path

os.environ.setdefault(
    "PMS_SECRET_KEY",
    "f002-local-only-not-for-production-change-before-delivery-2026",
)

from pms.settings.base import *
from pms.settings.environment import (
    ConfigurationError,
    ensure_private_directory,
    read_bool,
)

DEPLOYMENT_PROFILE = "local"
DEBUG = read_bool("PMS_DEBUG", default=False)
BIND_HOST = os.environ.get("PMS_BIND_HOST", "127.0.0.1").strip()

try:
    if not ipaddress.ip_address(BIND_HOST).is_loopback:
        raise ConfigurationError("local 档案的 PMS_BIND_HOST 必须是 loopback 地址。")
except ValueError as error:
    raise ConfigurationError(
        "local 档案的 PMS_BIND_HOST 必须是 IP loopback 地址。"
    ) from error

ALLOWED_HOSTS = ["127.0.0.1", "localhost", "[::1]"]
DATA_DIR = ensure_private_directory(
    Path(os.environ.get("PMS_DATA_DIR", BASE_DIR / "data")).resolve()
)
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": DATA_DIR / "pms.sqlite3",
    }
}

SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
