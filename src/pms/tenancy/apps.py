"""租户模块的 Django 应用配置。"""

from django.apps import AppConfig


class TenancyConfig(AppConfig):
    """注册 tenant 与 membership 的持久化映射。"""

    default_auto_field = "django.db.models.BigAutoField"
    name = "pms.tenancy"
    label = "tenancy"
    verbose_name = "租户与成员"
