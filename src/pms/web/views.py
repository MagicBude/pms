"""PMS 本机工作台 HTTP 入口。

视图只完成会话、表单、消息和重定向。所有业务写操作调用应用服务；跨
模块列表与详情调用只读查询层，不直接在视图中保存 ORM 模型。
"""

import uuid
from contextlib import suppress
from datetime import date
from decimal import Decimal
from typing import cast
from uuid import UUID

from django.contrib import messages
from django.contrib.auth import login as django_login
from django.contrib.auth import logout as django_logout
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import UploadedFile
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from pms.attachments.domain.attachments import AttachmentId
from pms.authorization.application.authorize import PermissionDeniedError, authorize
from pms.authorization.domain.permissions import PermissionCode
from pms.authorization.infrastructure.django.grant_lookup import DjangoPermissionGrantLookup
from pms.bom.application.service import ImportBomCommand
from pms.master_data.application.drawings import UploadDrawingCommand
from pms.master_data.application.service import (
    CreateCustomerCommand,
    CreateMaterialCommand,
    CreateSupplierCommand,
)
from pms.platform.business_services import (
    attachment_service,
    bom_service,
    drawing_service,
    master_data_service,
    order_document_service,
    pricing_service,
    procurement_service,
    production_service,
    project_service,
    purchase_order_service,
)
from pms.procurement.application.pricing import CreateQuoteCommand
from pms.production.application.service import CreateProductionCommand
from pms.projects.application.service import CreateProjectCommand
from pms.tenancy.application.resolve_context import TenantContextUnavailableError
from pms.tenancy.domain.context import TenantContext
from pms.web import queries
from pms.web.audit import record_denied_access, record_expected_error, record_protected_read
from pms.web.authentication import authenticate_local_user, record_logout
from pms.web.context import SESSION_MEMBERSHIP_KEY, resolve_request_context
from pms.web.forms import (
    BomImportForm,
    CancelForm,
    CodeNameForm,
    CustomerForm,
    DrawingUploadForm,
    LoginForm,
    MaterialAssignmentForm,
    MaterialForm,
    ProductionForm,
    ProjectForm,
    SupplierForm,
    SupplierQuoteForm,
)

EXPECTED_USER_ERRORS = (ValueError, LookupError, PermissionError)


@require_http_methods(["GET", "POST"])
def login_view(request: HttpRequest) -> HttpResponse:
    """显示登录页并建立认证用户与可信 membership session。"""
    if request.user.is_authenticated:
        return redirect("web-dashboard")
    form = LoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        authenticated = authenticate_local_user(
            username=str(form.cleaned_data["username"]),
            password=str(form.cleaned_data["password"]),
        )
        if authenticated is None:
            form.add_error(None, "用户名、密码或租户成员关系无效。")
        else:
            user, context = authenticated
            django_login(request, user)
            request.session[SESSION_MEMBERSHIP_KEY] = str(context.membership_id)
            return redirect("web-dashboard")
    return render(request, "web/login.html", {"form": form})


@require_POST
@login_required
def logout_view(request: HttpRequest) -> HttpResponse:
    """追加审计后清除服务端认证和租户 session。"""
    with suppress(TenantContextUnavailableError):
        record_logout(resolve_request_context(request))
    django_logout(request)
    return redirect("web-login")


@require_GET
@login_required
def dashboard_view(request: HttpRequest) -> HttpResponse:
    context = _context(request)
    return render(request, "web/dashboard.html", {"dashboard": queries.dashboard(context)})


@require_GET
@login_required
def customer_list_view(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "web/customer_list.html",
        {"customers": queries.customers(_context(request))},
    )


@require_http_methods(["GET", "POST"])
@login_required
def customer_create_view(request: HttpRequest) -> HttpResponse:
    context = _context(request)
    form = CustomerForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            master_data_service().create_customer(
                context=context,
                command=CreateCustomerCommand(
                    code=str(form.cleaned_data["code"]),
                    name=str(form.cleaned_data["name"]),
                    short_name=str(form.cleaned_data["short_name"]),
                    tax_identifier=str(form.cleaned_data["tax_identifier"]),
                    address=str(form.cleaned_data["address"]),
                    phone=str(form.cleaned_data["phone"]),
                    bank_name=str(form.cleaned_data["bank_name"]),
                    bank_account=str(form.cleaned_data["bank_account"]),
                    bank_routing_number=str(form.cleaned_data["bank_routing_number"]),
                ),
            )
        except EXPECTED_USER_ERRORS as error:
            record_expected_error(
                context=context,
                action="customer.create",
                object_type="customer",
                object_id=None,
                error=error,
            )
            form.add_error(None, str(error))
        else:
            messages.success(request, "客户已创建。")
            return redirect("web-customer-list")
    return render(request, "web/simple_form.html", {"form": form, "title": "新建客户"})


@require_GET
@login_required
def supplier_list_view(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "web/supplier_list.html",
        {"suppliers": queries.suppliers(_context(request))},
    )


@require_http_methods(["GET", "POST"])
@login_required
def supplier_create_view(request: HttpRequest) -> HttpResponse:
    context = _context(request)
    form = SupplierForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            master_data_service().create_supplier(
                context=context,
                command=CreateSupplierCommand(
                    code=str(form.cleaned_data["code"]),
                    name=str(form.cleaned_data["name"]),
                    short_name=str(form.cleaned_data["short_name"]),
                    contact_person=str(form.cleaned_data["contact_person"]),
                    phone=str(form.cleaned_data["phone"]),
                    address=str(form.cleaned_data["address"]),
                    tax_identifier=str(form.cleaned_data["tax_identifier"]),
                    bank_routing_number=str(form.cleaned_data["bank_routing_number"]),
                    bank_name=str(form.cleaned_data["bank_name"]),
                    bank_account=str(form.cleaned_data["bank_account"]),
                    service_description=str(form.cleaned_data["service_description"]),
                    english_name=str(form.cleaned_data["english_name"]),
                    english_address=str(form.cleaned_data["english_address"]),
                ),
            )
        except EXPECTED_USER_ERRORS as error:
            record_expected_error(
                context=context,
                action="supplier.create",
                object_type="supplier",
                object_id=None,
                error=error,
            )
            form.add_error(None, str(error))
        else:
            messages.success(request, "供应商已创建。")
            return redirect("web-supplier-list")
    return render(request, "web/simple_form.html", {"form": form, "title": "新建供应商"})


@require_GET
@login_required
def material_list_view(request: HttpRequest) -> HttpResponse:
    context = _context(request)
    return render(
        request,
        "web/material_list.html",
        {
            "materials": queries.materials(context),
            "can_view_drawings": queries.can_view_drawings(context),
        },
    )


@require_GET
@login_required
def material_drawing_view(request: HttpRequest, material_id: UUID) -> HttpResponse:
    detail = queries.material_drawings(_context(request), material_id)
    if detail is None:
        raise Http404
    return render(
        request,
        "web/material_drawings.html",
        {"detail": detail, "form": DrawingUploadForm()},
    )


@require_POST
@login_required
def material_drawing_upload_view(request: HttpRequest, material_id: UUID) -> HttpResponse:
    form = DrawingUploadForm(request.POST, request.FILES)
    if form.is_valid():
        uploaded = cast(UploadedFile, form.cleaned_data["file"])
        try:
            if uploaded.size is None or uploaded.size > 25 * 1024 * 1024:
                raise ValueError("单个图纸不能超过 25 MiB。")
            if not uploaded.name:
                raise ValueError("图纸文件名不能为空。")
            drawing_service().upload(
                context=_context(request),
                command=UploadDrawingCommand(
                    material_id=material_id,
                    filename=uploaded.name,
                    content=uploaded.read(),
                    revision_label=str(form.cleaned_data["revision_label"]),
                    note=str(form.cleaned_data["note"]),
                ),
            )
        except EXPECTED_USER_ERRORS as error:
            messages.error(request, str(error))
        else:
            messages.success(request, "图纸新版本已保存，旧版本仍可追溯。")
    else:
        messages.error(request, "请选择 PDF 或 DWG 图纸。")
    return redirect("web-material-drawings", material_id=material_id)


@require_GET
@login_required
def material_drawing_download_view(request: HttpRequest, attachment_id: UUID) -> FileResponse:
    context = _context(request)
    attachment = queries.attachment_for_drawing(context, attachment_id)
    if attachment is None:
        raise Http404
    stream = attachment_service().open_available(
        context=context, attachment_id=AttachmentId(attachment_id)
    )
    record_protected_read(
        context=context,
        action="material_drawing.downloaded",
        object_type="attachment",
        object_id=attachment_id,
    )
    return FileResponse(stream, as_attachment=True, filename=attachment.original_filename)


def _simple_master_create(request: HttpRequest, *, kind: str, title: str) -> HttpResponse:
    context = _context(request)
    form = CodeNameForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            service = master_data_service()
            if kind == "unit":
                service.create_unit(
                    context=context,
                    code=str(form.cleaned_data["code"]),
                    name=str(form.cleaned_data["name"]),
                )
            else:
                service.create_category(
                    context=context,
                    code=str(form.cleaned_data["code"]),
                    name=str(form.cleaned_data["name"]),
                )
        except EXPECTED_USER_ERRORS as error:
            record_expected_error(
                context=context,
                action=f"{kind}.create",
                object_type=kind,
                object_id=None,
                error=error,
            )
            form.add_error(None, str(error))
        else:
            messages.success(request, f"{title}已创建。")
            return redirect("web-material-list")
    return render(request, "web/simple_form.html", {"form": form, "title": f"新建{title}"})


@require_http_methods(["GET", "POST"])
@login_required
def unit_create_view(request: HttpRequest) -> HttpResponse:
    return _simple_master_create(request, kind="unit", title="单位")


@require_http_methods(["GET", "POST"])
@login_required
def category_create_view(request: HttpRequest) -> HttpResponse:
    return _simple_master_create(request, kind="category", title="物料分类")


@require_http_methods(["GET", "POST"])
@login_required
def material_create_view(request: HttpRequest) -> HttpResponse:
    context = _context(request)
    _authorize_tenant(context, PermissionCode.MATERIAL_MANAGE)
    options = queries.master_options(context)
    form = MaterialForm(
        request.POST or None,
        units=options["units"],
        categories=options["categories"],
    )
    if request.method == "POST" and form.is_valid():
        try:
            master_data_service().create_material(
                context=context,
                command=CreateMaterialCommand(
                    code=str(form.cleaned_data["code"]),
                    name=str(form.cleaned_data["name"]),
                    specification=str(form.cleaned_data["specification"]),
                    brand=str(form.cleaned_data["brand"]),
                    part_attribute=str(form.cleaned_data["part_attribute"]),
                    unit_id=UUID(str(form.cleaned_data["unit_id"])),
                    category_id=UUID(str(form.cleaned_data["category_id"])),
                    procurement_required=bool(form.cleaned_data["procurement_required"]),
                ),
            )
        except EXPECTED_USER_ERRORS as error:
            record_expected_error(
                context=context,
                action="material.create",
                object_type="material",
                object_id=None,
                error=error,
            )
            form.add_error(None, str(error))
        else:
            messages.success(request, "物料已创建。")
            return redirect("web-material-list")
    return render(request, "web/simple_form.html", {"form": form, "title": "新建物料"})


@require_GET
@login_required
def project_list_view(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "web/project_list.html",
        {"projects": queries.projects(_context(request))},
    )


@require_http_methods(["GET", "POST"])
@login_required
def project_create_view(request: HttpRequest) -> HttpResponse:
    context = _context(request)
    _authorize_tenant(context, PermissionCode.PROJECT_CREATE)
    options = queries.master_options(context)
    form = ProjectForm(
        request.POST or None,
        customers=options["customers"],
        memberships=options["memberships"],
    )
    if request.method == "POST" and form.is_valid():
        try:
            project = project_service().create_project(
                context=context,
                command=CreateProjectCommand(
                    number=str(form.cleaned_data["number"]),
                    customer_id=UUID(str(form.cleaned_data["customer_id"])),
                    device_model=str(form.cleaned_data["device_model"]),
                    owner_membership_id=UUID(str(form.cleaned_data["owner_membership_id"])),
                    start_date=form.cleaned_data["start_date"],
                    planned_completion_date=form.cleaned_data["planned_completion_date"],
                ),
            )
        except EXPECTED_USER_ERRORS as error:
            record_expected_error(
                context=context,
                action="project.create",
                object_type="project",
                object_id=None,
                error=error,
            )
            form.add_error(None, str(error))
        else:
            messages.success(request, "项目草稿已创建。")
            return redirect("web-project-detail", project_id=project.id)
    return render(request, "web/simple_form.html", {"form": form, "title": "新建项目"})


@require_GET
@login_required
def project_detail_view(request: HttpRequest, project_id: UUID) -> HttpResponse:
    context = _context(request)
    detail = queries.project_detail(context, project_id)
    if detail is None:
        record_denied_access(
            context=context,
            action="project.view",
            object_type="project",
            object_id=project_id,
        )
        raise Http404
    return render(request, "web/project_detail.html", {"detail": detail})


@require_POST
@login_required
def project_action_view(request: HttpRequest, project_id: UUID, action: str) -> HttpResponse:
    context = _context(request)
    try:
        service = project_service()
        if action == "activate":
            service.activate_project(context=context, project_id=project_id)
        elif action == "close":
            service.close_project(context=context, project_id=project_id)
        elif action == "cancel":
            service.cancel_project(
                context=context,
                project_id=project_id,
                reason=request.POST.get("reason", ""),
            )
        else:
            raise Http404
    except EXPECTED_USER_ERRORS as error:
        record_expected_error(
            context=context,
            action=f"project.{action}",
            object_type="project",
            object_id=project_id,
            error=error,
        )
        messages.error(request, str(error))
    else:
        messages.success(request, "项目状态已更新。")
    return redirect("web-project-detail", project_id=project_id)


@require_http_methods(["GET", "POST"])
@login_required
def bom_import_view(request: HttpRequest, project_id: UUID) -> HttpResponse:
    context = _context(request)
    form = BomImportForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        uploaded = form.cleaned_data["source_file"]
        try:
            content = uploaded.read()
            if not isinstance(content, bytes):
                raise ValueError("无法读取上传文件。")
            bom = bom_service().import_bom(
                context=context,
                command=ImportBomCommand(
                    project_id=project_id,
                    version_number=int(form.cleaned_data["version_number"]),
                    filename=uploaded.name,
                    content=content,
                    mapping=form.mapping(),
                ),
            )
        except EXPECTED_USER_ERRORS as error:
            record_expected_error(
                context=context,
                action="bom.import",
                object_type="project",
                object_id=project_id,
                error=error,
            )
            form.add_error(None, str(error))
        else:
            messages.success(request, f"BOM V{bom.version_number} 已导入为草稿。")
            return redirect("web-bom-detail", bom_id=bom.id)
    return render(
        request,
        "web/bom_import.html",
        {"form": form, "project_id": project_id},
    )


@require_GET
@login_required
def bom_detail_view(request: HttpRequest, bom_id: UUID) -> HttpResponse:
    context = _context(request)
    detail = queries.bom_detail(context, bom_id)
    if detail is None:
        record_denied_access(
            context=context,
            action="bom.view",
            object_type="bom_version",
            object_id=bom_id,
        )
        raise Http404
    assignment_form = MaterialAssignmentForm(materials=queries.visible_material_options(context))
    return render(
        request,
        "web/bom_detail.html",
        {"detail": detail, "assignment_form": assignment_form, "cancel_form": CancelForm()},
    )


@require_POST
@login_required
def bom_publish_view(request: HttpRequest, bom_id: UUID) -> HttpResponse:
    context = _context(request)
    try:
        bom_service().publish_bom(context=context, bom_id=bom_id)
    except EXPECTED_USER_ERRORS as error:
        record_expected_error(
            context=context,
            action="bom.publish",
            object_type="bom_version",
            object_id=bom_id,
            error=error,
        )
        messages.error(request, str(error))
    else:
        messages.success(request, "BOM 已发布，历史版本不会被覆盖。")
    return redirect("web-bom-detail", bom_id=bom_id)


@require_POST
@login_required
def bom_cancel_view(request: HttpRequest, bom_id: UUID) -> HttpResponse:
    context = _context(request)
    form = CancelForm(request.POST)
    if form.is_valid():
        try:
            bom_service().cancel_bom(
                context=context,
                bom_id=bom_id,
                reason=str(form.cleaned_data["reason"]),
            )
        except EXPECTED_USER_ERRORS as error:
            record_expected_error(
                context=context,
                action="bom.cancel",
                object_type="bom_version",
                object_id=bom_id,
                error=error,
            )
            messages.error(request, str(error))
        else:
            messages.success(request, "BOM 已取消，版本内容和来源附件仍保留。")
    else:
        messages.error(request, "请填写取消原因。")
    return redirect("web-bom-detail", bom_id=bom_id)


@require_POST
@login_required
def bom_assign_material_view(request: HttpRequest, bom_id: UUID, line_id: UUID) -> HttpResponse:
    context = _context(request)
    form = MaterialAssignmentForm(request.POST, materials=queries.visible_material_options(context))
    if form.is_valid():
        try:
            bom_service().assign_line_material(
                context=context,
                bom_id=bom_id,
                line_id=line_id,
                material_id=UUID(str(form.cleaned_data["material_id"])),
            )
        except EXPECTED_USER_ERRORS as error:
            record_expected_error(
                context=context,
                action="bom.assign_material",
                object_type="bom_version",
                object_id=bom_id,
                error=error,
            )
            messages.error(request, str(error))
        else:
            messages.success(request, "BOM 行物料已确认。")
    else:
        messages.error(request, "请选择有效物料。")
    return redirect("web-bom-detail", bom_id=bom_id)


@require_POST
@login_required
def bom_confirm_duplicate_view(request: HttpRequest, bom_id: UUID, line_id: UUID) -> HttpResponse:
    context = _context(request)
    try:
        bom_service().confirm_duplicate(context=context, bom_id=bom_id, line_id=line_id)
    except EXPECTED_USER_ERRORS as error:
        record_expected_error(
            context=context,
            action="bom.confirm_duplicate",
            object_type="bom_version",
            object_id=bom_id,
            error=error,
        )
        messages.error(request, str(error))
    else:
        messages.success(request, "已确认保留该疑似重复行；数量没有自动合并。")
    return redirect("web-bom-detail", bom_id=bom_id)


@require_http_methods(["GET", "POST"])
@login_required
def production_create_view(request: HttpRequest, project_id: UUID, bom_id: UUID) -> HttpResponse:
    context = _context(request)
    form = ProductionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            production = production_service().create_draft(
                context=context,
                command=CreateProductionCommand(
                    project_id=project_id,
                    bom_id=bom_id,
                    production_units=int(form.cleaned_data["production_units"]),
                    production_unit=str(form.cleaned_data["production_unit"]),
                    receiving_department=str(form.cleaned_data["receiving_department"]),
                ),
            )
        except EXPECTED_USER_ERRORS as error:
            record_expected_error(
                context=context,
                action="production_release.create",
                object_type="bom_version",
                object_id=bom_id,
                error=error,
            )
            form.add_error(None, str(error))
        else:
            messages.success(request, "投产草稿已创建。")
            return redirect("web-production-detail", production_id=production.id)
    return render(request, "web/simple_form.html", {"form": form, "title": "创建投产批次"})


@require_GET
@login_required
def production_detail_view(request: HttpRequest, production_id: UUID) -> HttpResponse:
    context = _context(request)
    detail = queries.production_detail(context, production_id)
    if detail is None:
        record_denied_access(
            context=context,
            action="production_release.view",
            object_type="production_release",
            object_id=production_id,
        )
        raise Http404
    return render(
        request,
        "web/production_detail.html",
        {
            "detail": detail,
            "idempotency_key": uuid.uuid4().hex,
            "cancel_form": CancelForm(),
        },
    )


@require_POST
@login_required
def production_release_view(request: HttpRequest, production_id: UUID) -> HttpResponse:
    context = _context(request)
    try:
        production_service().release(context=context, production_id=production_id)
    except EXPECTED_USER_ERRORS as error:
        record_expected_error(
            context=context,
            action="production_release.release",
            object_type="production_release",
            object_id=production_id,
            error=error,
        )
        messages.error(request, str(error))
    else:
        messages.success(request, "投产已发布，需求数量已固化。")
    return redirect("web-production-detail", production_id=production_id)


@require_POST
@login_required
def production_cancel_view(request: HttpRequest, production_id: UUID) -> HttpResponse:
    context = _context(request)
    form = CancelForm(request.POST)
    if form.is_valid():
        try:
            production_service().cancel(
                context=context,
                production_id=production_id,
                reason=str(form.cleaned_data["reason"]),
            )
        except EXPECTED_USER_ERRORS as error:
            record_expected_error(
                context=context,
                action="production_release.cancel",
                object_type="production_release",
                object_id=production_id,
                error=error,
            )
            messages.error(request, str(error))
        else:
            messages.success(request, "投产批次已取消，历史需求仍保留。")
    else:
        messages.error(request, "请填写取消原因。")
    return redirect("web-production-detail", production_id=production_id)


@require_POST
@login_required
def request_create_view(request: HttpRequest, production_id: UUID) -> HttpResponse:
    context = _context(request)
    key = request.POST.get("idempotency_key", "")
    try:
        purchase_request = procurement_service().create_draft(
            context=context,
            production_id=production_id,
            idempotency_key=key,
        )
    except EXPECTED_USER_ERRORS as error:
        record_expected_error(
            context=context,
            action="purchase_request.create",
            object_type="production_release",
            object_id=production_id,
            error=error,
        )
        messages.error(request, str(error))
        return redirect("web-production-detail", production_id=production_id)
    messages.success(request, "生产请购草稿已生成。")
    return redirect("web-request-detail", request_id=purchase_request.id)


@require_GET
@login_required
def request_detail_view(request: HttpRequest, request_id: UUID) -> HttpResponse:
    context = _context(request)
    detail = queries.request_detail(context, request_id)
    if detail is None:
        record_denied_access(
            context=context,
            action="purchase_request.view",
            object_type="purchase_request",
            object_id=request_id,
        )
        raise Http404
    return render(
        request,
        "web/request_detail.html",
        {
            "detail": detail,
            "cancel_form": CancelForm(),
            "quote_form": SupplierQuoteForm(suppliers=detail.supplier_options),
        },
    )


@require_POST
@login_required
def request_submit_view(request: HttpRequest, request_id: UUID) -> HttpResponse:
    context = _context(request)
    try:
        procurement_service().submit(context=context, request_id=request_id)
    except EXPECTED_USER_ERRORS as error:
        record_expected_error(
            context=context,
            action="purchase_request.submit",
            object_type="purchase_request",
            object_id=request_id,
            error=error,
        )
        messages.error(request, str(error))
    else:
        messages.success(request, "生产请购已提交并取得正式编号。")
    return redirect("web-request-detail", request_id=request_id)


@require_POST
@login_required
def request_cancel_view(request: HttpRequest, request_id: UUID) -> HttpResponse:
    context = _context(request)
    form = CancelForm(request.POST)
    if form.is_valid():
        try:
            procurement_service().cancel(
                context=context,
                request_id=request_id,
                reason=str(form.cleaned_data["reason"]),
            )
        except EXPECTED_USER_ERRORS as error:
            record_expected_error(
                context=context,
                action="purchase_request.cancel",
                object_type="purchase_request",
                object_id=request_id,
                error=error,
            )
            messages.error(request, str(error))
        else:
            messages.success(request, "生产请购已取消，来源数量恢复可请购。")
    else:
        messages.error(request, "请填写取消原因。")
    return redirect("web-request-detail", request_id=request_id)


@require_POST
@login_required
def quote_create_view(request: HttpRequest, request_id: UUID, line_id: UUID) -> HttpResponse:
    """录入不可变供应商报价，表单错误不写入任何部分状态。"""
    context = _context(request)
    detail = queries.request_detail(context, request_id)
    if detail is None or line_id not in {line.id for line in detail.lines}:
        raise Http404
    form = SupplierQuoteForm(request.POST, suppliers=detail.supplier_options)
    if form.is_valid():
        try:
            pricing_service().create_quote(
                context=context,
                command=CreateQuoteCommand(
                    request_line_id=line_id,
                    supplier_id=UUID(str(form.cleaned_data["supplier_id"])),
                    quote_date=cast(date, form.cleaned_data["quote_date"]),
                    valid_until=cast(date | None, form.cleaned_data["valid_until"]),
                    currency=str(form.cleaned_data["currency"]),
                    unit_price=cast(Decimal, form.cleaned_data["unit_price"]),
                    tax_rate=cast(Decimal, form.cleaned_data["tax_rate"]),
                    tax_included=bool(form.cleaned_data["tax_included"]),
                    minimum_order_quantity=cast(
                        Decimal | None, form.cleaned_data["minimum_order_quantity"]
                    ),
                    lead_time_days=cast(int | None, form.cleaned_data["lead_time_days"]),
                    source_type=str(form.cleaned_data["source_type"]),
                    source_reference=str(form.cleaned_data["source_reference"]),
                    remark=str(form.cleaned_data["remark"]),
                ),
            )
        except EXPECTED_USER_ERRORS as error:
            messages.error(request, str(error))
        else:
            messages.success(request, "供应商报价已保存。")
    else:
        messages.error(request, "报价字段不完整或格式不正确。")
    return redirect("web-request-detail", request_id=request_id)


@require_POST
@login_required
def quote_withdraw_view(request: HttpRequest, request_id: UUID, quote_id: UUID) -> HttpResponse:
    context = _context(request)
    try:
        pricing_service().withdraw_quote(context=context, quote_id=quote_id)
    except EXPECTED_USER_ERRORS as error:
        messages.error(request, str(error))
    else:
        messages.success(request, "报价已撤销并保留历史。")
    return redirect("web-request-detail", request_id=request_id)


@require_POST
@login_required
def quote_select_view(request: HttpRequest, request_id: UUID, quote_id: UUID) -> HttpResponse:
    context = _context(request)
    try:
        decision = pricing_service().select_quote(context=context, quote_id=quote_id)
    except EXPECTED_USER_ERRORS as error:
        messages.error(request, str(error))
    else:
        messages.success(request, f"供应商已确定，当前为第 {decision.version} 版决策。")
    return redirect("web-request-detail", request_id=request_id)


@require_GET
@login_required
def purchase_order_list_view(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "web/purchase_order_list.html",
        {"orders": queries.purchase_orders(_context(request))},
    )


@require_GET
@login_required
def purchase_order_detail_view(request: HttpRequest, order_id: UUID) -> HttpResponse:
    context = _context(request)
    detail = queries.purchase_order_detail(context, order_id)
    if detail is None:
        raise Http404
    return render(
        request, "web/purchase_order_detail.html", {"detail": detail, "cancel_form": CancelForm()}
    )


@require_POST
@login_required
def purchase_order_create_view(request: HttpRequest, request_id: UUID) -> HttpResponse:
    context = _context(request)
    try:
        orders = purchase_order_service().create_from_request(
            context=context, request_id=request_id
        )
    except EXPECTED_USER_ERRORS as error:
        messages.error(request, str(error))
        return redirect("web-request-detail", request_id=request_id)
    messages.success(request, f"已生成 {len(orders)} 张订单草稿。")
    return redirect("web-purchase-order-detail", order_id=orders[0].id)


@require_POST
@login_required
def purchase_order_issue_view(request: HttpRequest, order_id: UUID) -> HttpResponse:
    try:
        issued = purchase_order_service().issue(
            context=_context(request), order_id=order_id, business_date=timezone.localdate()
        )
    except EXPECTED_USER_ERRORS as error:
        messages.error(request, str(error))
    else:
        messages.success(request, f"订单已签发：{issued.order_number}")
    return redirect("web-purchase-order-detail", order_id=order_id)


@require_POST
@login_required
def purchase_order_cancel_view(request: HttpRequest, order_id: UUID) -> HttpResponse:
    form = CancelForm(request.POST)
    if form.is_valid():
        try:
            purchase_order_service().cancel(
                context=_context(request),
                order_id=order_id,
                reason=str(form.cleaned_data["reason"]),
            )
        except EXPECTED_USER_ERRORS as error:
            messages.error(request, str(error))
        else:
            messages.success(request, "订单已取消，来源请购明细已恢复可下单。")
    else:
        messages.error(request, "请填写取消原因。")
    return redirect("web-purchase-order-detail", order_id=order_id)


@require_POST
@login_required
def purchase_order_document_generate_view(request: HttpRequest, order_id: UUID) -> HttpResponse:
    try:
        document = order_document_service().generate(context=_context(request), order_id=order_id)
    except EXPECTED_USER_ERRORS as error:
        messages.error(request, str(error))
    else:
        messages.success(request, f"订单 Excel V{document.version} 已生成。")
    return redirect("web-purchase-order-detail", order_id=order_id)


@require_GET
@login_required
def purchase_order_document_download_view(
    request: HttpRequest, attachment_id: UUID
) -> FileResponse:
    context = _context(request)
    attachment = queries.attachment_for_order(context, attachment_id)
    if attachment is None:
        raise Http404
    stream = attachment_service().open_available(
        context=context, attachment_id=AttachmentId(attachment_id)
    )
    record_protected_read(
        context=context,
        action="purchase_order.document_downloaded",
        object_type="attachment",
        object_id=attachment_id,
    )
    return FileResponse(stream, as_attachment=True, filename=attachment.original_filename)


@require_GET
@login_required
def attachment_download_view(request: HttpRequest, attachment_id: UUID) -> FileResponse:
    context = _context(request)
    attachment = queries.attachment_for_bom(context, attachment_id)
    if attachment is None:
        record_denied_access(
            context=context,
            action="attachment.download",
            object_type="attachment",
            object_id=attachment_id,
        )
        raise Http404
    try:
        stream = attachment_service().open_available(
            context=context,
            attachment_id=AttachmentId(attachment_id),
        )
    except EXPECTED_USER_ERRORS as error:
        record_expected_error(
            context=context,
            action="attachment.download",
            object_type="attachment",
            object_id=attachment_id,
            error=error,
        )
        raise Http404 from error
    record_protected_read(
        context=context,
        action="attachment.download",
        object_type="attachment",
        object_id=attachment_id,
    )
    return FileResponse(stream, as_attachment=True, filename=attachment.original_filename)


def _context(request: HttpRequest) -> TenantContext:
    try:
        return resolve_request_context(request)
    except TenantContextUnavailableError as error:
        django_logout(request)
        raise PermissionDenied("当前租户会话已失效，请重新登录。") from error


def _authorize_tenant(context: TenantContext, permission: PermissionCode) -> None:
    try:
        authorize(
            context=context,
            resource_tenant_id=context.tenant_id,
            permission=permission,
            is_related=True,
            lookup=DjangoPermissionGrantLookup(),
        )
    except PermissionDeniedError as error:
        record_expected_error(
            context=context,
            action="authorization.denied",
            object_type="permission",
            object_id=permission.value,
            error=error,
        )
        raise PermissionDenied("当前成员无权打开该操作页面。") from error
