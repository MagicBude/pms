"""把受控旧数据包编排为正式 `SLICE-001` 应用用例。

迁移不是绕过业务规则的数据库脚本。每一步复用主数据、项目、BOM、投产
和请购服务，因此权限、事务、状态、审计及附件安全边界与浏览器完全一致。
中断后再次执行会复核并复用已经一致的稳定对象，不生成重复业务链。
"""

from datetime import date
from decimal import Decimal
from io import BytesIO
from uuid import UUID

from openpyxl import Workbook

from pms.bom.application.service import ImportBomCommand
from pms.bom.infrastructure.django.models import BomLine, BomVersion
from pms.legacy_migration.reconciliation import ReconciliationBuilder, ReconciliationReport
from pms.legacy_migration.schema import LegacyMaterial, LegacySlicePackage
from pms.master_data.application.service import CreateMaterialCommand
from pms.master_data.infrastructure.django.models import Customer, Material, MaterialCategory, Unit
from pms.platform.business_services import (
    bom_service,
    master_data_service,
    procurement_service,
    production_service,
    project_service,
)
from pms.procurement.infrastructure.django.models import PurchaseRequest, PurchaseRequestLine
from pms.production.application.service import CreateProductionCommand
from pms.production.infrastructure.django.models import ProductionRelease
from pms.projects.application.service import CreateProjectCommand
from pms.projects.infrastructure.django.models import Project
from pms.tenancy.domain.context import MembershipId, TenantContext, TenantId, UserId
from pms.tenancy.infrastructure.django.models import Membership

BOM_MAPPING = {
    "level_path": "层级",
    "assembly_code": "部套代号",
    "assembly_name": "部套名称",
    "material_code": "物料编码",
    "material_name": "物料名称",
    "specification": "规格型号",
    "brand": "品牌",
    "quantity_per_unit": "单台数量",
    "unit": "单位",
    "remark": "备注",
}


class LegacyImportConflictError(RuntimeError):
    """表示稳定业务键已存在，但内容与迁移包不一致，必须人工处理。"""


class LegacySliceMigrationService:
    """离线、可恢复的首切片迁移编排器。"""

    def migrate(
        self, *, package: LegacySlicePackage, actor_username: str = "admin"
    ) -> ReconciliationReport:
        """创建或复用完整业务链，并返回逐项对账结果。

        本操作只应在停止本机服务后的维护窗口执行。各应用服务各自保证
        原子性；若后续步骤失败，前面已经完成的合法对象会保留，下一次
        执行通过稳定编号、BOM 版本和请购幂等键复核后继续。
        """
        context, actor_membership = _actor_context(actor_username)
        customer = _ensure_customer(context=context, package=package)
        units = _ensure_units(context=context, package=package)
        categories = _ensure_categories(context=context, package=package)
        _ensure_materials(
            context=context,
            package=package,
            units=units,
            categories=categories,
        )
        owner = _owner_membership(context=context, username=package.project.owner_username)
        project = _ensure_project(
            context=context,
            package=package,
            customer=customer,
            owner=owner,
            actor=actor_membership,
        )
        bom = _ensure_bom(context=context, package=package, project=project)
        production = _ensure_production(
            context=context,
            package=package,
            project=project,
            bom=bom,
        )
        purchase_request = _ensure_request(
            context=context,
            package=package,
            production=production,
        )
        return _reconcile(
            package=package,
            project=project,
            bom=bom,
            production=production,
            purchase_request=purchase_request,
        )


def _actor_context(username: str) -> tuple[TenantContext, Membership]:
    memberships = list(
        Membership.objects.filter(
            user__username=username,
            user__is_active=True,
            tenant__is_active=True,
            is_active=True,
        )
        .select_related("user", "tenant")
        .order_by("created_at", "id")[:2]
    )
    if len(memberships) != 1:
        raise LegacyImportConflictError("迁移操作者必须恰好拥有一个活动 tenant membership。")
    membership = memberships[0]
    return (
        TenantContext(
            tenant_id=TenantId(membership.tenant_id),
            user_id=UserId(membership.user_id),
            membership_id=MembershipId(membership.id),
        ),
        membership,
    )


def _ensure_customer(*, context: TenantContext, package: LegacySlicePackage) -> Customer:
    existing = Customer.objects.filter(
        tenant_id=context.tenant_id, code__iexact=package.customer.code
    ).first()
    if existing is None:
        created = master_data_service().create_customer(
            context=context, code=package.customer.code, name=package.customer.name
        )
        return Customer.objects.get(id=created.id)
    if existing.name != package.customer.name or not existing.is_active:
        raise LegacyImportConflictError("既有客户与迁移包不一致。")
    return existing


def _ensure_units(*, context: TenantContext, package: LegacySlicePackage) -> dict[str, Unit]:
    result: dict[str, Unit] = {}
    for item in package.units:
        existing = Unit.objects.filter(tenant_id=context.tenant_id, code__iexact=item.code).first()
        if existing is None:
            created = master_data_service().create_unit(
                context=context, code=item.code, name=item.name
            )
            existing = Unit.objects.get(id=created.id)
        elif existing.name != item.name or not existing.is_active:
            raise LegacyImportConflictError(f"既有单位 {item.code} 与迁移包不一致。")
        result[item.code.casefold()] = existing
    return result


def _ensure_categories(
    *, context: TenantContext, package: LegacySlicePackage
) -> dict[str, MaterialCategory]:
    result: dict[str, MaterialCategory] = {}
    for item in package.categories:
        existing = MaterialCategory.objects.filter(
            tenant_id=context.tenant_id, code__iexact=item.code
        ).first()
        if existing is None:
            created = master_data_service().create_category(
                context=context, code=item.code, name=item.name
            )
            existing = MaterialCategory.objects.get(id=created.id)
        elif existing.name != item.name or not existing.is_active:
            raise LegacyImportConflictError(f"既有分类 {item.code} 与迁移包不一致。")
        result[item.code.casefold()] = existing
    return result


def _ensure_materials(
    *,
    context: TenantContext,
    package: LegacySlicePackage,
    units: dict[str, Unit],
    categories: dict[str, MaterialCategory],
) -> None:
    for item in package.materials:
        unit = units[item.unit_code.casefold()]
        category = categories[item.category_code.casefold()]
        existing = Material.objects.filter(
            tenant_id=context.tenant_id, code__iexact=item.code
        ).first()
        if existing is None:
            master_data_service().create_material(
                context=context,
                command=CreateMaterialCommand(
                    code=item.code,
                    name=item.name,
                    specification=item.specification,
                    brand=item.brand,
                    part_attribute=item.part_attribute,
                    unit_id=unit.id,
                    category_id=category.id,
                    procurement_required=item.procurement_required,
                ),
            )
        elif not _material_matches(existing, item, unit_id=unit.id, category_id=category.id):
            raise LegacyImportConflictError(f"既有物料 {item.code} 与迁移包不一致。")


def _material_matches(
    existing: Material, item: LegacyMaterial, *, unit_id: UUID, category_id: UUID
) -> bool:
    return (
        existing.name == item.name
        and existing.specification == item.specification
        and existing.brand == item.brand
        and existing.part_attribute == item.part_attribute
        and existing.unit_id == unit_id
        and existing.category_id == category_id
        and existing.procurement_required is item.procurement_required
        and existing.is_active
    )


def _owner_membership(*, context: TenantContext, username: str) -> Membership:
    owner = Membership.objects.filter(
        tenant_id=context.tenant_id,
        user__username=username,
        user__is_active=True,
        is_active=True,
    ).first()
    if owner is None:
        raise LegacyImportConflictError("项目负责人不是当前 tenant 的活动成员。")
    return owner


def _ensure_project(
    *,
    context: TenantContext,
    package: LegacySlicePackage,
    customer: Customer,
    owner: Membership,
    actor: Membership,
) -> Project:
    existing = Project.objects.filter(
        tenant_id=context.tenant_id, number__iexact=package.project.number
    ).first()
    if existing is None:
        created = project_service().create_project(
            context=context,
            command=CreateProjectCommand(
                number=package.project.number,
                customer_id=customer.id,
                device_model=package.project.device_model,
                owner_membership_id=owner.id,
                start_date=package.project.start_date,
                planned_completion_date=package.project.planned_completion_date,
            ),
        )
        project_service().activate_project(context=context, project_id=created.id)
        return Project.objects.get(id=created.id)
    if (
        existing.customer_id != customer.id
        or existing.device_model != package.project.device_model
        or existing.owner_membership_id != owner.id
        or existing.created_by_membership_id != actor.id
        or existing.start_date != package.project.start_date
        or existing.planned_completion_date != package.project.planned_completion_date
        or existing.status not in {"ACTIVE", "CLOSED"}
    ):
        raise LegacyImportConflictError("既有项目与迁移包或可继续状态不一致。")
    return existing


def _ensure_bom(
    *, context: TenantContext, package: LegacySlicePackage, project: Project
) -> BomVersion:
    existing = BomVersion.objects.filter(
        tenant_id=context.tenant_id,
        project=project,
        version_number=package.bom.version_number,
    ).first()
    if existing is None:
        imported = bom_service().import_bom(
            context=context,
            command=ImportBomCommand(
                project_id=project.id,
                version_number=package.bom.version_number,
                filename=f"legacy-{package.sample.id}.xlsx",
                content=_build_bom_workbook(package),
                mapping=BOM_MAPPING,
            ),
        )
        if imported.error_count:
            raise LegacyImportConflictError("迁移包 BOM 未通过新系统逐行校验。")
        bom_service().publish_bom(context=context, bom_id=imported.id)
        return BomVersion.objects.get(id=imported.id)
    _validate_existing_bom(existing, package)
    if existing.status == "DRAFT":
        bom_service().publish_bom(context=context, bom_id=existing.id)
        existing.refresh_from_db()
    if existing.status not in {"PUBLISHED", "SUPERSEDED"}:
        raise LegacyImportConflictError("既有 BOM 不是可复用的发布历史。")
    return existing


def _build_bom_workbook(package: LegacySlicePackage) -> bytes:
    workbook = Workbook()
    worksheet = workbook.active
    if worksheet is None:
        raise LegacyImportConflictError("无法建立受控 BOM 工作表。")
    worksheet.title = "BOM"
    worksheet.append(list(BOM_MAPPING.values()))
    for row in package.bom.rows:
        values = [
            row.level_path,
            row.assembly_code,
            row.assembly_name,
            row.material_code,
            row.material_name,
            row.specification,
            row.brand,
            str(row.quantity_per_unit),
            row.unit_code,
            row.remark,
        ]
        for column, value in enumerate(values, start=1):
            worksheet.cell(row=row.source_row_number, column=column, value=value)
    stream = BytesIO()
    workbook.save(stream)
    workbook.close()
    return stream.getvalue()


def _validate_existing_bom(bom: BomVersion, package: LegacySlicePackage) -> None:
    actual = list(
        BomLine.objects.filter(tenant_id=bom.tenant_id, bom_version=bom).order_by(
            "source_row_number", "id"
        )
    )
    if len(actual) != len(package.bom.rows):
        raise LegacyImportConflictError("既有 BOM 行数与迁移包不一致。")
    for expected, row in zip(package.bom.rows, actual, strict=True):
        if (
            row.source_row_number != expected.source_row_number
            or row.level_path != expected.level_path
            or row.assembly_code != expected.assembly_code
            or row.assembly_name != expected.assembly_name
            or row.material_code.casefold() != expected.material_code.casefold()
            or row.material_name != expected.material_name
            or row.specification != expected.specification
            or row.brand != expected.brand
            or row.quantity_per_unit != expected.quantity_per_unit
            or row.unit_text.casefold() != expected.unit_code.casefold()
            or row.remark != expected.remark
            or row.validation_errors
        ):
            raise LegacyImportConflictError("既有 BOM 明细与迁移包不一致。")


def _ensure_production(
    *,
    context: TenantContext,
    package: LegacySlicePackage,
    project: Project,
    bom: BomVersion,
) -> ProductionRelease:
    candidates = list(
        ProductionRelease.objects.filter(
            tenant_id=context.tenant_id,
            project=project,
            bom_version=bom,
            production_units=package.production.production_units,
            production_unit=package.production.production_unit,
            receiving_department=package.production.receiving_department,
        ).order_by("created_at", "id")[:2]
    )
    if len(candidates) > 1:
        raise LegacyImportConflictError("存在多条无法唯一复用的投产批次。")
    if candidates:
        production = candidates[0]
    else:
        created = production_service().create_draft(
            context=context,
            command=CreateProductionCommand(
                project_id=project.id,
                bom_id=bom.id,
                production_units=package.production.production_units,
                production_unit=package.production.production_unit,
                receiving_department=package.production.receiving_department,
            ),
        )
        production = ProductionRelease.objects.get(id=created.id)
    if production.status == "DRAFT":
        production_service().release(context=context, production_id=production.id)
        production.refresh_from_db()
    if production.status != "RELEASED":
        raise LegacyImportConflictError("既有投产批次不是可复用的已发布状态。")
    return production


def _ensure_request(
    *, context: TenantContext, package: LegacySlicePackage, production: ProductionRelease
) -> PurchaseRequest:
    snapshot = procurement_service().create_draft(
        context=context,
        production_id=production.id,
        idempotency_key=f"legacy-migration:{package.sample.id}",
    )
    submitted = procurement_service().submit(context=context, request_id=snapshot.id)
    return PurchaseRequest.objects.get(id=submitted.id)


def _reconcile(
    *,
    package: LegacySlicePackage,
    project: Project,
    bom: BomVersion,
    production: ProductionRelease,
    purchase_request: PurchaseRequest,
) -> ReconciliationReport:
    builder = ReconciliationBuilder(
        sample=package.sample, accepted_differences=package.accepted_differences
    )
    builder.compare(
        check_key="project.number",
        rule_id="PRJ-001",
        legacy_value=package.project.number,
        new_value=project.number,
    )
    builder.compare(
        check_key="project.customer_code",
        rule_id="MDM-001",
        legacy_value=package.project.customer_code,
        new_value=project.customer.code,
    )
    builder.compare(
        check_key="project.device_model",
        rule_id="PRJ-001",
        legacy_value=package.project.device_model,
        new_value=project.device_model,
    )
    builder.compare(
        check_key="project.dates",
        rule_id="PRJ-001",
        legacy_value={
            "start_date": _date_text(package.project.start_date),
            "planned_completion_date": _date_text(package.project.planned_completion_date),
        },
        new_value={
            "start_date": _date_text(project.start_date),
            "planned_completion_date": _date_text(project.planned_completion_date),
        },
    )
    builder.compare(
        check_key="production.units",
        rule_id="BR-PRD-001",
        legacy_value=package.production.production_units,
        new_value=production.production_units,
    )
    builder.compare(
        check_key="production.unit_and_department",
        rule_id="PRD-001",
        legacy_value={
            "production_unit": package.production.production_unit,
            "receiving_department": package.production.receiving_department,
        },
        new_value={
            "production_unit": production.production_unit,
            "receiving_department": production.receiving_department,
        },
    )
    actual_materials = tuple(
        {
            "code": material.code,
            "name": material.name,
            "specification": material.specification,
            "brand": material.brand,
            "part_attribute": material.part_attribute,
            "unit_code": material.unit.code,
            "category_code": material.category.code,
            "procurement_required": material.procurement_required,
        }
        for material in Material.objects.filter(
            tenant_id=project.tenant_id,
            code__in=[item.code for item in package.materials],
        )
        .select_related("unit", "category")
        .order_by("code", "id")
    )
    legacy_materials = tuple(
        {
            "code": item.code,
            "name": item.name,
            "specification": item.specification,
            "brand": item.brand,
            "part_attribute": item.part_attribute,
            "unit_code": item.unit_code,
            "category_code": item.category_code,
            "procurement_required": item.procurement_required,
        }
        for item in sorted(package.materials, key=lambda value: value.code)
    )
    builder.compare(
        check_key="master_data.materials",
        rule_id="MDM-003",
        legacy_value=legacy_materials,
        new_value=actual_materials,
    )
    actual_bom_rows = list(
        BomLine.objects.filter(bom_version=bom).order_by("source_row_number", "id")
    )
    for expected, actual in zip(package.bom.rows, actual_bom_rows, strict=True):
        builder.compare(
            check_key=f"bom.row.{actual.source_row_number}.identity",
            rule_id="BOM-001",
            legacy_value={
                "source_row_number": expected.source_row_number,
                "assembly_code": expected.assembly_code,
                "assembly_name": expected.assembly_name,
                "material_code": expected.material_code,
                "material_name": expected.material_name,
                "specification": expected.specification,
                "brand": expected.brand,
                "unit_code": expected.unit_code,
                "remark": expected.remark,
            },
            new_value={
                "source_row_number": actual.source_row_number,
                "assembly_code": actual.assembly_code,
                "assembly_name": actual.assembly_name,
                "material_code": actual.material_code,
                "material_name": actual.material_name,
                "specification": actual.specification,
                "brand": actual.brand,
                "unit_code": actual.unit_text,
                "remark": actual.remark,
            },
        )
        builder.compare(
            check_key=f"bom.row.{actual.source_row_number}.quantity_per_unit",
            rule_id="BOM-001",
            legacy_value=_decimal_text(expected.quantity_per_unit),
            new_value=(
                _decimal_text(actual.quantity_per_unit)
                if actual.quantity_per_unit is not None
                else ""
            ),
        )
    actual_candidates = tuple(
        {
            "source_row_number": line.source_requirement.source_bom_line.source_row_number,
            "material_code": line.material_code_snapshot,
            "requested_quantity": _decimal_text(line.requested_quantity),
            "unit_code": line.unit.code,
        }
        for line in PurchaseRequestLine.objects.filter(purchase_request=purchase_request)
        .select_related("source_requirement__source_bom_line", "unit")
        .order_by("source_requirement__source_bom_line__source_row_number", "id")
    )
    legacy_candidates = tuple(
        {
            "source_row_number": item.source_row_number,
            "material_code": item.material_code,
            "requested_quantity": _decimal_text(item.requested_quantity),
            "unit_code": item.unit_code,
        }
        for item in package.purchase_candidates
    )
    builder.compare(
        check_key="purchase.candidate_count",
        rule_id="BR-PUR-001",
        legacy_value=len(legacy_candidates),
        new_value=len(actual_candidates),
    )
    builder.compare(
        check_key="purchase.candidates",
        rule_id="BR-PUR-001",
        legacy_value=legacy_candidates,
        new_value=actual_candidates,
    )
    return builder.build()


def _decimal_text(value: Decimal) -> str:
    """以数据库六位精度比较迁移数量，避免 ``1`` 与 ``1.000000`` 假差异。"""
    return format(value, ".6f")


def _date_text(value: date | None) -> str | None:
    """把可选业务日期转为稳定 JSON 文本，避免报告依赖编码器魔法。"""
    return None if value is None else value.isoformat()
