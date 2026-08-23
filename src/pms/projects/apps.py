"""项目 Django 应用配置。"""

from django.apps import AppConfig


class ProjectsConfig(AppConfig):
    """注册项目模型与迁移。"""

    default_auto_field = "django.db.models.BigAutoField"
    name = "pms.projects"
    label = "projects"
