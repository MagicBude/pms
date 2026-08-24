"""生成一条待业务复核的真实项目/BOM/投产/请购案例。"""

from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from pms.legacy_migration.slice_mapping import (
    LegacySliceMappingError,
    map_pending_real_slice,
    write_pending_slice_outputs,
)


class Command(BaseCommand):
    help = "从原始提取包生成 business_pending 的 pms-legacy-slice-v2 和本机 HTML 复核页。"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--raw", type=Path, required=True)
        parser.add_argument("--output", type=Path, required=True)
        parser.add_argument("--review", type=Path, required=True)

    def handle(self, *args: Any, **options: Any) -> None:
        del args
        try:
            result = map_pending_real_slice(options["raw"])
            write_pending_slice_outputs(
                result,
                package_path=options["output"],
                review_path=options["review"],
            )
        except LegacySliceMappingError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(
            self.style.SUCCESS(
                "真实待复核案例已生成："
                f"合格候选 {result.eligible_candidate_count}，选定明细 {result.mapped_line_count} 行。"
            )
        )
        self.stdout.write("输出包含真实业务资料，只能在本机受控环境复核，不得提交 Git。")
