"""主数据 Django 应用配置。"""

from django.apps import AppConfig


class MasterDataConfig(AppConfig):
    """注册主数据模型与迁移，不在应用启动时隐式写入默认数据。"""

    default_auto_field = "django.db.models.BigAutoField"
    name = "pms.master_data"
    label = "master_data"
