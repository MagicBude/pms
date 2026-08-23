"""显式初始化 PMS 首次安装数据。"""

import os
from typing import Any, cast

from django.core.management.base import BaseCommand, CommandError, CommandParser

from pms.platform.bootstrap import InitializationError, initialize_installation

# 常量保存的是环境变量名称而不是密码；真实值只在命令进程内读取且从不输出。
INITIAL_ADMIN_PASSWORD_ENV = "PMS_INITIAL_ADMIN_PASSWORD"  # noqa: S105


class Command(BaseCommand):
    """创建默认租户、管理员和已接受的权限角色矩阵。"""

    help = "幂等初始化 PMS 默认租户、管理员、权限和角色；不会重置已有密码。"

    def add_arguments(self, parser: CommandParser) -> None:
        """允许部署者显式覆盖非秘密标识，秘密只从环境读取。"""
        parser.add_argument("--tenant-code", default="local")
        parser.add_argument("--tenant-name", default="本机租户")
        parser.add_argument("--admin-username", default="admin")

    def handle(self, *args: Any, **options: Any) -> None:
        """执行原子初始化，并输出不含秘密和内部主键的新增计数。"""
        del args
        try:
            result = initialize_installation(
                tenant_code=cast(str, options["tenant_code"]),
                tenant_name=cast(str, options["tenant_name"]),
                admin_username=cast(str, options["admin_username"]),
                initial_password=os.environ.get(INITIAL_ADMIN_PASSWORD_ENV),
            )
        except InitializationError as error:
            raise CommandError(str(error)) from error

        self.stdout.write(
            self.style.SUCCESS(
                "初始化完成："
                f"租户新增 {result.tenant_created}，"
                f"管理员新增 {result.admin_created}，"
                f"成员新增 {result.membership_created}，"
                f"权限新增 {result.permissions_created}，"
                f"角色新增 {result.roles_created}，"
                f"授权新增 {result.role_grants_created}，"
                f"管理员角色新增 {result.admin_role_created}。"
            )
        )
