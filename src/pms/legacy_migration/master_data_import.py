"""通过正式主数据应用用例幂等导入客户与供应商规范包。"""

from dataclasses import dataclass

from django.db import transaction

from pms.identity.infrastructure.django.models import User
from pms.legacy_migration.master_data_package import (
    LegacyCustomerRecord,
    LegacyMasterDataPackage,
    LegacySupplierRecord,
)
from pms.master_data.application.service import CreateCustomerCommand, CreateSupplierCommand
from pms.master_data.infrastructure.django.models import Customer, Supplier
from pms.platform.business_services import master_data_service
from pms.tenancy.domain.context import MembershipId, TenantContext, TenantId, UserId
from pms.tenancy.infrastructure.django.models import Membership


class LegacyMasterDataImportConflictError(ValueError):
    """表示同一稳定代码已存在，但内容与迁移包不同。"""


@dataclass(frozen=True, slots=True)
class LegacyMasterDataImportReport:
    """不含业务值的导入与对账汇总。"""

    source_manifest_sha256: str
    customer_total: int
    customer_created: int
    customer_reused: int
    supplier_total: int
    supplier_created: int
    supplier_reused: int


def import_legacy_master_data(
    *, package: LegacyMasterDataPackage, actor_username: str
) -> LegacyMasterDataImportReport:
    """全量原子导入；一致记录复用，任何冲突使整个批次回滚。"""
    context = _actor_context(actor_username)
    customer_created = 0
    supplier_created = 0
    with transaction.atomic():
        for item in package.customers:
            existing = Customer.objects.filter(
                tenant_id=context.tenant_id, code__iexact=item.code
            ).first()
            if existing is None:
                master_data_service().create_customer(
                    context=context,
                    command=CreateCustomerCommand(
                        code=item.code,
                        name=item.name,
                        short_name=item.short_name,
                        tax_identifier=item.tax_identifier,
                        address=item.address,
                        phone=item.phone,
                        bank_name=item.bank_name,
                        bank_account=item.bank_account,
                        bank_routing_number=item.bank_routing_number,
                    ),
                )
                customer_created += 1
            elif not _customer_matches(existing, item):
                raise LegacyMasterDataImportConflictError(
                    f"客户来源第 {item.source_row_number} 行与既有记录冲突。"
                )
        for supplier_item in package.suppliers:
            existing_supplier = Supplier.objects.filter(
                tenant_id=context.tenant_id, code__iexact=supplier_item.code
            ).first()
            if existing_supplier is None:
                master_data_service().create_supplier(
                    context=context,
                    command=CreateSupplierCommand(
                        code=supplier_item.code,
                        name=supplier_item.name,
                        short_name=supplier_item.short_name,
                        contact_person=supplier_item.contact_person,
                        phone=supplier_item.phone,
                        address=supplier_item.address,
                        tax_identifier=supplier_item.tax_identifier,
                        bank_routing_number=supplier_item.bank_routing_number,
                        bank_name=supplier_item.bank_name,
                        bank_account=supplier_item.bank_account,
                        service_description=supplier_item.service_description,
                        english_name=supplier_item.english_name,
                        english_address=supplier_item.english_address,
                    ),
                )
                supplier_created += 1
            elif not _supplier_matches(existing_supplier, supplier_item):
                raise LegacyMasterDataImportConflictError(
                    f"供应商来源第 {supplier_item.source_row_number} 行与既有记录冲突。"
                )
    return LegacyMasterDataImportReport(
        source_manifest_sha256=package.source_manifest_sha256,
        customer_total=len(package.customers),
        customer_created=customer_created,
        customer_reused=len(package.customers) - customer_created,
        supplier_total=len(package.suppliers),
        supplier_created=supplier_created,
        supplier_reused=len(package.suppliers) - supplier_created,
    )


def _actor_context(username: str) -> TenantContext:
    users = User.objects.filter(username=username, is_active=True)
    if users.count() != 1:
        raise LegacyMasterDataImportConflictError("迁移操作者不存在或不可用。")
    memberships = list(
        Membership.objects.filter(
            user=users.get(), tenant__is_active=True, is_active=True
        ).order_by("created_at", "id")[:2]
    )
    if len(memberships) != 1:
        raise LegacyMasterDataImportConflictError(
            "迁移操作者必须恰好拥有一个活动 tenant membership。"
        )
    membership = memberships[0]
    return TenantContext(
        tenant_id=TenantId(membership.tenant_id),
        user_id=UserId(membership.user_id),
        membership_id=MembershipId(membership.id),
    )


def _customer_matches(existing: Customer, item: LegacyCustomerRecord) -> bool:
    fields = (
        "name",
        "short_name",
        "tax_identifier",
        "address",
        "phone",
        "bank_name",
        "bank_account",
        "bank_routing_number",
    )
    return existing.is_active and all(
        getattr(existing, field) == getattr(item, field) for field in fields
    )


def _supplier_matches(existing: Supplier, item: LegacySupplierRecord) -> bool:
    fields = (
        "name",
        "short_name",
        "contact_person",
        "phone",
        "address",
        "tax_identifier",
        "bank_routing_number",
        "bank_name",
        "bank_account",
        "service_description",
        "english_name",
        "english_address",
    )
    return existing.is_active and all(
        getattr(existing, field) == getattr(item, field) for field in fields
    )
