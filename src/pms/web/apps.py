"""工作台展示应用配置。"""

from django.apps import AppConfig


class WebConfig(AppConfig):
    """注册模板资源；工作台没有模型或迁移。"""

    default_auto_field = "django.db.models.BigAutoField"
    name = "pms.web"
    label = "pms_web"
