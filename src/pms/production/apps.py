"""投产 Django 应用配置。"""

from django.apps import AppConfig


class ProductionConfig(AppConfig):
    """注册投产模型与迁移。"""

    default_auto_field = "django.db.models.BigAutoField"
    name = "pms.production"
    label = "production"
