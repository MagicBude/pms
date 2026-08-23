"""P2-01 主数据与项目用例的租户、权限、事务和审计测试。"""

from datetime import date

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command

from pms.audit.infrastructure.django.models import AuditLog
from pms.audit.infrastructure.django.recorder import DjangoAuditRecorder
from pms.authorization.application.authorize import PermissionDeniedError
from pms.authorization.domain.permissions import RoleCode
from pms.authorization.infrastructure.django.grant_lookup import DjangoPermissionGrantLookup
from pms.authorization.infrastructure.django.models import MembershipRole, Role
from pms.bom.infrastructure.django.repository import DjangoBomProjectDownstreamLookup
from pms.master_data.application.service import CreateMaterialCommand, MasterDataService
from pms.master_data.domain.values import DuplicateMasterDataError
from pms.master_data.infrastructure.django.models import Customer, Material
from pms.master_data.infrastructure.django.repository import (
    DjangoMasterDataRepository,
    DjangoTransactionManager,
)
from pms.projects.application.service import (
    CreateProjectCommand,
    DuplicateProjectNumberError,
    ProjectNotFoundError,
    ProjectService,
)
from pms.projects.domain.lifecycle import ProjectStatus
from pms.projects.infrastructure.django.models import Project
from pms.projects.infrastructure.django.repository import (
    DjangoProjectRepository,
    DjangoProjectTransactionManager,
)
from pms.tenancy.domain.context import MembershipId, TenantContext, TenantId, UserId
from pms.tenancy.infrastructure.django.models import Membership, Tenant

TEST_PASSWORD = "P2-01-only-Strong!5927"


def initialize_admin_context(monkeypatch: pytest.MonkeyPatch) -> TenantContext:
    """建立真实默认授权目录，避免测试绕过产品初始化规则。"""
    monkeypatch.setenv("PMS_INITIAL_ADMIN_PASSWORD", TEST_PASSWORD)
    call_command("initialize_pms", no_color=True, verbosity=0)
    monkeypatch.delenv("PMS_INITIAL_ADMIN_PASSWORD")
    membership = Membership.objects.select_related("tenant", "user").get()
    return TenantContext(
        tenant_id=TenantId(membership.tenant_id),
        user_id=UserId(membership.user_id),
        membership_id=MembershipId(membership.id),
    )


def master_data_service() -> MasterDataService:
    return MasterDataService(
        repository=DjangoMasterDataRepository(),
        grants=DjangoPermissionGrantLookup(),
        audit=DjangoAuditRecorder(),
        transactions=DjangoTransactionManager(),
    )


def project_service() -> ProjectService:
    return ProjectService(
        repository=DjangoProjectRepository(),
        grants=DjangoPermissionGrantLookup(),
        audit=DjangoAuditRecorder(),
        transactions=DjangoProjectTransactionManager(),
        downstream=DjangoBomProjectDownstreamLookup(),
    )


def create_second_context(*, role: RoleCode, suffix: str) -> TenantContext:
    user = get_user_model().objects.create_user(username=f"user-{suffix}")
    tenant = Tenant.objects.create(code=f"tenant-{suffix}", name=f"Tenant {suffix}")
    membership = Membership.objects.create(tenant=tenant, user=user)
    MembershipRole.objects.create(membership=membership, role=Role.objects.get(code=role))
    return TenantContext(
        tenant_id=TenantId(tenant.id),
        user_id=UserId(user.id),
        membership_id=MembershipId(membership.id),
    )


@pytest.mark.django_db
@pytest.mark.acceptance
def test_admin_creates_tenant_scoped_master_data_with_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-S001-044/045：客户与完整物料可创建、可持久化并进入审计。"""
    context = initialize_admin_context(monkeypatch)
    service = master_data_service()

    customer = service.create_customer(context=context, code="cus-001", name="示例客户")
    unit = service.create_unit(context=context, code="pcs", name="件")
    category = service.create_category(context=context, code="standard", name="标准件")
    material = service.create_material(
        context=context,
        command=CreateMaterialCommand(
            code="mat-001",
            name="示例电机",
            specification="  220V   1kW ",
            brand="Demo",
            unit_id=unit.id,
            category_id=category.id,
            procurement_required=True,
        ),
    )

    assert customer.code == "CUS-001"
    saved_material = Material.objects.get(id=material.id, tenant_id=context.tenant_id)
    assert saved_material.specification == "220V 1kW"
    assert saved_material.procurement_required
    assert (
        AuditLog.objects.filter(
            tenant_id=context.tenant_id,
            action__in=(
                "customer.created",
                "unit.created",
                "material_category.created",
                "material.created",
            ),
        ).count()
        == 4
    )


@pytest.mark.django_db
@pytest.mark.acceptance
def test_master_data_uniqueness_and_foreign_keys_are_tenant_scoped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-S001-039/045：代码可跨租户复用，但跨租户单位和分类不能猜测引用。"""
    context_a = initialize_admin_context(monkeypatch)
    context_b = create_second_context(role=RoleCode.TENANT_ADMIN, suffix="b")
    service = master_data_service()
    service.create_customer(context=context_a, code="CUS-001", name="客户 A")
    service.create_customer(context=context_b, code="CUS-001", name="客户 B")

    with pytest.raises(DuplicateMasterDataError):
        service.create_customer(context=context_a, code="cus-001", name="另一个客户")

    unit_a = service.create_unit(context=context_a, code="PCS", name="件")
    category_b = service.create_category(context=context_b, code="STD", name="标准件")
    with pytest.raises(ValueError, match="单位或分类不可用"):
        service.create_material(
            context=context_a,
            command=CreateMaterialCommand(
                code="MAT-X",
                name="跨租户攻击",
                unit_id=unit_a.id,
                category_id=category_b.id,
            ),
        )
    assert not Material.objects.filter(code="MAT-X").exists()


@pytest.mark.django_db
@pytest.mark.acceptance
def test_project_lifecycle_uses_named_actions_and_tenant_unique_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-S001-005—008：项目由草稿经命名动作启用、关闭，编号仅租户内唯一。"""
    context = initialize_admin_context(monkeypatch)
    customer = master_data_service().create_customer(
        context=context, code="CUS-001", name="示例客户"
    )
    service = project_service()
    command = CreateProjectCommand(
        number="demo-001",
        customer_id=customer.id,
        device_model="教学设备 A",
        owner_membership_id=context.membership_id,
        start_date=date(2026, 8, 24),
        planned_completion_date=date(2026, 9, 30),
    )

    project = service.create_project(context=context, command=command)
    assert project.status is ProjectStatus.DRAFT
    with pytest.raises(DuplicateProjectNumberError):
        service.create_project(context=context, command=command)

    active = service.activate_project(context=context, project_id=project.id)
    closed = service.close_project(context=context, project_id=project.id)
    assert active.status is ProjectStatus.ACTIVE
    assert closed.status is ProjectStatus.CLOSED
    assert list(
        AuditLog.objects.filter(object_id=str(project.id)).values_list("action", flat=True)
    ) == ["project.closed", "project.activated", "project.created"]


@pytest.mark.django_db
@pytest.mark.acceptance
def test_project_permissions_and_direct_ids_do_not_cross_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-S001-037—039：无权限和跨租户直接 ID 都在服务端拒绝。"""
    admin_context = initialize_admin_context(monkeypatch)
    customer = master_data_service().create_customer(
        context=admin_context, code="CUS-001", name="示例客户"
    )
    project = project_service().create_project(
        context=admin_context,
        command=CreateProjectCommand(
            number="DEMO-001",
            customer_id=customer.id,
            device_model="教学设备 A",
            owner_membership_id=admin_context.membership_id,
        ),
    )
    auditor_context = create_second_context(role=RoleCode.AUDITOR, suffix="auditor")

    with pytest.raises(PermissionDeniedError):
        master_data_service().create_customer(context=auditor_context, code="NOPE", name="无权客户")
    with pytest.raises(ProjectNotFoundError):
        project_service().activate_project(context=auditor_context, project_id=project.id)
    assert Customer.objects.filter(code="NOPE").count() == 0
    assert Project.objects.get(id=project.id).status == ProjectStatus.DRAFT
