"""工作台跨模块只读查询层。

本层可以组合多个模块的 ORM 数据用于页面展示，但绝不写表或承载状态
规则。每个入口先解析当前权限范围，再按 tenant 和对象关系过滤，避免
模板通过直接 UUID 猜测泄露其他租户或无关项目。
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from django.core.exceptions import PermissionDenied
from django.db.models import QuerySet

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
from pms.procurement.infrastructure.django.models import PurchaseRequest, PurchaseRequestLine
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
    material_code: str
    material_name: str
    requested_quantity: Decimal
    unit: str


@dataclass(frozen=True, slots=True)
class RequestDetail:
    request: RequestItem
    project_id: UUID
    project_number: str
    production_id: UUID
    cancellation_reason: str
    lines: tuple[RequestLineItem, ...]


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
    lines = tuple(
        RequestLineItem(
            material_code=row.material_code_snapshot,
            material_name=row.material_name_snapshot,
            requested_quantity=row.requested_quantity,
            unit=row.unit.name,
        )
        for row in PurchaseRequestLine.objects.filter(
            tenant_id=context.tenant_id, purchase_request=request
        )
        .select_related("unit")
        .order_by("source_requirement_id", "id")
    )
    return RequestDetail(
        request=_request_item(request),
        project_id=request.project_id,
        project_number=request.project.number,
        production_id=request.production_release_id,
        cancellation_reason=request.cancellation_reason,
        lines=lines,
    )


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
