"""从受控脱敏 JSON 包迁移首切片并输出逐项对账报告。"""

from pathlib import Path
from typing import Any, cast

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import connection

from pms.legacy_migration.reconciliation import write_reconciliation_report
from pms.legacy_migration.schema import LegacyPackageError, load_legacy_slice_package
from pms.legacy_migration.service import LegacyImportConflictError, LegacySliceMigrationService


class Command(BaseCommand):
    help = "迁移受控 SLICE-001 脱敏包并生成结构化对账报告。"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--input", required=True, help="pms-legacy-slice-v1/v2 JSON 文件")
        parser.add_argument("--report", required=True, help="待独占创建的对账 JSON 文件")
        parser.add_argument("--actor-username", default="admin")
        parser.add_argument(
            "--allow-business-pending",
            action="store_true",
            help="仅用于隔离复核库；允许导入尚未由业务人员签收的真实案例。",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        del args
        if (
            getattr(settings, "DEPLOYMENT_PROFILE", None) != "local"
            or connection.vendor != "sqlite"
        ):
            raise CommandError("Phase 2 迁移命令只支持停止服务后的 local + SQLite 维护窗口。")
        input_path = Path(cast(str, options["input"]))
        report_path = Path(cast(str, options["report"]))
        actor_username = cast(str, options["actor_username"])
        try:
            package = load_legacy_slice_package(input_path)
            if package.sample.kind == "business_pending" and not options["allow_business_pending"]:
                raise LegacyPackageError(
                    "真实待确认案例只能在隔离复核库使用；需显式传入 --allow-business-pending。"
                )
            if package.sample.kind == "business_pending":
                data_dir = getattr(settings, "DATA_DIR", None)
                formal_data_dir = (settings.BASE_DIR / "data").resolve()
                if data_dir is None or Path(data_dir).resolve() == formal_data_dir:
                    raise LegacyPackageError(
                        "真实待确认案例必须设置独立 PMS_DATA_DIR，禁止写入正式 data 目录。"
                    )
            report = LegacySliceMigrationService().migrate(
                package=package, actor_username=actor_username
            )
            write_reconciliation_report(report, report_path)
        except (
            LegacyPackageError,
            LegacyImportConflictError,
            ValueError,
            LookupError,
            PermissionError,
        ) as error:
            raise CommandError(str(error)) from error
        if report.overall_status == "DIFFERENCES_PENDING":
            raise CommandError("迁移完成，但仍有未签收差异；请审阅已生成的对账报告。")
        scope = {
            "BUSINESS_CONFIRMED": "业务确认",
            "BUSINESS_PENDING": "待业务复核",
            "TECHNICAL_ONLY": "仅技术验证",
        }[report.acceptance_scope]
        self.stdout.write(
            self.style.SUCCESS(
                f"迁移与对账完成：{report.overall_status}（{scope}），报告 {report_path.name}"
            )
        )
