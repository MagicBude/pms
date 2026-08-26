"""把旧采购订单原始包映射成版本化规范包。"""

from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from pms.legacy_migration.purchase_order_package import (
    LegacyPurchaseOrderPackageError,
    map_legacy_purchase_orders,
    write_legacy_purchase_order_package,
)


class Command(BaseCommand):
    help = "验证旧采购订单并生成 pms-legacy-purchase-orders-v1；不写数据库。"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--raw", type=Path, required=True, help="原始迁移包目录")
        parser.add_argument("--output", type=Path, required=True, help="尚不存在的规范 JSON 文件")

    def handle(self, *args: Any, **options: Any) -> None:
        del args
        try:
            package = map_legacy_purchase_orders(options["raw"])
            write_legacy_purchase_order_package(package, options["output"])
        except LegacyPurchaseOrderPackageError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(
            self.style.SUCCESS(
                "采购订单映射完成："
                f"明细 {package.source_record_count}，订单 {len(package.orders)}，"
                f"金额差异订单 {package.difference_order_count}。"
            )
        )
        self.stdout.write("输出包含真实供应商、价格和备注，请勿提交 Git 或通过不受控渠道传输。")
