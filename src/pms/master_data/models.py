"""Django 模型发现入口；实际映射位于 infrastructure 层。"""

from pms.master_data.infrastructure.django.models import Customer, Material, MaterialCategory, Unit

__all__ = ["Customer", "Material", "MaterialCategory", "Unit"]
