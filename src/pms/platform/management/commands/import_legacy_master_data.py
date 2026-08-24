"""在本机维护窗口导入客户与供应商规范包并输出对账摘要。"""

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import connection

from pms.legacy_migration.master_data_import import (
    LegacyMasterDataImportConflictError,
    import_legacy_master_data,
)
from pms.legacy_migration.master_data_package import (
    LegacyMasterDataPackageError,
    load_legacy_master_data_package,
)


class Command(BaseCommand):
    help = "通过正式应用用例幂等导入 pms-legacy-master-data-v1。"

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
            raise CommandError("当前导入命令只支持停止服务后的 local + SQLite 维护窗口。")
        report_path = cast(Path, options["report"])
        if report_path.suffix.lower() != ".json" or report_path.exists():
            raise CommandError("对账报告必须是尚不存在的 .json 文件。")
        try:
            package = load_legacy_master_data_package(cast(Path, options["input"]))
            report = import_legacy_master_data(
                package=package, actor_username=cast(str, options["actor_username"])
            )
            payload = {"schema_version": "pms-legacy-master-data-report-v1", **asdict(report)}
            report_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        except (
            LegacyMasterDataPackageError,
            LegacyMasterDataImportConflictError,
            OSError,
            ValueError,
            LookupError,
            PermissionError,
        ) as error:
            raise CommandError(str(error)) from error
        self.stdout.write(
            self.style.SUCCESS(
                "主数据导入与对账完成："
                f"客户 {report.customer_total}（新增 {report.customer_created}，复用 {report.customer_reused}），"
                f"供应商 {report.supplier_total}（新增 {report.supplier_created}，复用 {report.supplier_reused}）。"
            )
        )
