"""平台技术边界的 Django 应用声明。"""

from django.apps import AppConfig


class PlatformConfig(AppConfig):
    """注册只承担启动、初始化和运维编排的平台能力。"""

    default_auto_field = "django.db.models.BigAutoField"
    name = "pms.platform"
    label = "platform"
    verbose_name = "平台"
