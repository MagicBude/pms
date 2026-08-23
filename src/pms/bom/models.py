"""Django 模型发现入口；实际映射位于 infrastructure 层。"""

from pms.bom.infrastructure.django.models import BomLine, BomVersion

__all__ = ["BomLine", "BomVersion"]
