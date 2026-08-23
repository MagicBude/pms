"""自动化测试档案；只使用临时内存数据库。"""

import os

os.environ.setdefault("PMS_SECRET_KEY", "automated-tests-only")

from pms.settings.base import *

DEPLOYMENT_PROFILE = "test"
DEBUG = False
ALLOWED_HOSTS = ["testserver"]
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
