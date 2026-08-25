"""工作台跨模块只读查询层。

本层可以组合多个模块的 ORM 数据用于页面展示，但绝不写表或承载状态
规则。每个入口先解析当前权限范围，再按 tenant 和对象关系过滤，避免
模板通过直接 UUID 猜测泄露其他租户或无关项目。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from django.core.exceptions import PermissionDenied
from django.db.models import QuerySet, Sum

from pms.attachments.infrastructure.django.models import Attachment
from pms.authorization.domain.permissions import PermissionCode, PermissionScope
from pms.authorization.infrastructure.django.grant_lookup import DjangoPermissionGrantLookup
from pms.bom.domain.validation import ERROR_MESSAGES, BomLineErrorCode
from pms.bom.infrastructure.django.models import BomLine, BomVersion
from pms.master_data.infrastructure.django.models import (
    Customer,
    Material,
    MaterialCategory,
    Supplier,
    Unit,
)
from pms.procurement.domain.pricing import calculate_price_amounts
from pms.procurement.infrastructure.django.models import (
    PurchaseOrder,
    PurchaseOrderDocument,
    PurchaseRequest,
    PurchaseRequestLine,
    SupplierDecision,
    SupplierQuote,
)
from pms.production.infrastructure.django.models import ProductionRelease, ProductionRequirement
from pms.projects.infrastructure.django.models import Project
from pms.tenancy.domain.context import TenantContext
from pms.tenancy.infrastructure.django.models import Membership, Tenant

STATUS_LABELS = {
    "DRAFT": "草稿",
    "ACTIVE": "进行中",
    "CLOSED": "已关闭",
    "CANCELLED": "已取消",
    "PUBLISHED": "已发布",
    "SUPERSEDED": "已替代",
    "RELEASED": "已发布",
    "SUBMITTED": "已提交",
    "ISSUED": "已签发",
}


@dataclass(frozen=True, slots=True)
class Option:
    """表单下拉框使用的租户范围选项。"""

    id: UUID
    label: str


@dataclass(frozen=True, slots=True)
class DashboardData:
    """工作台首页的业务数量与最近项目。"""

    tenant_name: str
    project_count: int
    draft_bom_count: int
    released_production_count: int
    submitted_request_count: int
    recent_projects: tuple[ProjectItem, ...]


@dataclass(frozen=True, slots=True)
class CustomerItem:
    id: UUID
    code: str
    name: str
    is_active: bool


@dataclass(frozen=True, slots=True)
class SupplierItem:
    """供应商普通列表只展示联系与服务摘要，不暴露银行和税务资料。"""

    id: UUID
    code: str
    name: str
    contact_person: str
    phone: str
    service_description: str
    is_active: bool


@dataclass(frozen=True, slots=True)
class MaterialItem:
    id: UUID
    code: str
    name: str
    specification: str
    part_attribute: str
    unit: str
    category: str
    procurement_required: bool
    is_active: bool


@dataclass(frozen=True, slots=True)
class ProjectItem:
    id: UUID
    number: str
    customer_name: str
    device_model: str
    owner_name: str
    status: str
    status_label: str
    cancellation_reason: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class BomItem:
    id: UUID
    version_number: int
    status: str
    status_label: str
    line_count: int
    error_count: int
    source_filename: str
    cancellation_reason: str


@dataclass(frozen=True, slots=True)
class ProductionItem:
    id: UUID
    production_units: int
    production_unit: str
    receiving_department: str
    status: str
    status_label: str
    requirement_count: int
    cancellation_reason: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RequestItem:
    id: UUID
    request_number: str
    status: str
    status_label: str
    line_count: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ProjectDetail:
    project: ProjectItem
    customer_id: UUID
    start_date: date | None
    planned_completion_date: date | None
    boms: tuple[BomItem, ...]
    productions: tuple[ProductionItem, ...]
    requests: tuple[RequestItem, ...]


@dataclass(frozen=True, slots=True)
class BomLineItem:
    id: UUID
    source_row_number: int
    material_code: str
    material_name: str
    specification: str
    quantity_per_unit: Decimal | None
    unit_text: str
    procurement_required: bool
    remark: str
    errors: tuple[str, ...]
    error_messages: tuple[str, ...]
    duplicate_confirmed: bool


@dataclass(frozen=True, slots=True)
class BomDetail:
    bom: BomItem
    project_id: UUID
    project_number: str
    attachment_id: UUID
    lines: tuple[BomLineItem, ...]


@dataclass(frozen=True, slots=True)
class RequirementItem:
    material_code: str
    material_name: str
    quantity_per_unit: Decimal
    required_quantity: Decimal
    unit: str
    procurement_required: bool


@dataclass(frozen=True, slots=True)
class ProductionDetail:
    production: ProductionItem
    project_id: UUID
    project_number: str
    bom_id: UUID
    bom_version: int
    requirements: tuple[RequirementItem, ...]


@dataclass(frozen=True, slots=True)
class RequestLineItem:
    id: UUID
    material_code: str
    material_name: str
    requested_quantity: Decimal
    unit: str
    quotes: tuple[QuoteItem, ...]
    current_decision: DecisionItem | None


@dataclass(frozen=True, slots=True)
class QuoteItem:
    id: UUID
    supplier_name: str
    quote_date: date
    valid_until: date | None
    currency: str
    unit_price: Decimal
    tax_rate: Decimal
    tax_included: bool
    minimum_order_quantity: Decimal | None
    lead_time_days: int | None
    source_type: str
    source_reference: str
    remark: str
    status: str
    net_amount: Decimal
    tax_amount: Decimal
    gross_amount: Decimal


@dataclass(frozen=True, slots=True)
class DecisionItem:
    quote_id: UUID
    version: int
    supplier_name: str
    currency: str
    net_amount: Decimal
    tax_amount: Decimal
    gross_amount: Decimal


@dataclass(frozen=True, slots=True)
class RequestDetail:
    request: RequestItem
    project_id: UUID
    project_number: str
    production_id: UUID
    cancellation_reason: str
    lines: tuple[RequestLineItem, ...]
    can_view_pricing: bool
    can_manage_pricing: bool
    supplier_options: tuple[Option, ...]
    currency_totals: tuple[tuple[str, Decimal, Decimal], ...]
    undecided_line_count: int


@dataclass(frozen=True, slots=True)
class OrderItem:
    id: UUID
    order_number: str
    supplier_name: str
    kind: str
    status: str
    status_label: str
    currency: str
    line_count: int
    gross_amount: Decimal
    created_at: datetime


@dataclass(frozen=True, slots=True)
class OrderLineItem:
    project_code: str
    request_number: str
    material_code: str
    material_name: str
    part_attribute: str
    unit: str
    quantity: Decimal
    unit_price: Decimal
    tax_rate: Decimal
    net_amount: Decimal
    tax_amount: Decimal
    gross_amount: Decimal


@dataclass(frozen=True, slots=True)
class OrderDocumentItem:
    attachment_id: UUID
    version: int
    filename: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class OrderDetail:
    order: OrderItem
    cancellation_reason: str
    lines: tuple[OrderLineItem, ...]
    documents: tuple[OrderDocumentItem, ...]
    net_amount: Decimal
    tax_amount: Decimal
    can_manage: bool


def dashboard(context: TenantContext) -> DashboardData:
    projects = visible_project_queryset(context=context, permission=PermissionCode.PROJECT_VIEW)
    project_ids = projects.values_list("id", flat=True)
    recent = tuple(_project_item(row) for row in projects[:6])
    tenant_name = Tenant.objects.filter(id=context.tenant_id).values_list("name", flat=True).get()
    return DashboardData(
        tenant_name=tenant_name,
        project_count=projects.count(),
        draft_bom_count=BomVersion.objects.filter(
            tenant_id=context.tenant_id, project_id__in=project_ids, status="DRAFT"
        ).count(),
        released_production_count=ProductionRelease.objects.filter(
            tenant_id=context.tenant_id, project_id__in=project_ids, status="RELEASED"
        ).count(),
        submitted_request_count=PurchaseRequest.objects.filter(
            tenant_id=context.tenant_id, project_id__in=project_ids, status="SUBMITTED"
        ).count(),
        recent_projects=recent,
    )


def customers(context: TenantContext) -> tuple[CustomerItem, ...]:
    scope = _scope(context, PermissionCode.CUSTOMER_VIEW)
    rows = Customer.objects.filter(tenant_id=context.tenant_id)
    if scope is PermissionScope.RELATED:
        rows = rows.filter(projects__owner_membership_id=context.membership_id).distinct()
    return tuple(
        CustomerItem(id=row.id, code=row.code, name=row.name, is_active=row.is_active)
        for row in rows.order_by("code", "id")
    )


def suppliers(context: TenantContext) -> tuple[SupplierItem, ...]:
    """返回当前租户供应商；供应商权限只定义 tenant 范围。"""
    _scope(context, PermissionCode.SUPPLIER_VIEW)
    rows = Supplier.objects.filter(tenant_id=context.tenant_id)
    return tuple(
        SupplierItem(
            id=row.id,
            code=row.code,
            name=row.name,
            contact_person=row.contact_person,
            phone=row.phone,
            service_description=row.service_description,
            is_active=row.is_active,
        )
        for row in rows.order_by("code", "id")
    )


def materials(context: TenantContext) -> tuple[MaterialItem, ...]:
    scope = _scope(context, PermissionCode.MATERIAL_VIEW)
    rows = Material.objects.filter(tenant_id=context.tenant_id).select_related("unit", "category")
    if scope is PermissionScope.RELATED:
        rows = rows.filter(
            bom_lines__bom_version__project__owner_membership_id=context.membership_id
        ).distinct()
    return tuple(
        MaterialItem(
            id=row.id,
            code=row.code,
            name=row.name,
            specification=row.specification,
            part_attribute=row.part_attribute,
            unit=row.unit.name,
            category=row.category.name,
            procurement_required=row.procurement_required,
            is_active=row.is_active,
        )
        for row in rows.order_by("code", "id")
    )


def master_options(context: TenantContext) -> dict[str, tuple[Option, ...]]:
    """返回写表单选项；调用方必须先执行对应 manage/create 授权。"""
    tenant_id = context.tenant_id
    return {
        "customers": tuple(
            Option(row.id, f"{row.code} · {row.name}")
            for row in Customer.objects.filter(tenant_id=tenant_id, is_active=True).order_by(
                "code", "id"
            )
        ),
        "units": tuple(
            Option(row.id, f"{row.code} · {row.name}")
            for row in Unit.objects.filter(tenant_id=tenant_id, is_active=True).order_by(
                "code", "id"
            )
        ),
        "categories": tuple(
            Option(row.id, f"{row.code} · {row.name}")
            for row in MaterialCategory.objects.filter(
                tenant_id=tenant_id, is_active=True
            ).order_by("code", "id")
        ),
        "memberships": tuple(
            Option(row.id, row.user.username)
            for row in Membership.objects.filter(
                tenant_id=tenant_id, is_active=True, user__is_active=True
            )
            .select_related("user")
            .order_by("user__username", "id")
        ),
        "materials": tuple(
            Option(row.id, f"{row.code} · {row.name}")
            for row in Material.objects.filter(tenant_id=tenant_id, is_active=True).order_by(
                "code", "id"
            )
        ),
    }


def visible_material_options(context: TenantContext) -> tuple[Option, ...]:
    """返回当前成员可见的有效物料选项。

    BOM 行确认页也可能被只读角色访问，因此不能复用写表单专用的
    ``master_options`` 绕过 material.view 的范围判断。这里先复用正式
    查询策略，再把展示 DTO 收窄为下拉框选项。
    """
    return tuple(
        Option(item.id, f"{item.code} · {item.name}")
        for item in materials(context)
        if item.is_active
    )


def projects(context: TenantContext) -> tuple[ProjectItem, ...]:
    return tuple(
        _project_item(row)
        for row in visible_project_queryset(context=context, permission=PermissionCode.PROJECT_VIEW)
    )


def project_detail(context: TenantContext, project_id: UUID) -> ProjectDetail | None:
    row = (
        visible_project_queryset(context=context, permission=PermissionCode.PROJECT_VIEW)
        .filter(id=project_id)
        .first()
    )
    if row is None:
        return None
    bom_rows = BomVersion.objects.filter(
        tenant_id=context.tenant_id, project_id=row.id
    ).select_related("source_attachment")
    production_rows = ProductionRelease.objects.filter(
        tenant_id=context.tenant_id, project_id=row.id
    )
    request_rows = PurchaseRequest.objects.filter(tenant_id=context.tenant_id, project_id=row.id)
    return ProjectDetail(
        project=_project_item(row),
        customer_id=row.customer_id,
        start_date=row.start_date,
        planned_completion_date=row.planned_completion_date,
        boms=tuple(_bom_item(bom) for bom in bom_rows),
        productions=tuple(_production_item(item) for item in production_rows),
        requests=tuple(_request_item(item) for item in request_rows),
    )


def bom_detail(context: TenantContext, bom_id: UUID) -> BomDetail | None:
    allowed_projects = visible_project_queryset(
        context=context, permission=PermissionCode.BOM_VIEW
    ).values_list("id", flat=True)
    bom = (
        BomVersion.objects.filter(
            id=bom_id,
            tenant_id=context.tenant_id,
            project_id__in=allowed_projects,
        )
        .select_related("project", "source_attachment")
        .first()
    )
    if bom is None:
        return None
    lines = tuple(
        BomLineItem(
            id=line.id,
            source_row_number=line.source_row_number,
            material_code=line.material_code,
            material_name=line.material_name,
            specification=line.specification,
            quantity_per_unit=line.quantity_per_unit,
            unit_text=line.unit_text,
            procurement_required=line.procurement_required,
            remark=line.remark,
            errors=tuple(map(str, line.validation_errors)),
            error_messages=tuple(_bom_error_message(str(code)) for code in line.validation_errors),
            duplicate_confirmed=line.duplicate_confirmed,
        )
        for line in BomLine.objects.filter(tenant_id=context.tenant_id, bom_version=bom).order_by(
            "source_row_number", "id"
        )
    )
    return BomDetail(
        bom=_bom_item(bom),
        project_id=bom.project_id,
        project_number=bom.project.number,
        attachment_id=bom.source_attachment_id,
        lines=lines,
    )


def production_detail(context: TenantContext, production_id: UUID) -> ProductionDetail | None:
    allowed_projects = visible_project_queryset(
        context=context, permission=PermissionCode.PRODUCTION_RELEASE_VIEW
    ).values_list("id", flat=True)
    production = (
        ProductionRelease.objects.filter(
            id=production_id,
            tenant_id=context.tenant_id,
            project_id__in=allowed_projects,
        )
        .select_related("project", "bom_version")
        .first()
    )
    if production is None:
        return None
    requirements = tuple(
        RequirementItem(
            material_code=row.material_code_snapshot,
            material_name=row.material_name_snapshot,
            quantity_per_unit=row.quantity_per_unit,
            required_quantity=row.required_quantity,
            unit=row.unit.name,
            procurement_required=row.procurement_required,
        )
        for row in ProductionRequirement.objects.filter(
            tenant_id=context.tenant_id, production_release=production
        )
        .select_related("unit")
        .order_by("source_bom_line__source_row_number", "id")
    )
    return ProductionDetail(
        production=_production_item(production),
        project_id=production.project_id,
        project_number=production.project.number,
        bom_id=production.bom_version_id,
        bom_version=production.bom_version.version_number,
        requirements=requirements,
    )


def request_detail(context: TenantContext, request_id: UUID) -> RequestDetail | None:
    allowed_projects = visible_project_queryset(
        context=context, permission=PermissionCode.PURCHASE_REQUEST_VIEW
    ).values_list("id", flat=True)
    request = (
        PurchaseRequest.objects.filter(
            id=request_id,
            tenant_id=context.tenant_id,
            project_id__in=allowed_projects,
        )
        .select_related("project")
        .first()
    )
    if request is None:
        return None
    can_view_pricing = _has_scope(context, PermissionCode.PURCHASE_QUOTE_VIEW)
    can_manage_pricing = _has_scope(context, PermissionCode.PURCHASE_QUOTE_MANAGE)
    request_lines = list(
        PurchaseRequestLine.objects.filter(tenant_id=context.tenant_id, purchase_request=request)
        .select_related("unit")
        .order_by("source_requirement_id", "id")
    )
    quote_map: dict[UUID, list[QuoteItem]] = {line.id: [] for line in request_lines}
    decision_map: dict[UUID, DecisionItem] = {}
    if can_view_pricing:
        for quote in SupplierQuote.objects.filter(
            tenant_id=context.tenant_id, request_line__purchase_request=request
        ).select_related("supplier", "request_line"):
            amounts = calculate_price_amounts(
                quantity=quote.request_line.requested_quantity,
                unit_price=quote.unit_price,
                tax_rate=quote.tax_rate,
                tax_included=quote.tax_included,
            )
            quote_map[quote.request_line_id].append(
                QuoteItem(
                    id=quote.id,
                    supplier_name=quote.supplier.name,
                    quote_date=quote.quote_date,
                    valid_until=quote.valid_until,
                    currency=quote.currency,
                    unit_price=quote.unit_price,
                    tax_rate=quote.tax_rate,
                    tax_included=quote.tax_included,
                    minimum_order_quantity=quote.minimum_order_quantity,
                    lead_time_days=quote.lead_time_days,
                    source_type=quote.source_type,
                    source_reference=quote.source_reference,
                    remark=quote.remark,
                    status=quote.status,
                    net_amount=amounts.net_amount,
                    tax_amount=amounts.tax_amount,
                    gross_amount=amounts.gross_amount,
                )
            )
        for decision_row in SupplierDecision.objects.filter(
            tenant_id=context.tenant_id,
            request_line__purchase_request=request,
            is_current=True,
        ):
            decision_map[decision_row.request_line_id] = DecisionItem(
                quote_id=decision_row.quote_id,
                version=decision_row.version,
                supplier_name=decision_row.supplier_name_snapshot,
                currency=decision_row.currency,
                net_amount=decision_row.net_amount,
                tax_amount=decision_row.tax_amount,
                gross_amount=decision_row.gross_amount,
            )
    lines = tuple(
        RequestLineItem(
            id=row.id,
            material_code=row.material_code_snapshot,
            material_name=row.material_name_snapshot,
            requested_quantity=row.requested_quantity,
            unit=row.unit.name,
            quotes=tuple(quote_map[row.id]),
            current_decision=decision_map.get(row.id),
        )
        for row in request_lines
    )
    totals: dict[str, tuple[Decimal, Decimal]] = {}
    for current_decision in decision_map.values():
        net, gross = totals.get(current_decision.currency, (Decimal("0"), Decimal("0")))
        totals[current_decision.currency] = (
            net + current_decision.net_amount,
            gross + current_decision.gross_amount,
        )
    supplier_options = (
        tuple(
            Option(id=row.id, label=f"{row.code} · {row.name}")
            for row in Supplier.objects.filter(tenant_id=context.tenant_id, is_active=True)
        )
        if can_manage_pricing
        else ()
    )
    return RequestDetail(
        request=_request_item(request),
        project_id=request.project_id,
        project_number=request.project.number,
        production_id=request.production_release_id,
        cancellation_reason=request.cancellation_reason,
        lines=lines,
        can_view_pricing=can_view_pricing,
        can_manage_pricing=can_manage_pricing,
        supplier_options=supplier_options,
        currency_totals=tuple(
            (currency, amounts[0], amounts[1]) for currency, amounts in sorted(totals.items())
        ),
        undecided_line_count=len(request_lines) - len(decision_map),
    )


def purchase_orders(context: TenantContext) -> tuple[OrderItem, ...]:
    """返回租户或相关项目范围内的正式订单列表。"""
    scope = _scope(context, PermissionCode.PURCHASE_ORDER_VIEW)
    rows = PurchaseOrder.objects.filter(tenant_id=context.tenant_id)
    if scope is PermissionScope.RELATED:
        rows = rows.filter(
            lines__request_line__purchase_request__project__owner_membership_id=context.membership_id
        ).distinct()
    return tuple(_order_item(row) for row in rows)


def purchase_order_detail(context: TenantContext, order_id: UUID) -> OrderDetail | None:
    """读取订单冻结内容；相关范围通过任一关联项目判断。"""
    scope = _scope(context, PermissionCode.PURCHASE_ORDER_VIEW)
    rows = PurchaseOrder.objects.filter(id=order_id, tenant_id=context.tenant_id)
    if scope is PermissionScope.RELATED:
        rows = rows.filter(
            lines__request_line__purchase_request__project__owner_membership_id=context.membership_id
        )
    order = rows.distinct().first()
    if order is None:
        return None
    lines = tuple(
        OrderLineItem(
            project_code=row.project_code_snapshot,
            request_number=row.request_number_snapshot,
            material_code=row.material_code_snapshot,
            material_name=row.material_name_snapshot,
            part_attribute=row.part_attribute_snapshot,
            unit=row.unit_name_snapshot,
            quantity=row.quantity,
            unit_price=row.unit_price,
            tax_rate=row.tax_rate,
            net_amount=row.net_amount,
            tax_amount=row.tax_amount,
            gross_amount=row.gross_amount,
        )
        for row in order.lines.all()
    )
    return OrderDetail(
        order=_order_item(order),
        cancellation_reason=order.cancellation_reason,
        lines=lines,
        documents=tuple(
            OrderDocumentItem(
                attachment_id=row.attachment_id,
                version=row.version,
                filename=row.attachment.original_filename,
                created_at=row.created_at,
            )
            for row in order.documents.select_related("attachment")
        ),
        net_amount=sum((line.net_amount for line in lines), Decimal("0.00")),
        tax_amount=sum((line.tax_amount for line in lines), Decimal("0.00")),
        can_manage=_has_scope(context, PermissionCode.PURCHASE_ORDER_MANAGE),
    )


def attachment_for_order(context: TenantContext, attachment_id: UUID) -> Attachment | None:
    """只允许通过可见正式订单下载其版本化文档。"""
    scope = _scope(context, PermissionCode.PURCHASE_ORDER_VIEW)
    documents = PurchaseOrderDocument.objects.filter(
        tenant_id=context.tenant_id, attachment_id=attachment_id
    )
    if scope is PermissionScope.RELATED:
        documents = documents.filter(
            order__lines__request_line__purchase_request__project__owner_membership_id=context.membership_id
        )
    document = documents.select_related("attachment").distinct().first()
    return document.attachment if document and document.attachment.status == "available" else None


def attachment_for_bom(context: TenantContext, attachment_id: UUID) -> Attachment | None:
    allowed_projects = visible_project_queryset(
        context=context, permission=PermissionCode.ATTACHMENT_DOWNLOAD
    ).values_list("id", flat=True)
    return Attachment.objects.filter(
        id=attachment_id,
        tenant_id=context.tenant_id,
        source_bom_versions__project_id__in=allowed_projects,
        status="available",
    ).first()


def visible_project_queryset(
    *, context: TenantContext, permission: PermissionCode
) -> QuerySet[Project]:
    """返回当前权限范围可见项目 QuerySet；只供本只读模块内部组合。"""
    scope = _scope(context, permission)
    rows = Project.objects.filter(tenant_id=context.tenant_id).select_related(
        "customer", "owner_membership__user"
    )
    if scope is PermissionScope.RELATED:
        rows = rows.filter(owner_membership_id=context.membership_id)
    return rows.order_by("-created_at", "-id")


def _scope(context: TenantContext, permission: PermissionCode) -> PermissionScope:
    scope = DjangoPermissionGrantLookup().find_scope(
        membership_id=context.membership_id, permission=permission
    )
    if scope is None:
        raise PermissionDenied("当前成员无权查看该内容。")
    return scope


def _has_scope(context: TenantContext, permission: PermissionCode) -> bool:
    """页面可选区块使用的无副作用权限探测；写操作仍由应用服务授权。"""
    return (
        DjangoPermissionGrantLookup().find_scope(
            membership_id=context.membership_id, permission=permission
        )
        is not None
    )


def _project_item(row: Project) -> ProjectItem:
    return ProjectItem(
        id=row.id,
        number=row.number,
        customer_name=row.customer.name,
        device_model=row.device_model,
        owner_name=row.owner_membership.user.username,
        status=row.status,
        status_label=STATUS_LABELS.get(row.status, row.status),
        cancellation_reason=row.cancellation_reason,
        created_at=row.created_at,
    )


def _order_item(row: PurchaseOrder) -> OrderItem:
    totals = row.lines.aggregate(value=Sum("gross_amount"))
    return OrderItem(
        id=row.id,
        order_number=row.order_number or "草稿（未编号）",
        supplier_name=row.supplier_name_snapshot,
        kind=row.kind,
        status=row.status,
        status_label=STATUS_LABELS.get(row.status, row.status),
        currency=row.currency,
        line_count=row.lines.count(),
        gross_amount=totals["value"] or Decimal("0.00"),
        created_at=row.created_at,
    )


def _bom_error_message(code: str) -> str:
    """把稳定持久化代码转换为中文；未知历史代码仍可原样诊断。"""
    try:
        return ERROR_MESSAGES[BomLineErrorCode(code)]
    except KeyError, ValueError:
        return code


def _bom_item(row: BomVersion) -> BomItem:
    line_errors = list(row.lines.values_list("validation_errors", flat=True))
    return BomItem(
        id=row.id,
        version_number=row.version_number,
        status=row.status,
        status_label=STATUS_LABELS.get(row.status, row.status),
        line_count=len(line_errors),
        error_count=sum(len(errors) for errors in line_errors),
        source_filename=row.source_attachment.original_filename,
        cancellation_reason=row.cancellation_reason,
    )


def _production_item(row: ProductionRelease) -> ProductionItem:
    return ProductionItem(
        id=row.id,
        production_units=row.production_units,
        production_unit=row.production_unit,
        receiving_department=row.receiving_department,
        status=row.status,
        status_label=STATUS_LABELS.get(row.status, row.status),
        requirement_count=row.requirements.count(),
        cancellation_reason=row.cancellation_reason,
        created_at=row.created_at,
    )


def _request_item(row: PurchaseRequest) -> RequestItem:
    return RequestItem(
        id=row.id,
        request_number=row.request_number or "待提交",
        status=row.status,
        status_label=STATUS_LABELS.get(row.status, row.status),
        line_count=row.lines.count(),
        created_at=row.created_at,
    )
