"""把旧 PMS 原始提取包映射成客户与供应商规范包。"""

from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from pms.legacy_migration.master_data_package import (
    LegacyMasterDataPackageError,
    map_legacy_master_data,
    write_legacy_master_data_package,
)


class Command(BaseCommand):
    help = "验证原始包并生成 pms-legacy-master-data-v1；不写数据库。"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--raw", type=Path, required=True, help="原始提取包目录")
        parser.add_argument("--output", type=Path, required=True, help="尚不存在的规范 JSON 文件")

    def handle(self, *args: Any, **options: Any) -> None:
        del args
        try:
            package = map_legacy_master_data(options["raw"])
            write_legacy_master_data_package(package, options["output"])
        except LegacyMasterDataPackageError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(
            self.style.SUCCESS(
                f"主数据映射完成：客户 {len(package.customers)}，供应商 {len(package.suppliers)}。"
            )
        )
        self.stdout.write("输出包含真实业务敏感数据，请勿提交 Git 或通过不受控渠道传输。")
