"""组合正式业务应用服务与当前基础设施适配器。

Web 页面、离线迁移命令和后续受控任务共享这一最外层组合根。领域与应用
层因此不读取 Django settings，也不依赖 ORM 或本地文件系统。
"""

from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from pms.attachments.application.service import AttachmentService
from pms.attachments.infrastructure.django.repository import DjangoAttachmentRepository
from pms.attachments.infrastructure.local_storage import LocalBinaryStorage
from pms.audit.infrastructure.django.recorder import DjangoAuditRecorder
from pms.authorization.infrastructure.django.grant_lookup import DjangoPermissionGrantLookup
from pms.bom.application.service import BomService
from pms.bom.infrastructure.django.repository import (
    DjangoBomProjectDownstreamLookup,
    DjangoBomRepository,
    DjangoBomTransactionManager,
)
from pms.bom.infrastructure.spreadsheet import OpenPyxlBomSpreadsheetParser
from pms.master_data.application.drawings import DrawingService
from pms.master_data.application.service import MasterDataService
from pms.master_data.infrastructure.django.drawing_repository import DjangoDrawingRepository
from pms.master_data.infrastructure.django.repository import (
    DjangoMasterDataRepository,
    DjangoTransactionManager,
)
from pms.procurement.application.documents import OrderDocumentService
from pms.procurement.application.orders import PurchaseOrderService
from pms.procurement.application.pricing import PricingService
from pms.procurement.application.service import ProcurementService
from pms.procurement.infrastructure.django.document_repository import DjangoOrderDocumentRepository
from pms.procurement.infrastructure.django.order_repository import (
    DjangoPurchaseOrderRepository,
    DjangoPurchaseOrderTransactionManager,
)
from pms.procurement.infrastructure.django.pricing_repository import (
    DjangoPricingRepository,
    DjangoPricingTransactionManager,
)
from pms.procurement.infrastructure.django.repository import (
    DjangoProcurementProductionDownstreamLookup,
    DjangoProcurementRepository,
    DjangoProcurementTransactionManager,
)
from pms.procurement.infrastructure.spreadsheet import render_order_xlsx
from pms.production.application.service import ProductionService
from pms.production.infrastructure.django.repository import (
    DjangoBomProductionDownstreamLookup,
    DjangoProductionRepository,
    DjangoProductionTransactionManager,
)
from pms.projects.application.service import ProjectService
from pms.projects.infrastructure.django.repository import (
    DjangoProjectRepository,
    DjangoProjectTransactionManager,
)


def attachment_service() -> AttachmentService:
    """建立当前部署档案的附件服务。"""
    root_setting = getattr(settings, "ATTACHMENT_STORAGE_ROOT", None)
    if not isinstance(root_setting, (str, Path)):
        raise ImproperlyConfigured("当前部署档案缺少 ATTACHMENT_STORAGE_ROOT。")
    root = Path(root_setting)
    return AttachmentService(
        repository=DjangoAttachmentRepository(),
        storage=LocalBinaryStorage(root),
    )


def master_data_service() -> MasterDataService:
    return MasterDataService(
        repository=DjangoMasterDataRepository(),
        grants=DjangoPermissionGrantLookup(),
        audit=DjangoAuditRecorder(),
        transactions=DjangoTransactionManager(),
    )


def drawing_service() -> DrawingService:
    """建立物料图纸版本服务并复用主数据事务和附件存储。"""
    return DrawingService(
        repository=DjangoDrawingRepository(),
        attachments=attachment_service(),
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


def bom_service() -> BomService:
    return BomService(
        repository=DjangoBomRepository(),
        parser=OpenPyxlBomSpreadsheetParser(),
        attachments=attachment_service(),
        grants=DjangoPermissionGrantLookup(),
        audit=DjangoAuditRecorder(),
        transactions=DjangoBomTransactionManager(),
        downstream=DjangoBomProductionDownstreamLookup(),
    )


def production_service() -> ProductionService:
    return ProductionService(
        repository=DjangoProductionRepository(),
        grants=DjangoPermissionGrantLookup(),
        audit=DjangoAuditRecorder(),
        transactions=DjangoProductionTransactionManager(),
        downstream=DjangoProcurementProductionDownstreamLookup(),
    )


def procurement_service() -> ProcurementService:
    return ProcurementService(
        repository=DjangoProcurementRepository(),
        grants=DjangoPermissionGrantLookup(),
        audit=DjangoAuditRecorder(),
        transactions=DjangoProcurementTransactionManager(),
    )


def pricing_service() -> PricingService:
    """建立采购报价和供应商确定服务。"""
    return PricingService(
        repository=DjangoPricingRepository(),
        grants=DjangoPermissionGrantLookup(),
        audit=DjangoAuditRecorder(),
        transactions=DjangoPricingTransactionManager(),
    )


def purchase_order_service() -> PurchaseOrderService:
    """建立正式订单生命周期服务。"""
    return PurchaseOrderService(
        repository=DjangoPurchaseOrderRepository(),
        grants=DjangoPermissionGrantLookup(),
        audit=DjangoAuditRecorder(),
        transactions=DjangoPurchaseOrderTransactionManager(),
    )


def order_document_service() -> OrderDocumentService:
    """建立版本化订单 Excel 服务并复用受控附件存储。"""
    return OrderDocumentService(
        repository=DjangoOrderDocumentRepository(),
        renderer=render_order_xlsx,
        attachments=attachment_service(),
        grants=DjangoPermissionGrantLookup(),
        audit=DjangoAuditRecorder(),
        transactions=DjangoPurchaseOrderTransactionManager(),
    )
