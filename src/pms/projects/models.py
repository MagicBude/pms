"""Django 模型发现入口；实际映射位于 infrastructure 层。"""

from pms.projects.infrastructure.django.models import Project

__all__ = ["Project"]
