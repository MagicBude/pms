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
        parser.add_argument("--input", required=True, help="pms-legacy-slice-v1 JSON 文件")
        parser.add_argument("--report", required=True, help="待独占创建的对账 JSON 文件")
        parser.add_argument("--actor-username", default="admin")

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
        scope = "业务确认" if report.acceptance_scope == "BUSINESS_CONFIRMED" else "仅技术验证"
        self.stdout.write(
            self.style.SUCCESS(
                f"迁移与对账完成：{report.overall_status}（{scope}），报告 {report_path.name}"
            )
        )
