"""主数据应用端口的 Django ORM 实现。"""

from contextlib import AbstractContextManager
from uuid import UUID

from django.db import IntegrityError, transaction

from pms.master_data.application.service import CreatedMasterData
from pms.master_data.domain.values import DuplicateMasterDataError
from pms.master_data.infrastructure.django.models import (
    Customer,
    Material,
    MaterialCategory,
    Unit,
)


class DjangoTransactionManager:
    """把 Django 原子事务适配为应用层事务端口。"""

    def atomic(self) -> AbstractContextManager[None]:
        return transaction.atomic()


class DjangoMasterDataRepository:
    """所有查询都显式带 tenant，跨租户外键按不存在处理。"""

    def create_customer(
        self, *, tenant_id: UUID, code: str, name: str, normalized_name: str
    ) -> CreatedMasterData:
        return self._create_simple(
            Customer,
            tenant_id=tenant_id,
            code=code,
            name=name,
            normalized_name=normalized_name,
        )

    def create_unit(
        self, *, tenant_id: UUID, code: str, name: str, normalized_name: str
    ) -> CreatedMasterData:
        return self._create_simple(
            Unit,
            tenant_id=tenant_id,
            code=code,
            name=name,
            normalized_name=normalized_name,
        )

    def create_category(
        self, *, tenant_id: UUID, code: str, name: str, normalized_name: str
    ) -> CreatedMasterData:
        return self._create_simple(
            MaterialCategory,
            tenant_id=tenant_id,
            code=code,
            name=name,
            normalized_name=normalized_name,
        )

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
    ) -> CreatedMasterData:
        # 同时限定两个外键的 tenant，避免攻击者把另一租户的 UUID 关联到
        # 当前租户物料。对外统一成字段无效，不泄露该 UUID 是否真实存在。
        unit = Unit.objects.filter(id=unit_id, tenant_id=tenant_id, is_active=True).first()
        category = MaterialCategory.objects.filter(
            id=category_id, tenant_id=tenant_id, is_active=True
        ).first()
        if unit is None or category is None:
            raise ValueError("单位或分类不可用。")
        try:
            material = Material.objects.create(
                tenant_id=tenant_id,
                code=code,
                name=name,
                normalized_name=normalized_name,
                specification=specification,
                brand=brand,
                unit=unit,
                category=category,
                procurement_required=procurement_required,
            )
        except IntegrityError as error:
            raise DuplicateMasterDataError("当前租户已存在相同物料编码。") from error
        return CreatedMasterData(id=material.id, code=material.code, name=material.name)

    @staticmethod
    def _create_simple(
        model: type[Customer] | type[Unit] | type[MaterialCategory],
        *,
        tenant_id: UUID,
        code: str,
        name: str,
        normalized_name: str,
    ) -> CreatedMasterData:
        try:
            row = model.objects.create(
                tenant_id=tenant_id,
                code=code,
                name=name,
                normalized_name=normalized_name,
            )
        except IntegrityError as error:
            raise DuplicateMasterDataError("当前租户已存在相同代码或名称。") from error
        return CreatedMasterData(id=row.id, code=row.code, name=row.name)
