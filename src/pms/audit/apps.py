"""审计模块的 Django 应用配置。"""

from django.apps import AppConfig


class AuditConfig(AppConfig):
    """注册追加式审计日志持久化。"""

    default_auto_field = "django.db.models.BigAutoField"
    name = "pms.audit"
    label = "audit"
    verbose_name = "审计"
