"""订单导出快照和文档版本关系的 ORM 适配器。"""

from datetime import datetime
from decimal import Decimal
from typing import cast
from uuid import UUID

from django.db.models import Max, Sum

from pms.procurement.application.documents import OrderDocumentSnapshot
from pms.procurement.infrastructure.django.models import (
    PurchaseOrder,
    PurchaseOrderDocument,
)
from pms.procurement.infrastructure.spreadsheet import OrderExportData, OrderExportLine


class DjangoOrderDocumentRepository:
    """锁定订单分配版本，确保并发生成不会覆盖同一版本。"""

    def export_access(
        self, *, tenant_id: UUID, order_id: UUID, membership_id: UUID
    ) -> tuple[OrderExportData, bool, int] | None:
        order = (
            PurchaseOrder.objects.select_for_update()
            .filter(id=order_id, tenant_id=tenant_id)
            .first()
        )
        if order is None:
            return None
        lines = list(order.lines.all())
        totals = order.lines.aggregate(
            net=Sum("net_amount"), tax=Sum("tax_amount"), gross=Sum("gross_amount")
        )
        version = (order.documents.aggregate(value=Max("version"))["value"] or 0) + 1
        is_related = order.lines.filter(
            request_line__purchase_request__project__owner_membership_id=membership_id
        ).exists()
        data = OrderExportData(
            order_number=order.order_number or "",
            status=order.status,
            kind=order.kind,
            supplier_name=order.supplier_name_snapshot,
            currency=order.currency,
            issued_at=cast(datetime, order.issued_at),
            lines=tuple(
                OrderExportLine(
                    project_code=line.project_code_snapshot,
                    request_number=line.request_number_snapshot,
                    material_code=line.material_code_snapshot,
                    material_name=line.material_name_snapshot,
                    part_attribute=line.part_attribute_snapshot,
                    unit=line.unit_name_snapshot,
                    quantity=line.quantity,
                    unit_price=line.unit_price,
                    tax_rate=line.tax_rate,
                    net_amount=line.net_amount,
                    tax_amount=line.tax_amount,
                    gross_amount=line.gross_amount,
                    remark=line.remark_snapshot,
                )
                for line in lines
            ),
            net_amount=totals["net"] or Decimal("0.00"),
            tax_amount=totals["tax"] or Decimal("0.00"),
            gross_amount=totals["gross"] or Decimal("0.00"),
        )
        return data, is_related, version

    def link_document(
        self,
        *,
        tenant_id: UUID,
        order_id: UUID,
        attachment_id: UUID,
        membership_id: UUID,
        version: int,
    ) -> OrderDocumentSnapshot:
        row = PurchaseOrderDocument.objects.create(
            tenant_id=tenant_id,
            order_id=order_id,
            attachment_id=attachment_id,
            created_by_membership_id=membership_id,
            version=version,
        )
        return OrderDocumentSnapshot(
            id=row.id,
            order_id=row.order_id,
            attachment_id=row.attachment_id,
            version=row.version,
            filename=row.attachment.original_filename,
        )
