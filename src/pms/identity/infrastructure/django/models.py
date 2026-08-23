"""身份模块的 Django ORM 映射。"""

import uuid

from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """跨租户共享的登录身份。

    用户名是当前已接受的登录标识，保持全局唯一；用户加入哪些租户以及
    在租户内拥有什么角色由 F-005 的 membership 表达，不能直接增加
    ``tenant_id`` 或租户角色字段到本模型。UUIDv7 由应用生成，兼顾不可
    猜测性和按创建时间大致有序的索引局部性。
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)

    class Meta:
        """提供稳定且不与 Django 默认用户表冲突的数据库名称。"""

        db_table = "identity_user"
        verbose_name = "用户"
        verbose_name_plural = "用户"
