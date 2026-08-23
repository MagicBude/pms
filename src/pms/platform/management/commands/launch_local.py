"""使用正式 Uvicorn 单实例启动本机 PMS。"""

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from pms.platform.local_launcher import (
    LocalLauncherError,
    configuration_from_settings,
    launch_local_server,
)


class Command(BaseCommand):
    """启动前验证迁移与初始化，ready 后自动打开一次默认浏览器。"""

    help = "以 local 单实例启动 Uvicorn，通过 ready 后打开默认浏览器。"

    def add_arguments(self, parser: CommandParser) -> None:
        """无图形 CI 可以关闭浏览器，但不能绕过其他正式启动检查。"""
        parser.add_argument(
            "--no-browser",
            action="store_true",
            help="服务就绪后不自动打开浏览器；用于 CI 或无图形环境。",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """运行阻塞服务；用户按 Ctrl+C 后正常返回管理命令。"""
        del args
        try:
            configuration = configuration_from_settings()
            self.stdout.write(f"正在启动本机 PMS：{configuration.base_url}")
            launch_local_server(
                configuration,
                open_browser=not bool(options["no_browser"]),
                notifier=self.stdout.write,
            )
        except LocalLauncherError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(self.style.SUCCESS("本机 PMS 已停止。"))
