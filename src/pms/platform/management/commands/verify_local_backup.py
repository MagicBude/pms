"""离线验证本机 PMS 备份集。"""

from pathlib import Path
from typing import Any, cast

from django.core.management.base import BaseCommand, CommandError, CommandParser

from pms.platform.local_backup import LocalBackupError, verify_local_backup


class Command(BaseCommand):
    """验证清单、SQLite、迁移、记录计数和全部正式附件。"""

    help = "验证本机备份集适用于当前应用版本且所有摘要一致。"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--backup-set", required=True)

    def handle(self, *args: Any, **options: Any) -> None:
        del args
        try:
            manifest = verify_local_backup(Path(cast(str, options["backup_set"])))
        except LocalBackupError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(
            self.style.SUCCESS(
                f"备份验证通过：{manifest.backup_id}（附件 {len(manifest.attachments)} 个）。"
            )
        )
