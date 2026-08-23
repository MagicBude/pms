"""授权模块的 Django 应用配置。"""

from django.apps import AppConfig


class AuthorizationConfig(AppConfig):
    """注册角色、权限和成员角色关系。"""

    default_auto_field = "django.db.models.BigAutoField"
    name = "pms.authorization"
    label = "authorization"
    verbose_name = "角色与权限"
