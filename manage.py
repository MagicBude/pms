#!/usr/bin/env python
"""PMS 的 Django 管理命令入口。"""

import os
import sys


def main() -> None:
    """使用显式选择的配置档案执行 Django 管理命令。"""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pms.settings.local")

    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
