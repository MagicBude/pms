"""生产请购 Django 应用配置。"""

from django.apps import AppConfig


class ProcurementConfig(AppConfig):
    """注册生产请购模型与迁移。"""

    default_auto_field = "django.db.models.BigAutoField"
    name = "pms.procurement"
    label = "procurement"
