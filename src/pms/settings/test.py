"""自动化测试档案；只使用临时内存数据库。"""

import os

os.environ.setdefault("PMS_SECRET_KEY", "automated-tests-only")

from pms.platform.logging import build_logging_config
from pms.settings.base import *

DEPLOYMENT_PROFILE = "test"
DEBUG = False
LOGGING = build_logging_config(app_level="CRITICAL")
ALLOWED_HOSTS = ["testserver"]
if os.environ.get("PMS_TEST_DATABASE") == "postgresql":
    from pms.settings.server import postgres_database

    DATABASES = {"default": postgres_database()}
else:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
