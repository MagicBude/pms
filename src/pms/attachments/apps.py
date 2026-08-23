"""附件模块的 Django 应用配置。"""

from django.apps import AppConfig


class AttachmentsConfig(AppConfig):
    """注册附件元数据，不把二进制内容放入数据库。"""

    default_auto_field = "django.db.models.BigAutoField"
    name = "pms.attachments"
    label = "attachments"
    verbose_name = "附件"
