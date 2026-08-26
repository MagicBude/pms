"""只读检查旧采购订单规范包能否安全进入正式导入阶段。"""

from pathlib import Path
from typing import Any, cast

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import connection

from pms.legacy_migration.purchase_order_package import (
    LegacyPurchaseOrderPackageError,
    load_legacy_purchase_order_package,
)
from pms.legacy_migration.purchase_order_preflight import (
    LegacyPurchaseOrderPreflightError,
    preflight_legacy_purchase_orders,
    write_purchase_order_preflight_report,
)


class Command(BaseCommand):
    help = "只读预检 pms-legacy-purchase-orders-v1 的全部正式引用。"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--input", type=Path, required=True)
        parser.add_argument("--report", type=Path, required=True)
        parser.add_argument("--actor-username", default="admin")

    def handle(self, *args: Any, **options: Any) -> None:
        del args
        if (
            getattr(settings, "DEPLOYMENT_PROFILE", None) != "local"
            or connection.vendor != "sqlite"
        ):
            raise CommandError("当前预检命令只支持 local + SQLite。")
        try:
            package = load_legacy_purchase_order_package(cast(Path, options["input"]))
            report = preflight_legacy_purchase_orders(
                package=package, actor_username=cast(str, options["actor_username"])
            )
            write_purchase_order_preflight_report(report, cast(Path, options["report"]))
        except (LegacyPurchaseOrderPackageError, LegacyPurchaseOrderPreflightError) as error:
            raise CommandError(str(error)) from error
        unresolved = sum(len(item.unresolved_source_rows) for item in report.references)
        ambiguous = sum(len(item.ambiguous_source_rows) for item in report.references)
        state = "可导入" if report.ready_for_import else "存在引用缺口，禁止导入"
        self.stdout.write(
            self.style.SUCCESS(
                f"采购订单预检完成：明细 {report.source_record_count}，"
                f"未解析引用 {unresolved}，歧义引用 {ambiguous}；{state}。"
            )
        )
