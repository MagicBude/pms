"""Django 模型发现桥接。

实际 ORM 映射位于 infrastructure 层。本文件只负责满足 Django 对应用
根级 ``models`` 模块的发现约定，不在这里放置业务规则。
"""

from pms.identity.infrastructure.django.models import User

__all__ = ["User"]
