"""P2-03 投产与生产请购端到端事务、编号和防重复测试。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier
from uuid import UUID

import pytest
from django.db import connection

from pms.audit.application.recorder import AuditRecorder
from pms.audit.domain.events import AuditEvent
from pms.audit.infrastructure.django.models import AuditLog
from pms.audit.infrastructure.django.recorder import DjangoAuditRecorder
from pms.authorization.application.authorize import PermissionDeniedError
from pms.authorization.domain.permissions import RoleCode
from pms.authorization.infrastructure.django.grant_lookup import DjangoPermissionGrantLookup
from pms.bom.application.service import ImportBomCommand
from pms.master_data.application.service import CreateMaterialCommand
from pms.master_data.infrastructure.django.models import Material
from pms.procurement.application.service import (
    ProcurementService,
    PurchaseRequestConflictError,
    PurchaseRequestNotFoundError,
)
from pms.procurement.domain.request import PurchaseRequestStatus
from pms.procurement.infrastructure.django.models import (
    PurchaseRequest,
    PurchaseRequestLine,
    PurchaseRequestSequence,
)
from pms.procurement.infrastructure.django.repository import (
    DjangoProcurementProductionDownstreamLookup,
    DjangoProcurementRepository,
    DjangoProcurementTransactionManager,
)
from pms.production.application.service import (
    CreateProductionCommand,
    ProductionService,
    ProductionSnapshot,
)
from pms.production.domain.release import InvalidProductionError, ProductionStatus
from pms.production.infrastructure.django.models import ProductionRelease, ProductionRequirement
from pms.production.infrastructure.django.repository import (
    DjangoProductionRepository,
    DjangoProductionTransactionManager,
)
from pms.tenancy.domain.context import TenantContext
from pms.tenancy.infrastructure.django.models import Tenant
from tests.integration.business.test_bom_workflow import (
    MAPPING,
    bom_service,
    create_member_context,
    initialize_context,
    make_workbook,
    master_service,
    prepare_active_project,
)


class FailingAuditRecorder:
    """模拟提交审计失败，证明编号、状态和序列与审计同事务回滚。"""

    def record(self, event: AuditEvent) -> UUID:
        del event
        raise RuntimeError("simulated audit failure")


def production_service() -> ProductionService:
    return ProductionService(
        repository=DjangoProductionRepository(),
        grants=DjangoPermissionGrantLookup(),
        audit=DjangoAuditRecorder(),
        transactions=DjangoProductionTransactionManager(),
        downstream=DjangoProcurementProductionDownstreamLookup(),
    )


def procurement_service(
    *,
    audit: AuditRecorder | None = None,
    now: datetime = datetime(2026, 8, 24, 16, 30, tzinfo=UTC),
) -> ProcurementService:
    return ProcurementService(
        repository=DjangoProcurementRepository(),
        grants=DjangoPermissionGrantLookup(),
        audit=audit or DjangoAuditRecorder(),
        transactions=DjangoProcurementTransactionManager(),
        clock=lambda: now,
    )


def create_released_production(
    *, context: TenantContext, tmp_path: Path, number_suffix: str = "1"
) -> ProductionSnapshot:
    project, unit, _material = prepare_active_project(admin=context)
    category_id = Material.objects.get(code="MAT-001").category_id
    non_procurement = master_service().create_material(
        context=context,
        command=CreateMaterialCommand(
            code=f"MAT-NP-{number_suffix}",
            name=f"不请购物料 {number_suffix}",
            unit_id=unit.id,
            category_id=category_id,
            procurement_required=False,
        ),
    )
    bom = bom_service(tmp_path).import_bom(
        context=context,
        command=ImportBomCommand(
            project_id=project.id,
            version_number=1,
            filename=f"production-{number_suffix}.xlsx",
            content=make_workbook(
                [
                    ["MAT-001", "示例电机", "", "2", "PCS", ""],
                    [non_procurement.code, non_procurement.name, "", "5", "PCS", ""],
                ]
            ),
            mapping=MAPPING,
        ),
    )
    bom_service(tmp_path).publish_bom(context=context, bom_id=bom.id)
    production = production_service().create_draft(
        context=context,
        command=CreateProductionCommand(
            project_id=project.id,
            bom_id=bom.id,
            production_units=3,
            production_unit="台",
            receiving_department="装配部",
        ),
    )
    return production_service().release(context=context, production_id=production.id)


@pytest.mark.django_db
@pytest.mark.acceptance
def test_release_snapshots_formula_and_request_excludes_non_procurement(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC-S001-023—026/028：发布固化数量与物料快照，请购只含明确可请购行。"""
    context = initialize_context(monkeypatch)
    production = create_released_production(context=context, tmp_path=tmp_path)

    requirements = list(
        ProductionRequirement.objects.filter(production_release_id=production.id).order_by(
            "material_code_snapshot"
        )
    )
    assert production.status is ProductionStatus.RELEASED
    assert [item.required_quantity for item in requirements] == [6, 15]
    Material.objects.filter(code="MAT-001").update(name="后来修改的名称")
    assert requirements[0].material_name_snapshot == "示例电机"

    request = procurement_service().create_draft(
        context=context,
        production_id=production.id,
        idempotency_key="create-production-1",
    )
    line = PurchaseRequestLine.objects.get(purchase_request_id=request.id)
    assert request.line_count == 1
    assert line.material_code_snapshot == "MAT-001"
    assert line.requested_quantity == 6


@pytest.mark.django_db
@pytest.mark.acceptance
def test_request_creation_is_idempotent_numbered_in_tenant_timezone_and_cancellable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC-S001-029—032/035：防重复、时区编号和取消恢复在同一来源数量上成立。"""
    context = initialize_context(monkeypatch)
    production = create_released_production(context=context, tmp_path=tmp_path)
    service = procurement_service()
    first = service.create_draft(
        context=context,
        production_id=production.id,
        idempotency_key="double-click-1",
    )
    retry = service.create_draft(
        context=context,
        production_id=production.id,
        idempotency_key="double-click-1",
    )
    assert retry.id == first.id
    assert PurchaseRequest.objects.count() == 1
    with pytest.raises(PurchaseRequestConflictError, match="已经存在"):
        service.create_draft(
            context=context,
            production_id=production.id,
            idempotency_key="different-key",
        )

    submitted = service.submit(context=context, request_id=first.id)
    assert submitted.request_number == "20260825-001"
    assert service.submit(context=context, request_id=first.id).id == first.id
    cancelled = service.cancel(context=context, request_id=first.id, reason=" 项目   调整 ")
    assert cancelled.status is PurchaseRequestStatus.CANCELLED
    assert PurchaseRequest.objects.get(id=first.id).cancellation_reason == "项目 调整"

    replacement = service.create_draft(
        context=context,
        production_id=production.id,
        idempotency_key="replacement-after-cancel",
    )
    replacement_line = PurchaseRequestLine.objects.get(purchase_request_id=replacement.id)
    assert replacement_line.requested_quantity == 6
    assert (
        service.submit(context=context, request_id=replacement.id).request_number == "20260825-002"
    )


@pytest.mark.django_db
@pytest.mark.acceptance
def test_submit_audit_failure_rolls_back_number_status_and_sequence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC-S001-034：编号、状态、行校验和审计必须整体成功或整体回滚。"""
    context = initialize_context(monkeypatch)
    production = create_released_production(context=context, tmp_path=tmp_path)
    request = procurement_service().create_draft(
        context=context,
        production_id=production.id,
        idempotency_key="rollback-submit",
    )

    with pytest.raises(RuntimeError, match="simulated audit failure"):
        procurement_service(audit=FailingAuditRecorder()).submit(
            context=context, request_id=request.id
        )

    persisted = PurchaseRequest.objects.get(id=request.id)
    assert persisted.status == PurchaseRequestStatus.DRAFT
    assert persisted.request_number is None
    assert PurchaseRequestSequence.objects.count() == 0
    assert not AuditLog.objects.filter(action="purchase_request.submitted").exists()


@pytest.mark.django_db
@pytest.mark.acceptance
def test_production_cannot_cancel_until_every_request_is_cancelled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC-S001-027/035：有效请购保护投产，取消请购后来源链仍保留且投产可取消。"""
    context = initialize_context(monkeypatch)
    production = create_released_production(context=context, tmp_path=tmp_path)
    request = procurement_service().create_draft(
        context=context,
        production_id=production.id,
        idempotency_key="cancel-boundary",
    )
    with pytest.raises(InvalidProductionError, match="未取消请购"):
        production_service().cancel(context=context, production_id=production.id)

    procurement_service().cancel(context=context, request_id=request.id, reason="不再采购")
    cancelled = production_service().cancel(context=context, production_id=production.id)
    assert cancelled.status is ProductionStatus.CANCELLED
    assert ProductionRequirement.objects.filter(production_release_id=production.id).count() == 2


@pytest.mark.django_db
@pytest.mark.acceptance
def test_purchase_request_permission_and_tenant_boundaries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC-S001-037—039：项目负责人不能创建请购，另一租户直接 ID 统一按不存在处理。"""
    admin = initialize_context(monkeypatch)
    production = create_released_production(context=admin, tmp_path=tmp_path)
    local_tenant = Tenant.objects.get(id=admin.tenant_id)
    manager = create_member_context(
        tenant=local_tenant, role=RoleCode.PROJECT_MANAGER, suffix="production-manager"
    )
    with pytest.raises(PermissionDeniedError):
        procurement_service().create_draft(
            context=manager,
            production_id=production.id,
            idempotency_key="manager-not-requester",
        )

    other_tenant = Tenant.objects.create(code="other-procurement", name="Other Procurement")
    other_admin = create_member_context(
        tenant=other_tenant, role=RoleCode.TENANT_ADMIN, suffix="other-admin"
    )
    with pytest.raises(PurchaseRequestNotFoundError):
        procurement_service().create_draft(
            context=other_admin,
            production_id=production.id,
            idempotency_key="cross-tenant-id",
        )


@pytest.mark.django_db(transaction=True)
@pytest.mark.postgresql
@pytest.mark.acceptance
def test_concurrent_submissions_receive_distinct_tenant_numbers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC-S001-033：PostgreSQL 行锁与唯一约束为同日并发提交分配不同编号。"""
    if connection.vendor != "postgresql":
        pytest.skip("并发编号验收只在 PostgreSQL 事务语义下执行。")
    context = initialize_context(monkeypatch)
    first_production = create_released_production(context=context, tmp_path=tmp_path)
    first_request = procurement_service().create_draft(
        context=context,
        production_id=first_production.id,
        idempotency_key="concurrent-one",
    )
    # 同一已发布 BOM 可以形成另一投产批次；来源独立，所以可并发提交第二份请购。
    first_release = first_production
    source = ProductionRelease.objects.get(id=first_release.id)
    second_draft = production_service().create_draft(
        context=context,
        command=CreateProductionCommand(
            project_id=source.project_id,
            bom_id=source.bom_version_id,
            production_units=1,
            production_unit="台",
            receiving_department="装配部",
        ),
    )
    second_production = production_service().release(context=context, production_id=second_draft.id)
    second_request = procurement_service().create_draft(
        context=context,
        production_id=second_production.id,
        idempotency_key="concurrent-two",
    )
    barrier = Barrier(2)

    def submit_after_barrier(request_id: UUID) -> str:
        barrier.wait()
        result = procurement_service().submit(context=context, request_id=request_id)
        assert result.request_number is not None
        return result.request_number

    with ThreadPoolExecutor(max_workers=2) as executor:
        numbers = set(executor.map(submit_after_barrier, (first_request.id, second_request.id)))
    assert numbers == {"20260825-001", "20260825-002"}
