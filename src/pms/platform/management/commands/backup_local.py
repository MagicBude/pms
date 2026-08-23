"""创建经过完整性验证的本机 PMS 备份集。"""

from pathlib import Path
from typing import Any, cast

from django.core.management.base import BaseCommand, CommandError, CommandParser

from pms.platform.local_backup import LocalBackupError, create_local_backup


class Command(BaseCommand):
    """把 local SQLite、正式附件和版本清单备份到用户选择的目录。"""

    help = "创建本机 SQLite、附件和版本清单组成的已验证备份集。"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--destination", required=True)

    def handle(self, *args: Any, **options: Any) -> None:
        del args
        try:
            result = create_local_backup(Path(cast(str, options["destination"])))
        except LocalBackupError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(
            self.style.SUCCESS(
                f"备份完成：{result.backup_set}（附件 {result.attachment_count} 个）。"
            )
        )
