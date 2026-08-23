"""P2-02 BOM 导入、修正、发布、权限和版本集成测试。"""

from io import BytesIO
from pathlib import Path
from uuid import UUID

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from openpyxl import Workbook

from pms.attachments.application.service import AttachmentService
from pms.attachments.infrastructure.django.models import Attachment
from pms.attachments.infrastructure.django.repository import DjangoAttachmentRepository
from pms.attachments.infrastructure.local_storage import LocalBinaryStorage
from pms.audit.application.recorder import AuditRecorder
from pms.audit.domain.events import AuditEvent
from pms.audit.infrastructure.django.models import AuditLog
from pms.audit.infrastructure.django.recorder import DjangoAuditRecorder
from pms.authorization.application.authorize import PermissionDeniedError
from pms.authorization.domain.permissions import RoleCode
from pms.authorization.infrastructure.django.grant_lookup import DjangoPermissionGrantLookup
from pms.authorization.infrastructure.django.models import MembershipRole, Role
from pms.bom.application.service import BomImportError, BomService, ImportBomCommand
from pms.bom.domain.lifecycle import BomStatus, InvalidBomTransitionError
from pms.bom.domain.validation import BomLineErrorCode
from pms.bom.infrastructure.django.models import BomLine, BomVersion
from pms.bom.infrastructure.django.repository import (
    DjangoBomProjectDownstreamLookup,
    DjangoBomRepository,
    DjangoBomTransactionManager,
)
from pms.bom.infrastructure.spreadsheet import OpenPyxlBomSpreadsheetParser
from pms.master_data.application.service import (
    CreatedMasterData,
    CreateMaterialCommand,
    MasterDataService,
)
from pms.master_data.infrastructure.django.repository import (
    DjangoMasterDataRepository,
    DjangoTransactionManager,
)
from pms.production.infrastructure.django.repository import DjangoBomProductionDownstreamLookup
from pms.projects.application.service import (
    CreateProjectCommand,
    ProjectService,
    ProjectSnapshot,
)
from pms.projects.domain.lifecycle import InvalidProjectTransitionError
from pms.projects.infrastructure.django.repository import (
    DjangoProjectRepository,
    DjangoProjectTransactionManager,
)
from pms.tenancy.domain.context import MembershipId, TenantContext, TenantId, UserId
from pms.tenancy.infrastructure.django.models import Membership, Tenant

PASSWORD = "P2-02-only-Strong!5927"
MAPPING = {
    "material_code": "物料编码",
    "material_name": "物料名称",
    "specification": "规格型号",
    "quantity_per_unit": "单台数量",
    "unit": "单位",
    "remark": "备注",
}


def initialize_context(monkeypatch: pytest.MonkeyPatch) -> TenantContext:
    monkeypatch.setenv("PMS_INITIAL_ADMIN_PASSWORD", PASSWORD)
    call_command("initialize_pms", no_color=True, verbosity=0)
    monkeypatch.delenv("PMS_INITIAL_ADMIN_PASSWORD")
    membership = Membership.objects.get()
    return TenantContext(
        tenant_id=TenantId(membership.tenant_id),
        user_id=UserId(membership.user_id),
        membership_id=MembershipId(membership.id),
    )


def create_member_context(*, tenant: Tenant, role: RoleCode, suffix: str) -> TenantContext:
    user = get_user_model().objects.create_user(username=f"bom-{suffix}")
    membership = Membership.objects.create(tenant=tenant, user=user)
    MembershipRole.objects.create(membership=membership, role=Role.objects.get(code=role))
    return TenantContext(
        tenant_id=TenantId(tenant.id),
        user_id=UserId(user.id),
        membership_id=MembershipId(membership.id),
    )


def master_service() -> MasterDataService:
    return MasterDataService(
        repository=DjangoMasterDataRepository(),
        grants=DjangoPermissionGrantLookup(),
        audit=DjangoAuditRecorder(),
        transactions=DjangoTransactionManager(),
    )


def projects_service() -> ProjectService:
    return ProjectService(
        repository=DjangoProjectRepository(),
        grants=DjangoPermissionGrantLookup(),
        audit=DjangoAuditRecorder(),
        transactions=DjangoProjectTransactionManager(),
        downstream=DjangoBomProjectDownstreamLookup(),
    )


class FailingAuditRecorder:
    """在 BOM 保存完成后模拟必要审计失败，验证整个发布事务回滚。"""

    def record(self, event: AuditEvent) -> UUID:
        del event
        raise RuntimeError("simulated BOM audit failure")


def bom_service(root: Path, *, audit: AuditRecorder | None = None) -> BomService:
    return BomService(
        repository=DjangoBomRepository(),
        parser=OpenPyxlBomSpreadsheetParser(),
        attachments=AttachmentService(
            repository=DjangoAttachmentRepository(),
            storage=LocalBinaryStorage(root),
        ),
        grants=DjangoPermissionGrantLookup(),
        audit=audit or DjangoAuditRecorder(),
        transactions=DjangoBomTransactionManager(),
        downstream=DjangoBomProductionDownstreamLookup(),
    )


def make_workbook(rows: list[list[object]], *, formula: bool = False) -> bytes:
    workbook = Workbook()
    worksheet = workbook.active
    assert worksheet is not None
    worksheet.append(list(MAPPING.values()))
    for row in rows:
        worksheet.append(row)
    if formula:
        worksheet.cell(row=2, column=4, value="=1+1")
    stream = BytesIO()
    workbook.save(stream)
    workbook.close()
    return stream.getvalue()


def prepare_active_project(
    *, admin: TenantContext, owner_membership_id: MembershipId | None = None
) -> tuple[ProjectSnapshot, CreatedMasterData, CreatedMasterData]:
    masters = master_service()
    customer = masters.create_customer(context=admin, code="CUS-001", name="示例客户")
    unit = masters.create_unit(context=admin, code="PCS", name="件")
    category = masters.create_category(context=admin, code="STD", name="标准件")
    material = masters.create_material(
        context=admin,
        command=CreateMaterialCommand(
            code="MAT-001",
            name="示例电机",
            unit_id=unit.id,
            category_id=category.id,
            procurement_required=True,
        ),
    )
    owner_id = admin.membership_id if owner_membership_id is None else owner_membership_id
    project = projects_service().create_project(
        context=admin,
        command=CreateProjectCommand(
            number="DEMO-001",
            customer_id=customer.id,
            device_model="教学设备 A",
            owner_membership_id=owner_id,
        ),
    )
    projects_service().activate_project(context=admin, project_id=project.id)
    return project, unit, material


@pytest.mark.django_db
@pytest.mark.acceptance
def test_valid_bom_is_retained_published_superseded_and_diffed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC-S001-010/013/020/021：来源留存，发布不可变，新版本原子替代并可比较。"""
    admin = initialize_context(monkeypatch)
    project, _unit, material = prepare_active_project(admin=admin)
    service = bom_service(tmp_path)
    v1 = service.import_bom(
        context=admin,
        command=ImportBomCommand(
            project_id=project.id,
            version_number=1,
            filename="demo-v1.xlsx",
            content=make_workbook([["MAT-001", "示例电机", "", "2", "PCS", ""]]),
            mapping=MAPPING,
        ),
    )
    assert v1.error_count == 0
    assert Attachment.objects.get().status == "available"
    line_v1 = BomLine.objects.get(bom_version_id=v1.id)
    assert line_v1.source_row_number == 2
    assert line_v1.material_id == material.id
    assert service.publish_bom(context=admin, bom_id=v1.id).status is BomStatus.PUBLISHED

    with pytest.raises(InvalidBomTransitionError):
        service.assign_line_material(
            context=admin,
            bom_id=v1.id,
            line_id=line_v1.id,
            material_id=material.id,
        )

    v2 = service.import_bom(
        context=admin,
        command=ImportBomCommand(
            project_id=project.id,
            version_number=2,
            filename="demo-v2.xlsm",
            content=make_workbook([["MAT-001", "示例电机", "", "3", "PCS", "调整"]]),
            mapping=MAPPING,
        ),
    )
    with pytest.raises(RuntimeError, match="simulated BOM audit failure"):
        bom_service(tmp_path, audit=FailingAuditRecorder()).publish_bom(context=admin, bom_id=v2.id)
    assert BomVersion.objects.get(id=v1.id).status == BomStatus.PUBLISHED
    assert BomVersion.objects.get(id=v2.id).status == BomStatus.DRAFT

    service.publish_bom(context=admin, bom_id=v2.id)
    assert BomVersion.objects.get(id=v1.id).status == BomStatus.SUPERSEDED
    assert service.compare_versions(context=admin, left_id=v1.id, right_id=v2.id).changed == (
        "MAT-001",
    )
    assert AuditLog.objects.filter(action="bom.published").count() == 2


@pytest.mark.django_db
@pytest.mark.acceptance
def test_invalid_rows_keep_source_numbers_and_block_publish(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC-S001-012/014—018：错误逐行保留，公式和重复不被静默计算或合并。"""
    admin = initialize_context(monkeypatch)
    project, _unit, _material = prepare_active_project(admin=admin)
    service = bom_service(tmp_path)
    draft = service.import_bom(
        context=admin,
        command=ImportBomCommand(
            project_id=project.id,
            version_number=1,
            filename="invalid.xlsx",
            content=make_workbook(
                [
                    ["MAT-001", "示例电机", "", "0", "UNKNOWN", ""],
                    ["MAT-001", "示例电机", "", "2", "PCS", "重复"],
                    ["", "非标支架", "自制", "1", "PCS", "待确认"],
                ],
                formula=True,
            ),
            mapping=MAPPING,
        ),
    )
    rows = list(BomLine.objects.filter(bom_version_id=draft.id))
    first_errors = set(rows[0].validation_errors)
    assert rows[0].source_row_number == 2
    assert BomLineErrorCode.FORMULA_NOT_ALLOWED in first_errors
    assert BomLineErrorCode.INVALID_QUANTITY in first_errors
    assert BomLineErrorCode.UNKNOWN_UNIT in first_errors
    assert BomLineErrorCode.SUSPECTED_DUPLICATE in first_errors
    assert BomLineErrorCode.MATERIAL_CONFIRMATION_REQUIRED in rows[2].validation_errors
    with pytest.raises(InvalidBomTransitionError, match="逐行错误"):
        service.publish_bom(context=admin, bom_id=draft.id)
    assert BomVersion.objects.get(id=draft.id).status == BomStatus.DRAFT


@pytest.mark.django_db
@pytest.mark.acceptance
def test_no_code_and_duplicate_rows_require_explicit_human_resolution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC-S001-016/018：人工确认只消除对应问题，系统从不按名称或重复键静默合并。"""
    admin = initialize_context(monkeypatch)
    project, _unit, material = prepare_active_project(admin=admin)
    service = bom_service(tmp_path)
    no_code = service.import_bom(
        context=admin,
        command=ImportBomCommand(
            project_id=project.id,
            version_number=1,
            filename="no-code.xlsx",
            content=make_workbook([["", "示例电机", "", "1", "PCS", "人工确认"]]),
            mapping=MAPPING,
        ),
    )
    pending_line = BomLine.objects.get(bom_version_id=no_code.id)
    assert no_code.error_count == 1
    corrected = service.assign_line_material(
        context=admin,
        bom_id=no_code.id,
        line_id=pending_line.id,
        material_id=material.id,
    )
    assert corrected.error_count == 0
    service.publish_bom(context=admin, bom_id=no_code.id)

    duplicate = service.import_bom(
        context=admin,
        command=ImportBomCommand(
            project_id=project.id,
            version_number=2,
            filename="duplicate.xlsx",
            content=make_workbook(
                [
                    ["MAT-001", "示例电机", "", "1", "PCS", "第一处"],
                    ["MAT-001", "示例电机", "", "1", "PCS", "第二处"],
                ]
            ),
            mapping=MAPPING,
        ),
    )
    assert duplicate.error_count == 2
    for line_id in BomLine.objects.filter(bom_version_id=duplicate.id).values_list("id", flat=True):
        service.confirm_duplicate(context=admin, bom_id=duplicate.id, line_id=line_id)
    assert service.publish_bom(context=admin, bom_id=duplicate.id).status is BomStatus.PUBLISHED
    assert BomLine.objects.filter(bom_version_id=duplicate.id).count() == 2


@pytest.mark.django_db
@pytest.mark.acceptance
def test_bom_role_boundary_and_project_cancel_history(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC-S001-009/019/037：负责人不能发布，工程人员在相关项目可发布，BOM 阻止取消。"""
    admin = initialize_context(monkeypatch)
    tenant = Tenant.objects.get(id=admin.tenant_id)
    engineer = create_member_context(tenant=tenant, role=RoleCode.BOM_ENGINEER, suffix="engineer")
    manager = create_member_context(tenant=tenant, role=RoleCode.PROJECT_MANAGER, suffix="manager")
    project, _unit, _material = prepare_active_project(
        admin=admin, owner_membership_id=engineer.membership_id
    )
    draft = bom_service(tmp_path).import_bom(
        context=engineer,
        command=ImportBomCommand(
            project_id=project.id,
            version_number=1,
            filename="engineer.xlsx",
            content=make_workbook([["MAT-001", "示例电机", "", "2", "PCS", ""]]),
            mapping=MAPPING,
        ),
    )
    with pytest.raises(PermissionDeniedError):
        bom_service(tmp_path).publish_bom(context=manager, bom_id=draft.id)
    assert (
        bom_service(tmp_path).publish_bom(context=engineer, bom_id=draft.id).status
        is BomStatus.PUBLISHED
    )
    with pytest.raises(InvalidProjectTransitionError, match="已有下游记录"):
        projects_service().cancel_project(
            context=admin,
            project_id=project.id,
            reason="项目终止但已有正式 BOM",
        )


@pytest.mark.django_db
@pytest.mark.acceptance
def test_closed_project_cannot_import_a_new_bom(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC-S001-008：项目关闭后不能通过 BOM 入口继续建立下游记录。"""
    admin = initialize_context(monkeypatch)
    project, _unit, _material = prepare_active_project(admin=admin)
    projects_service().close_project(context=admin, project_id=project.id)

    with pytest.raises(BomImportError, match="活动项目"):
        bom_service(tmp_path).import_bom(
            context=admin,
            command=ImportBomCommand(
                project_id=project.id,
                version_number=1,
                filename="closed-project.xlsx",
                content=make_workbook([["MAT-001", "示例电机", "", "2", "PCS", ""]]),
                mapping=MAPPING,
            ),
        )
    assert not BomVersion.objects.exists()


@pytest.mark.django_db
@pytest.mark.acceptance
def test_bom_cancel_keeps_source_lines_reason_actor_and_time(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """BOM 状态机：取消不删除版本或附件，并留下完整专用字段。"""
    admin = initialize_context(monkeypatch)
    project, _unit, _material = prepare_active_project(admin=admin)
    service = bom_service(tmp_path)
    draft = service.import_bom(
        context=admin,
        command=ImportBomCommand(
            project_id=project.id,
            version_number=1,
            filename="cancel-bom.xlsx",
            content=make_workbook([["MAT-001", "示例电机", "", "2", "PCS", ""]]),
            mapping=MAPPING,
        ),
    )

    cancelled = service.cancel_bom(
        context=admin,
        bom_id=draft.id,
        reason=" 设计   方案终止 ",
    )
    row = BomVersion.objects.get(id=draft.id)
    assert cancelled.status is BomStatus.CANCELLED
    assert row.cancellation_reason == "设计 方案终止"
    assert row.cancelled_by_membership_id == admin.membership_id
    assert row.cancelled_at is not None
    assert row.lines.count() == 1
    assert row.source_attachment.status == "available"
    with pytest.raises(InvalidBomTransitionError):
        service.publish_bom(context=admin, bom_id=draft.id)
