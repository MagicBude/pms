"""生成只读、版本化且不进入 Git 的旧 PMS 原始数据包。"""

from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from pms.legacy_migration.raw_extraction import (
    LegacyRawExtractionError,
    extract_legacy_raw_package,
)


class Command(BaseCommand):
    """把旧核心数据库提取为后续映射和正式导入使用的 JSONL 包。"""

    help = "只读提取旧 PMS 核心工作簿，不执行宏、不写数据库、不输出业务值。"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--legacy-root",
            type=Path,
            required=True,
            help="旧 PMS 根目录，例如 .internal/legacy-pms。",
        )
        parser.add_argument(
            "--output",
            type=Path,
            required=True,
            help="尚不存在的受控输出目录；其中会包含真实业务数据。",
        )
        parser.add_argument(
            "--include-restricted",
            action="store_true",
            help="显式允许提取客户账户、财务、员工等高敏感数据。",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            extracted = extract_legacy_raw_package(
                legacy_root=options["legacy_root"],
                output_directory=options["output"],
                include_restricted=options["include_restricted"],
            )
        except LegacyRawExtractionError as error:
            raise CommandError(str(error)) from error
        total_records = sum(item.record_count for item in extracted)
        self.stdout.write(
            self.style.SUCCESS(
                f"旧数据只读提取完成：{len(extracted)} 个数据集，{total_records} 条记录。"
            )
        )
        self.stdout.write("输出包含真实业务数据，请勿提交 Git 或通过不受控渠道传输。")
