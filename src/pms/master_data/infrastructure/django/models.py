"""客户、单位、分类和物料的租户级 ORM 映射。"""

import uuid

from django.db import models

from pms.tenancy.infrastructure.django.models import Tenant


class TenantMasterData(models.Model):
    """租户主数据共同字段；抽象基类不会建立独立数据库表。"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT)
    code = models.CharField(max_length=64)
    name = models.CharField(max_length=200)
    normalized_name = models.CharField(max_length=200)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Customer(TenantMasterData):
    """项目引用的客户主数据。"""

    class Meta:
        db_table = "master_data_customer"
        ordering = ("code", "id")
        constraints = [
            models.UniqueConstraint(fields=("tenant", "code"), name="uq_customer_tenant_code"),
            models.UniqueConstraint(
                fields=("tenant", "normalized_name"), name="uq_customer_tenant_name"
            ),
        ]


class Unit(TenantMasterData):
    """数量的计量单位；本切片不自动换算不同单位。"""

    class Meta:
        db_table = "master_data_unit"
        ordering = ("code", "id")
        constraints = [
            models.UniqueConstraint(fields=("tenant", "code"), name="uq_unit_tenant_code"),
            models.UniqueConstraint(
                fields=("tenant", "normalized_name"), name="uq_unit_tenant_name"
            ),
        ]


class MaterialCategory(TenantMasterData):
    """物料分类，仅用于归类，不隐式决定是否可请购。"""

    class Meta:
        db_table = "master_data_material_category"
        ordering = ("code", "id")
        constraints = [
            models.UniqueConstraint(fields=("tenant", "code"), name="uq_category_tenant_code"),
            models.UniqueConstraint(
                fields=("tenant", "normalized_name"), name="uq_category_tenant_name"
            ),
        ]


class Material(TenantMasterData):
    """BOM 与请购引用的物料主数据。"""

    specification = models.CharField(max_length=200, blank=True)
    brand = models.CharField(max_length=100, blank=True)
    unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name="materials")
    category = models.ForeignKey(
        MaterialCategory, on_delete=models.PROTECT, related_name="materials"
    )
    procurement_required = models.BooleanField(default=True)

    class Meta:
        db_table = "master_data_material"
        ordering = ("code", "id")
        constraints = [
            models.UniqueConstraint(fields=("tenant", "code"), name="uq_material_tenant_code"),
        ]
