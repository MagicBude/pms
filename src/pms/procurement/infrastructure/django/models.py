"""生产请购、来源行和租户日期序列的 ORM 映射。"""

import uuid

from django.db import models

from pms.attachments.infrastructure.django.models import Attachment
from pms.master_data.infrastructure.django.models import Material, MaterialDrawing, Supplier, Unit
from pms.procurement.domain.orders import PurchaseOrderKind, PurchaseOrderStatus
from pms.procurement.domain.pricing import Currency, QuoteSource, QuoteStatus
from pms.procurement.domain.request import PurchaseRequestStatus
from pms.production.infrastructure.django.models import (
    ProductionRelease,
    ProductionRequirement,
)
from pms.projects.infrastructure.django.models import Project
from pms.tenancy.infrastructure.django.models import Membership, Tenant


class PurchaseRequest(models.Model):
    """由一个投产批次生成、提交后取得正式编号的生产请购。"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="purchase_requests")
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="purchase_requests")
    production_release = models.ForeignKey(
        ProductionRelease, on_delete=models.PROTECT, related_name="purchase_requests"
    )
    idempotency_key = models.CharField(max_length=128)
    request_number = models.CharField(max_length=32, null=True, blank=True)
    status = models.CharField(
        max_length=16,
        choices=[(status.value, status.value) for status in PurchaseRequestStatus],
        default=PurchaseRequestStatus.DRAFT.value,
    )
    created_by_membership = models.ForeignKey(
        Membership, on_delete=models.PROTECT, related_name="created_purchase_requests"
    )
    submitted_by_membership = models.ForeignKey(
        Membership,
        on_delete=models.PROTECT,
        related_name="submitted_purchase_requests",
        null=True,
        blank=True,
    )
    cancelled_by_membership = models.ForeignKey(
        Membership,
        on_delete=models.PROTECT,
        related_name="cancelled_purchase_requests",
        null=True,
        blank=True,
    )
    cancellation_reason = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "procurement_purchase_request"
        ordering = ("-created_at", "-id")
        constraints = [
            models.UniqueConstraint(
                fields=("tenant", "idempotency_key"), name="uq_request_tenant_idempotency"
            ),
            models.UniqueConstraint(
                fields=("tenant", "request_number"), name="uq_request_tenant_number"
            ),
            models.UniqueConstraint(
                fields=("tenant", "production_release"),
                condition=~models.Q(status=PurchaseRequestStatus.CANCELLED.value),
                name="uq_request_active_production",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=[status.value for status in PurchaseRequestStatus]),
                name="ck_request_status_valid",
            ),
        ]


class PurchaseRequestLine(models.Model):
    """引用投产需求并冻结物料、单位和申请数量的请购行。"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    tenant = models.ForeignKey(
        Tenant, on_delete=models.PROTECT, related_name="purchase_request_lines"
    )
    purchase_request = models.ForeignKey(
        PurchaseRequest, on_delete=models.CASCADE, related_name="lines"
    )
    source_requirement = models.ForeignKey(
        ProductionRequirement, on_delete=models.PROTECT, related_name="purchase_request_lines"
    )
    material = models.ForeignKey(
        Material, on_delete=models.PROTECT, related_name="purchase_request_lines"
    )
    material_code_snapshot = models.CharField(max_length=64)
    material_name_snapshot = models.CharField(max_length=200)
    unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name="purchase_request_lines")
    requested_quantity = models.DecimalField(max_digits=24, decimal_places=6)
    remark = models.CharField(max_length=500, blank=True)

    class Meta:
        db_table = "procurement_purchase_request_line"
        ordering = ("source_requirement_id", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("purchase_request", "source_requirement"),
                name="uq_request_line_source",
            ),
            models.CheckConstraint(
                condition=models.Q(requested_quantity__gt=0),
                name="ck_request_line_quantity_positive",
            ),
        ]


class PurchaseRequestSequence(models.Model):
    """一个租户业务日期内的最后分配序号。

    行锁与唯一约束共同保护并发分配。允许事务安全导致序号缺口，但绝不
    为追求连续号而把编号放到请购事务之外。
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    tenant = models.ForeignKey(
        Tenant, on_delete=models.PROTECT, related_name="purchase_request_sequences"
    )
    business_date = models.DateField()
    last_value = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "procurement_purchase_request_sequence"
        constraints = [
            models.UniqueConstraint(
                fields=("tenant", "business_date"), name="uq_request_sequence_tenant_date"
            )
        ]


class SupplierQuote(models.Model):
    """一条不可变供应商报价；错误事实通过撤销状态保留。"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="supplier_quotes")
    request_line = models.ForeignKey(
        PurchaseRequestLine, on_delete=models.PROTECT, related_name="supplier_quotes"
    )
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="quotes")
    quote_date = models.DateField()
    valid_until = models.DateField(null=True, blank=True)
    currency = models.CharField(
        max_length=3, choices=[(item.value, item.value) for item in Currency]
    )
    unit_price = models.DecimalField(max_digits=24, decimal_places=6)
    tax_rate = models.DecimalField(max_digits=7, decimal_places=4)
    tax_included = models.BooleanField(default=True)
    minimum_order_quantity = models.DecimalField(
        max_digits=24, decimal_places=6, null=True, blank=True
    )
    lead_time_days = models.PositiveIntegerField(null=True, blank=True)
    source_type = models.CharField(
        max_length=16, choices=[(item.value, item.value) for item in QuoteSource]
    )
    source_reference = models.CharField(max_length=100, blank=True)
    remark = models.CharField(max_length=500, blank=True)
    status = models.CharField(
        max_length=16,
        choices=[(item.value, item.value) for item in QuoteStatus],
        default=QuoteStatus.ACTIVE.value,
    )
    created_by_membership = models.ForeignKey(
        Membership, on_delete=models.PROTECT, related_name="created_supplier_quotes"
    )
    withdrawn_by_membership = models.ForeignKey(
        Membership,
        on_delete=models.PROTECT,
        related_name="withdrawn_supplier_quotes",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    withdrawn_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "procurement_supplier_quote"
        ordering = ("-created_at", "-id")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(unit_price__gt=0), name="ck_supplier_quote_price_positive"
            ),
            models.CheckConstraint(
                condition=models.Q(tax_rate__gte=0, tax_rate__lte=100),
                name="ck_supplier_quote_tax_range",
            ),
            models.CheckConstraint(
                condition=models.Q(minimum_order_quantity__isnull=True)
                | models.Q(minimum_order_quantity__gt=0),
                name="ck_supplier_quote_minimum_positive",
            ),
        ]


class SupplierDecision(models.Model):
    """追加式供应商确定版本及当时采用的完整价格快照。"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="supplier_decisions")
    request_line = models.ForeignKey(
        PurchaseRequestLine, on_delete=models.PROTECT, related_name="supplier_decisions"
    )
    quote = models.ForeignKey(
        SupplierQuote, on_delete=models.PROTECT, related_name="supplier_decisions"
    )
    version = models.PositiveIntegerField()
    is_current = models.BooleanField(default=True)
    supplier_code_snapshot = models.CharField(max_length=64)
    supplier_name_snapshot = models.CharField(max_length=200)
    currency = models.CharField(max_length=3)
    unit_price = models.DecimalField(max_digits=24, decimal_places=6)
    tax_rate = models.DecimalField(max_digits=7, decimal_places=4)
    tax_included = models.BooleanField()
    requested_quantity = models.DecimalField(max_digits=24, decimal_places=6)
    net_amount = models.DecimalField(max_digits=30, decimal_places=2)
    tax_amount = models.DecimalField(max_digits=30, decimal_places=2)
    gross_amount = models.DecimalField(max_digits=30, decimal_places=2)
    decided_by_membership = models.ForeignKey(
        Membership, on_delete=models.PROTECT, related_name="supplier_decisions"
    )
    decided_at = models.DateTimeField(auto_now_add=True)
    superseded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "procurement_supplier_decision"
        ordering = ("request_line_id", "-version")
        constraints = [
            models.UniqueConstraint(
                fields=("request_line", "version"), name="uq_supplier_decision_line_version"
            ),
            models.UniqueConstraint(
                fields=("request_line",),
                condition=models.Q(is_current=True),
                name="uq_supplier_decision_line_current",
            ),
            models.CheckConstraint(
                condition=models.Q(version__gt=0), name="ck_supplier_decision_version_positive"
            ),
        ]


class PurchaseOrder(models.Model):
    """供应商与币种一致的正式订单头；业务事实不依赖导出文件存在。"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="purchase_orders")
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="purchase_orders")
    supplier_code_snapshot = models.CharField(max_length=64)
    supplier_name_snapshot = models.CharField(max_length=200)
    currency = models.CharField(max_length=3)
    kind = models.CharField(
        max_length=16, choices=[(item.value, item.value) for item in PurchaseOrderKind]
    )
    status = models.CharField(
        max_length=16,
        choices=[(item.value, item.value) for item in PurchaseOrderStatus],
        default=PurchaseOrderStatus.DRAFT.value,
    )
    order_number = models.CharField(max_length=32, null=True, blank=True)
    created_by_membership = models.ForeignKey(
        Membership, on_delete=models.PROTECT, related_name="created_purchase_orders"
    )
    issued_by_membership = models.ForeignKey(
        Membership,
        on_delete=models.PROTECT,
        related_name="issued_purchase_orders",
        null=True,
        blank=True,
    )
    cancelled_by_membership = models.ForeignKey(
        Membership,
        on_delete=models.PROTECT,
        related_name="cancelled_purchase_orders",
        null=True,
        blank=True,
    )
    cancellation_reason = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    issued_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "procurement_purchase_order"
        ordering = ("-created_at", "-id")
        constraints = [
            models.UniqueConstraint(
                fields=("tenant", "order_number"), name="uq_order_tenant_number"
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=[item.value for item in PurchaseOrderStatus]),
                name="ck_order_status_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(kind__in=[item.value for item in PurchaseOrderKind]),
                name="ck_order_kind_valid",
            ),
        ]


class PurchaseOrderLine(models.Model):
    """引用确定版本并冻结下单时全部商业与物料快照。"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    tenant = models.ForeignKey(
        Tenant, on_delete=models.PROTECT, related_name="purchase_order_lines"
    )
    order = models.ForeignKey(PurchaseOrder, on_delete=models.PROTECT, related_name="lines")
    decision = models.ForeignKey(
        SupplierDecision, on_delete=models.PROTECT, related_name="order_lines"
    )
    request_line = models.ForeignKey(
        PurchaseRequestLine, on_delete=models.PROTECT, related_name="order_lines"
    )
    is_active = models.BooleanField(default=True)
    project_code_snapshot = models.CharField(max_length=64)
    request_number_snapshot = models.CharField(max_length=32)
    material_code_snapshot = models.CharField(max_length=64)
    material_name_snapshot = models.CharField(max_length=200)
    part_attribute_snapshot = models.CharField(max_length=32, blank=True)
    unit_name_snapshot = models.CharField(max_length=64)
    quantity = models.DecimalField(max_digits=24, decimal_places=6)
    unit_price = models.DecimalField(max_digits=24, decimal_places=6)
    tax_rate = models.DecimalField(max_digits=7, decimal_places=4)
    tax_included = models.BooleanField()
    net_amount = models.DecimalField(max_digits=30, decimal_places=2)
    tax_amount = models.DecimalField(max_digits=30, decimal_places=2)
    gross_amount = models.DecimalField(max_digits=30, decimal_places=2)
    remark_snapshot = models.CharField(max_length=500, blank=True)

    class Meta:
        db_table = "procurement_purchase_order_line"
        ordering = ("id",)
        constraints = [
            models.UniqueConstraint(
                fields=("request_line",),
                condition=models.Q(is_active=True),
                name="uq_order_line_request_active",
            ),
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0), name="ck_order_line_quantity_positive"
            ),
        ]


class PurchaseOrderSequence(models.Model):
    """租户、业务日期和订单类型内的签发序号。"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="order_sequences")
    business_date = models.DateField()
    kind = models.CharField(max_length=16)
    last_value = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "procurement_purchase_order_sequence"
        constraints = [
            models.UniqueConstraint(
                fields=("tenant", "business_date", "kind"),
                name="uq_order_sequence_tenant_date_kind",
            )
        ]


class PurchaseOrderDocument(models.Model):
    """一次订单 Excel 生成结果；新版本追加而不覆盖旧附件。"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="order_documents")
    order = models.ForeignKey(PurchaseOrder, on_delete=models.PROTECT, related_name="documents")
    attachment = models.OneToOneField(
        Attachment, on_delete=models.PROTECT, related_name="purchase_order_document"
    )
    version = models.PositiveIntegerField()
    created_by_membership = models.ForeignKey(
        Membership, on_delete=models.PROTECT, related_name="created_order_documents"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "procurement_purchase_order_document"
        ordering = ("-version",)
        constraints = [
            models.UniqueConstraint(fields=("order", "version"), name="uq_order_document_version"),
            models.CheckConstraint(
                condition=models.Q(version__gt=0), name="ck_order_document_version_positive"
            ),
        ]


class PurchaseOrderDrawingPackage(models.Model):
    """一次订单图纸 ZIP 生成结果及冻结清单的聚合根。"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="drawing_packages")
    order = models.ForeignKey(
        PurchaseOrder, on_delete=models.PROTECT, related_name="drawing_packages"
    )
    attachment = models.OneToOneField(
        Attachment, on_delete=models.PROTECT, related_name="purchase_order_drawing_package"
    )
    version = models.PositiveIntegerField()
    included_file_count = models.PositiveIntegerField()
    missing_material_count = models.PositiveIntegerField()
    created_by_membership = models.ForeignKey(
        Membership, on_delete=models.PROTECT, related_name="created_drawing_packages"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "procurement_purchase_order_drawing_package"
        ordering = ("-version",)
        constraints = [
            models.UniqueConstraint(
                fields=("order", "version"), name="uq_order_drawing_package_version"
            ),
            models.CheckConstraint(
                condition=models.Q(version__gt=0), name="ck_drawing_package_version_positive"
            ),
            models.CheckConstraint(
                condition=models.Q(included_file_count__gt=0),
                name="ck_drawing_package_included_positive",
            ),
        ]


class PurchaseOrderDrawingPackageItem(models.Model):
    """图纸包生成时冻结的具体图纸版本、路径和完整性元数据。"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    package = models.ForeignKey(
        PurchaseOrderDrawingPackage, on_delete=models.PROTECT, related_name="items"
    )
    drawing = models.ForeignKey(
        MaterialDrawing, on_delete=models.PROTECT, related_name="package_items"
    )
    material_code_snapshot = models.CharField(max_length=64)
    material_name_snapshot = models.CharField(max_length=200)
    document_format = models.CharField(max_length=8)
    drawing_version = models.PositiveIntegerField()
    revision_label = models.CharField(max_length=64, blank=True)
    archive_path = models.CharField(max_length=500)
    size_bytes = models.PositiveBigIntegerField()
    sha256_hex = models.CharField(max_length=64)

    class Meta:
        db_table = "procurement_purchase_order_drawing_package_item"
        constraints = [
            models.UniqueConstraint(
                fields=("package", "drawing"), name="uq_drawing_package_item_drawing"
            )
        ]


class PurchaseOrderDrawingPackageMissing(models.Model):
    """生成时没有任何当前图纸的订单物料，避免部分包被误认为完整。"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    package = models.ForeignKey(
        PurchaseOrderDrawingPackage, on_delete=models.PROTECT, related_name="missing_materials"
    )
    material = models.ForeignKey(
        Material, on_delete=models.PROTECT, related_name="missing_packages"
    )
    material_code_snapshot = models.CharField(max_length=64)
    material_name_snapshot = models.CharField(max_length=200)

    class Meta:
        db_table = "procurement_purchase_order_drawing_package_missing"
        constraints = [
            models.UniqueConstraint(
                fields=("package", "material"), name="uq_drawing_package_missing_material"
            )
        ]
