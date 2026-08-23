"""BOM Django 应用配置。"""

from django.apps import AppConfig


class BomConfig(AppConfig):
    """注册 BOM 模型和迁移，不在启动阶段读取用户工作簿。"""

    default_auto_field = "django.db.models.BigAutoField"
    name = "pms.bom"
    label = "bom"
