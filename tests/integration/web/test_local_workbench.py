"""P2-04 本机浏览器工作台的登录、安全边界与完整业务链测试。"""

from pathlib import Path

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client

from pms.attachments.infrastructure.django.models import Attachment
from pms.audit.infrastructure.django.models import AuditLog
from pms.authorization.domain.permissions import RoleCode
from pms.authorization.infrastructure.django.models import MembershipRole, Role
from pms.bom.infrastructure.django.models import BomVersion
from pms.master_data.infrastructure.django.models import Customer, Material, MaterialCategory, Unit
from pms.procurement.domain.request import PurchaseRequestStatus
from pms.procurement.infrastructure.django.models import PurchaseRequest
from pms.production.domain.release import ProductionStatus
from pms.production.infrastructure.django.models import ProductionRelease
from pms.projects.domain.lifecycle import ProjectStatus
from pms.projects.infrastructure.django.models import Project
from pms.tenancy.infrastructure.django.models import Membership
from pms.web.context import SESSION_MEMBERSHIP_KEY
from tests.integration.business.test_bom_workflow import make_workbook

PASSWORD = "P2-04-only-Strong!5927"


def initialize_local_installation(monkeypatch: pytest.MonkeyPatch) -> Membership:
    """建立与真实本机初始化命令一致的默认管理员。"""
    monkeypatch.setenv("PMS_INITIAL_ADMIN_PASSWORD", PASSWORD)
    call_command("initialize_pms", no_color=True, verbosity=0)
    monkeypatch.delenv("PMS_INITIAL_ADMIN_PASSWORD")
    return Membership.objects.select_related("user", "tenant").get()


def login(client: Client) -> None:
    response = client.post("/login/", {"username": "admin", "password": PASSWORD})
    assert response.status_code == 302
    assert response.headers["Location"] == "/"


@pytest.mark.django_db
@pytest.mark.acceptance
def test_login_csrf_audit_and_deactivated_membership_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-S001-001—004/044：认证、CSRF、即时停用和审计均在服务端生效。"""
    membership = initialize_local_installation(monkeypatch)
    anonymous = Client()
    assert anonymous.get("/").status_code == 302
    assert anonymous.get("/").headers["Location"] == "/login/?next=/"

    csrf_client = Client(enforce_csrf_checks=True)
    assert (
        csrf_client.post("/login/", {"username": "admin", "password": PASSWORD}).status_code == 403
    )
    login_page = csrf_client.get("/login/")
    csrf_token = login_page.cookies["csrftoken"].value
    failed = csrf_client.post(
        "/login/",
        {
            "username": "admin",
            "password": "incorrect-password",
            "csrfmiddlewaretoken": csrf_token,
        },
    )
    assert failed.status_code == 200
    assert "用户名、密码或租户成员关系无效" in failed.content.decode()
    assert AuditLog.objects.filter(action="identity.login_failed").count() == 1

    csrf_token = csrf_client.get("/login/").cookies["csrftoken"].value
    signed_in = csrf_client.post(
        "/login/",
        {
            "username": "admin",
            "password": PASSWORD,
            "csrfmiddlewaretoken": csrf_token,
        },
    )
    assert signed_in.status_code == 302
    assert AuditLog.objects.filter(action="identity.login_succeeded").count() == 1
    assert (
        csrf_client.post("/customers/new/", {"code": "NO-CSRF", "name": "拒绝"}).status_code == 403
    )

    membership.is_active = False
    membership.save(update_fields=("is_active",))
    assert csrf_client.get("/").status_code == 403


@pytest.mark.django_db
@pytest.mark.acceptance
def test_browser_workflow_reaches_submitted_purchase_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC-S001-005—035：从主数据到已提交生产请购可完全通过浏览器完成。"""
    membership = initialize_local_installation(monkeypatch)
    monkeypatch.setattr(settings, "ATTACHMENT_STORAGE_ROOT", tmp_path, raising=False)
    client = Client()
    login(client)

    assert (
        client.post("/customers/new/", {"code": "CUS-UI", "name": "界面示例客户"}).status_code
        == 302
    )
    assert client.post("/units/new/", {"code": "PCS", "name": "件"}).status_code == 302
    assert client.post("/categories/new/", {"code": "STD", "name": "标准件"}).status_code == 302
    customer = Customer.objects.get(code="CUS-UI")
    unit = Unit.objects.get(code="PCS")
    category = MaterialCategory.objects.get(code="STD")

    material_response = client.post(
        "/materials/new/",
        {
            "code": "MAT-UI-001",
            "name": "界面示例电机",
            "specification": "220V",
            "brand": "教学品牌",
            "unit_id": str(unit.id),
            "category_id": str(category.id),
            "procurement_required": "on",
        },
    )
    assert material_response.status_code == 302
    assert Material.objects.get(code="MAT-UI-001").procurement_required is True

    project_response = client.post(
        "/projects/new/",
        {
            "number": "UI-2026-001",
            "customer_id": str(customer.id),
            "device_model": "教学设备 UI",
            "owner_membership_id": str(membership.id),
            "start_date": "2026-08-24",
            "planned_completion_date": "2026-09-30",
            "status": "CLOSED",
        },
    )
    project = Project.objects.get(number="UI-2026-001")
    assert project_response.headers["Location"] == f"/projects/{project.id}/"
    assert project.status == ProjectStatus.DRAFT
    client.post(f"/projects/{project.id}/activate/")
    project.refresh_from_db()
    assert project.status == ProjectStatus.ACTIVE

    source = SimpleUploadedFile(
        "ui-bom.xlsx",
        make_workbook([["MAT-UI-001", "界面示例电机", "220V", "2", "PCS", ""]]),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    import_response = client.post(
        f"/projects/{project.id}/bom/import/",
        {
            "version_number": "1",
            "source_file": source,
            "header_material_code": "物料编码",
            "header_material_name": "物料名称",
            "header_specification": "规格型号",
            "header_brand": "",
            "header_quantity": "单台数量",
            "header_unit": "单位",
            "header_level": "",
            "header_remark": "备注",
        },
    )
    bom = BomVersion.objects.get(project=project)
    assert import_response.headers["Location"] == f"/boms/{bom.id}/"
    assert client.get(f"/attachments/{bom.source_attachment_id}/download/").status_code == 200
    client.post(f"/boms/{bom.id}/publish/")
    bom.refresh_from_db()
    assert bom.status == "PUBLISHED"

    production_response = client.post(
        f"/projects/{project.id}/boms/{bom.id}/production/new/",
        {
            "production_units": "3",
            "production_unit": "台",
            "receiving_department": "装配部",
        },
    )
    production = ProductionRelease.objects.get(project=project)
    assert production_response.headers["Location"] == f"/production/{production.id}/"
    client.post(f"/production/{production.id}/release/")
    production.refresh_from_db()
    assert production.status == ProductionStatus.RELEASED

    request_response = client.post(
        f"/production/{production.id}/requests/new/",
        {"idempotency_key": "ui-double-click-safe-1"},
    )
    purchase_request = PurchaseRequest.objects.get(production_release=production)
    assert request_response.headers["Location"] == f"/requests/{purchase_request.id}/"
    client.post(f"/requests/{purchase_request.id}/submit/")
    purchase_request.refresh_from_db()
    assert purchase_request.status == PurchaseRequestStatus.SUBMITTED
    assert purchase_request.request_number is not None
    final_page = client.get(f"/requests/{purchase_request.id}/")
    assert purchase_request.request_number in final_page.content.decode()
    assert AuditLog.objects.filter(action="purchase_request.submitted").count() == 1
    assert Attachment.objects.filter(id=bom.source_attachment_id, status="available").exists()


@pytest.mark.django_db
@pytest.mark.acceptance
def test_related_scope_cannot_guess_another_members_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-S001-036/038/045：RELATED 列表与详情都隐藏不相关对象。"""
    admin_membership = initialize_local_installation(monkeypatch)
    customer = Customer.objects.create(
        tenant=admin_membership.tenant, code="CUS-SCOPE", name="范围测试客户"
    )
    project = Project.objects.create(
        tenant=admin_membership.tenant,
        number="SCOPE-001",
        customer=customer,
        device_model="不可猜测设备",
        owner_membership=admin_membership,
        created_by_membership=admin_membership,
    )
    user = get_user_model().objects.create_user(
        username="related-manager", password="related-manager-Strong!2026"
    )
    related = Membership.objects.create(tenant=admin_membership.tenant, user=user)
    MembershipRole.objects.create(
        membership=related, role=Role.objects.get(code=RoleCode.PROJECT_MANAGER)
    )
    client = Client()
    client.force_login(user)
    session = client.session
    session[SESSION_MEMBERSHIP_KEY] = str(related.id)
    session.save()

    listing = client.get("/projects/")
    assert listing.status_code == 200
    assert "SCOPE-001" not in listing.content.decode()
    assert client.get(f"/projects/{project.id}/").status_code == 404
