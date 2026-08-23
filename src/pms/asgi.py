"""PMS 的 ASGI 入口。

默认采用安全的本机档案，服务器部署必须显式设置
``DJANGO_SETTINGS_MODULE``，避免把开发配置意外带入内网或云端。
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pms.settings.local")

application = get_asgi_application()
