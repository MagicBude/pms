"""把已验证本机备份恢复到新的空数据目录。"""

from pathlib import Path
from typing import Any, cast

from django.core.management.base import BaseCommand, CommandError, CommandParser

from pms.platform.local_backup import LocalBackupError, restore_local_backup


class Command(BaseCommand):
    """恢复到显式空目标，不覆盖当前或任何非空数据目录。"""

    help = "验证本机备份并原子恢复到不存在或明确为空的新数据目录。"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--backup-set", required=True)
        parser.add_argument("--target-data-dir", required=True)

    def handle(self, *args: Any, **options: Any) -> None:
        del args
        try:
            result = restore_local_backup(
                backup_set=Path(cast(str, options["backup_set"])),
                target_data_dir=Path(cast(str, options["target_data_dir"])),
            )
        except LocalBackupError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(
            self.style.SUCCESS(
                f"恢复完成：{result.target_data_dir}（附件 {result.attachment_count} 个）。"
            )
        )
