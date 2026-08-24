"""主数据创建用例。

用例从可信 ``TenantContext`` 取得租户和操作者，先授权，再在同一事务中
写入业务记录与审计。仓储和事务均为端口，因此本层不依赖 Django ORM。
"""

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from pms.audit.application.recorder import AuditRecorder
from pms.audit.domain.events import AuditEvent, AuditResult
from pms.authorization.application.authorize import PermissionGrantLookup, authorize
from pms.authorization.domain.permissions import PermissionCode
from pms.master_data.domain.values import (
    normalize_code,
    normalize_name,
    normalize_optional_text,
)
from pms.tenancy.domain.context import TenantContext


class TransactionManager(Protocol):
    """为一次业务用例提供数据库原子边界。"""

    def atomic(self) -> AbstractContextManager[None]:
        """返回成功提交、异常回滚的上下文管理器。"""


@dataclass(frozen=True, slots=True)
class CreatedMasterData:
    """界面和下游模块可安全使用的创建结果。"""

    id: UUID
    code: str
    name: str


class MasterDataRepository(Protocol):
    """主数据模块拥有的最小写入端口。"""

    def create_customer(
        self,
        *,
        tenant_id: UUID,
        code: str,
        name: str,
        normalized_name: str,
        short_name: str,
        tax_identifier: str,
        address: str,
        phone: str,
        bank_name: str,
        bank_account: str,
        bank_routing_number: str,
    ) -> CreatedMasterData: ...

    def create_supplier(
        self,
        *,
        tenant_id: UUID,
        code: str,
        name: str,
        normalized_name: str,
        short_name: str,
        contact_person: str,
        phone: str,
        address: str,
        tax_identifier: str,
        bank_routing_number: str,
        bank_name: str,
        bank_account: str,
        service_description: str,
        english_name: str,
        english_address: str,
    ) -> CreatedMasterData: ...

    def create_unit(
        self, *, tenant_id: UUID, code: str, name: str, normalized_name: str
    ) -> CreatedMasterData: ...

    def create_category(
        self, *, tenant_id: UUID, code: str, name: str, normalized_name: str
    ) -> CreatedMasterData: ...

    def create_material(
        self,
        *,
        tenant_id: UUID,
        code: str,
        name: str,
        normalized_name: str,
        specification: str,
        brand: str,
        unit_id: UUID,
        category_id: UUID,
        procurement_required: bool,
    ) -> CreatedMasterData: ...


@dataclass(frozen=True, slots=True)
class CreateMaterialCommand:
    """创建物料所需字段；单位和分类 ID 必须属于当前可信租户。"""

    code: str
    name: str
    unit_id: UUID
    category_id: UUID
    specification: str = ""
    brand: str = ""
    procurement_required: bool = True


@dataclass(frozen=True, slots=True)
class CreateCustomerCommand:
    """创建客户的完整组织资料；银行字段不会进入普通审计摘要。"""

    code: str
    name: str
    short_name: str = ""
    tax_identifier: str = ""
    address: str = ""
    phone: str = ""
    bank_name: str = ""
    bank_account: str = ""
    bank_routing_number: str = ""


@dataclass(frozen=True, slots=True)
class CreateSupplierCommand:
    """创建供应商的完整档案；所有字段在应用层统一规范化。"""

    code: str
    name: str
    short_name: str = ""
    contact_person: str = ""
    phone: str = ""
    address: str = ""
    tax_identifier: str = ""
    bank_routing_number: str = ""
    bank_name: str = ""
    bank_account: str = ""
    service_description: str = ""
    english_name: str = ""
    english_address: str = ""


class MasterDataService:
    """执行主数据写入、授权和审计的应用服务。"""

    def __init__(
        self,
        *,
        repository: MasterDataRepository,
        grants: PermissionGrantLookup,
        audit: AuditRecorder,
        transactions: TransactionManager,
    ) -> None:
        self._repository = repository
        self._grants = grants
        self._audit = audit
        self._transactions = transactions

    def create_customer(
        self,
        *,
        context: TenantContext,
        code: str | None = None,
        name: str | None = None,
        command: CreateCustomerCommand | None = None,
    ) -> CreatedMasterData:
        """创建租户客户并追加审计；相同代码或规范化名称必须明确失败。"""
        if command is None:
            if code is None or name is None:
                raise ValueError("客户代码和名称不能为空。")
            command = CreateCustomerCommand(code=code, name=name)
        return self._create_organization(
            context=context,
            permission=PermissionCode.CUSTOMER_MANAGE,
            object_type="customer",
            action="customer.created",
            command=command,
            create=self._repository.create_customer,
            field_limits={
                "short_name": (100, "客户简称"),
                "tax_identifier": (64, "客户税号"),
                "address": (300, "客户地址"),
                "phone": (64, "客户电话"),
                "bank_name": (200, "客户开户行"),
                "bank_account": (64, "客户银行账号"),
                "bank_routing_number": (64, "客户银行行号"),
            },
        )

    def create_supplier(
        self, *, context: TenantContext, command: CreateSupplierCommand
    ) -> CreatedMasterData:
        """创建供应商，并只在审计摘要中保留非敏感稳定代码。"""
        return self._create_organization(
            context=context,
            permission=PermissionCode.SUPPLIER_MANAGE,
            object_type="supplier",
            action="supplier.created",
            command=command,
            create=self._repository.create_supplier,
            field_limits={
                "short_name": (100, "供应商简称"),
                "contact_person": (100, "联系人"),
                "phone": (64, "联系电话"),
                "address": (300, "供应商地址"),
                "tax_identifier": (64, "供应商税号"),
                "bank_routing_number": (64, "银行行号"),
                "bank_name": (200, "开户银行"),
                "bank_account": (64, "银行账号"),
                "service_description": (200, "服务说明"),
                "english_name": (200, "英文名称"),
                "english_address": (300, "英文地址"),
            },
        )

    def create_unit(self, *, context: TenantContext, code: str, name: str) -> CreatedMasterData:
        """创建计量单位；单位代码只表达身份，不在本切片执行换算。"""
        return self._create_simple(
            context=context,
            permission=PermissionCode.MATERIAL_MANAGE,
            object_type="unit",
            action="unit.created",
            code=code,
            name=name,
            create=self._repository.create_unit,
        )

    def create_category(self, *, context: TenantContext, code: str, name: str) -> CreatedMasterData:
        """创建物料分类；可请购规则不根据分类中文名称推断。"""
        return self._create_simple(
            context=context,
            permission=PermissionCode.MATERIAL_MANAGE,
            object_type="material_category",
            action="material_category.created",
            code=code,
            name=name,
            create=self._repository.create_category,
        )

    def create_material(
        self, *, context: TenantContext, command: CreateMaterialCommand
    ) -> CreatedMasterData:
        """创建带明确单位、分类和可请购属性的物料。"""
        authorize(
            context=context,
            resource_tenant_id=context.tenant_id,
            permission=PermissionCode.MATERIAL_MANAGE,
            is_related=True,
            lookup=self._grants,
        )
        code = normalize_code(command.code, field_name="物料编码")
        name, normalized_name = normalize_name(command.name, field_name="物料名称")
        specification = normalize_optional_text(
            command.specification, maximum_length=200, field_name="规格型号"
        )
        brand = normalize_optional_text(command.brand, maximum_length=100, field_name="品牌")
        with self._transactions.atomic():
            created = self._repository.create_material(
                tenant_id=context.tenant_id,
                code=code,
                name=name,
                normalized_name=normalized_name,
                specification=specification,
                brand=brand,
                unit_id=command.unit_id,
                category_id=command.category_id,
                procurement_required=command.procurement_required,
            )
            self._record_created(context=context, action="material.created", created=created)
        return created

    def _create_simple(
        self,
        *,
        context: TenantContext,
        permission: PermissionCode,
        object_type: str,
        action: str,
        code: str,
        name: str,
        create: Callable[..., CreatedMasterData],
    ) -> CreatedMasterData:
        """集中简单主数据的共同边界，避免四套授权和审计规则漂移。"""
        authorize(
            context=context,
            resource_tenant_id=context.tenant_id,
            permission=permission,
            is_related=True,
            lookup=self._grants,
        )
        normalized_code = normalize_code(code, field_name="代码")
        display_name, normalized_name = normalize_name(name, field_name="名称")
        with self._transactions.atomic():
            created = create(
                tenant_id=context.tenant_id,
                code=normalized_code,
                name=display_name,
                normalized_name=normalized_name,
            )
            self._record_created(
                context=context,
                action=action,
                created=created,
                object_type=object_type,
            )
        return created

    def _create_organization(
        self,
        *,
        context: TenantContext,
        permission: PermissionCode,
        object_type: str,
        action: str,
        command: CreateCustomerCommand | CreateSupplierCommand,
        create: Callable[..., CreatedMasterData],
        field_limits: dict[str, tuple[int, str]],
    ) -> CreatedMasterData:
        """执行组织主数据的共同授权、规范化、事务与最小审计边界。"""
        authorize(
            context=context,
            resource_tenant_id=context.tenant_id,
            permission=permission,
            is_related=True,
            lookup=self._grants,
        )
        code = normalize_code(command.code, field_name="代码")
        name, normalized_name = normalize_name(command.name, field_name="名称")
        optional_fields = {
            field: normalize_optional_text(
                str(getattr(command, field)), maximum_length=limit, field_name=label
            )
            for field, (limit, label) in field_limits.items()
        }
        with self._transactions.atomic():
            created = create(
                tenant_id=context.tenant_id,
                code=code,
                name=name,
                normalized_name=normalized_name,
                **optional_fields,
            )
            self._record_created(
                context=context,
                action=action,
                created=created,
                object_type=object_type,
            )
        return created

    def _record_created(
        self,
        *,
        context: TenantContext,
        action: str,
        created: CreatedMasterData,
        object_type: str = "material",
    ) -> None:
        """追加不含敏感字段的最小创建审计。"""
        self._audit.record(
            AuditEvent(
                tenant_id=context.tenant_id,
                actor_id=context.user_id,
                membership_id=context.membership_id,
                action=action,
                object_type=object_type,
                object_id=str(created.id),
                result=AuditResult.SUCCESS,
                summary={"code": created.code},
            )
        )
