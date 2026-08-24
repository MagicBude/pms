"""客户、供应商、单位、分类和物料的租户级 ORM 映射。"""

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
    """项目引用的客户主数据，以及开票和收款所需的组织资料。"""

    short_name = models.CharField(max_length=100, blank=True)
    tax_identifier = models.CharField(max_length=64, blank=True)
    address = models.CharField(max_length=300, blank=True)
    phone = models.CharField(max_length=64, blank=True)
    bank_name = models.CharField(max_length=200, blank=True)
    bank_account = models.CharField(max_length=64, blank=True)
    bank_routing_number = models.CharField(max_length=64, blank=True)

    class Meta:
        db_table = "master_data_customer"
        ordering = ("code", "id")
        constraints = [
            models.UniqueConstraint(fields=("tenant", "code"), name="uq_customer_tenant_code"),
            models.UniqueConstraint(
                fields=("tenant", "normalized_name"), name="uq_customer_tenant_name"
            ),
        ]


class Supplier(TenantMasterData):
    """询价、采购订单和付款引用的供应商组织档案。

    银行和税务字段属于业务敏感数据：可以由获授权用例保存，但不得进入
    普通列表、审计摘要或结构化日志。供应商代码与规范化全称共同形成租户
    内唯一边界，旧系统中的简称直接迁移为稳定代码。
    """

    short_name = models.CharField(max_length=100, blank=True)
    contact_person = models.CharField(max_length=100, blank=True)
    phone = models.CharField(max_length=64, blank=True)
    address = models.CharField(max_length=300, blank=True)
    tax_identifier = models.CharField(max_length=64, blank=True)
    bank_routing_number = models.CharField(max_length=64, blank=True)
    bank_name = models.CharField(max_length=200, blank=True)
    bank_account = models.CharField(max_length=64, blank=True)
    service_description = models.CharField(max_length=200, blank=True)
    english_name = models.CharField(max_length=200, blank=True)
    english_address = models.CharField(max_length=300, blank=True)

    class Meta:
        db_table = "master_data_supplier"
        ordering = ("code", "id")
        constraints = [
            models.UniqueConstraint(fields=("tenant", "code"), name="uq_supplier_tenant_code"),
            models.UniqueConstraint(
                fields=("tenant", "normalized_name"), name="uq_supplier_tenant_name"
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
    part_attribute = models.CharField(max_length=100, blank=True)
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
