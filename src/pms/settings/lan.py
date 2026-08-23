"""公司内网多人部署档案。"""

from pms.settings.base import *
from pms.settings.server import allowed_hosts, postgres_database

DEPLOYMENT_PROFILE = "lan"
DEBUG = False
ALLOWED_HOSTS = allowed_hosts()
DATABASES = {"default": postgres_database()}

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
