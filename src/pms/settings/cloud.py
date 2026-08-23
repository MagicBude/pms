"""商业云端部署档案，启用严格 HTTPS 安全策略。"""

from pms.settings.base import *
from pms.settings.server import allowed_hosts, postgres_database

DEPLOYMENT_PROFILE = "cloud"
DEBUG = False
ALLOWED_HOSTS = allowed_hosts()
DATABASES = {"default": postgres_database()}

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31_536_000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
