"""身份模块的 Django 应用配置。"""

from django.apps import AppConfig


class IdentityConfig(AppConfig):
    """注册自有用户模型，固定首次迁移的身份边界。"""

    default_auto_field = "django.db.models.BigAutoField"
    name = "pms.identity"
    label = "identity"
    verbose_name = "身份与认证"
