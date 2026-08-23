"""把应用端口组合为本机 Web 用例服务。

组合发生在系统最外层，领域和应用层因此无需知道 Django settings、ORM
仓储或本地附件路径。未来切换内网/云端存储时只替换这里的适配器。
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
from pms.master_data.application.service import MasterDataService
from pms.master_data.infrastructure.django.repository import (
    DjangoMasterDataRepository,
    DjangoTransactionManager,
)
from pms.procurement.application.service import ProcurementService
from pms.procurement.infrastructure.django.repository import (
    DjangoProcurementProductionDownstreamLookup,
    DjangoProcurementRepository,
    DjangoProcurementTransactionManager,
)
from pms.production.application.service import ProductionService
from pms.production.infrastructure.django.repository import (
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
