"""Django 模型发现入口；实际映射位于 infrastructure 层。"""

from pms.production.infrastructure.django.models import (
    ProductionRelease,
    ProductionRequirement,
)

__all__ = ["ProductionRelease", "ProductionRequirement"]
