"""集中读取并验证部署环境变量。

配置解析属于平台边界。业务模块不得直接读取环境变量，否则同一条
业务规则会随进程环境产生隐式变化，也会让测试难以完整覆盖。
"""

import os
from collections.abc import Mapping
from pathlib import Path


class ConfigurationError(ValueError):
    """表示配置缺失、格式错误或组合不安全。"""


def read_bool(name: str, *, default: bool, environ: Mapping[str, str] = os.environ) -> bool:
    """读取严格布尔值，拒绝容易误解的拼写。"""
    raw_value = environ.get(name)
    if raw_value is None:
        return default

    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} 必须是 true/false、yes/no、on/off 或 1/0。")


def read_int(
    name: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
    environ: Mapping[str, str] = os.environ,
) -> int:
    """读取有闭区间约束的十进制整数，不接受布尔值或模糊格式。"""
    raw_value = environ.get(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value.strip(), base=10)
    except ValueError as error:
        raise ConfigurationError(f"{name} 必须是十进制整数。") from error
    if not minimum <= value <= maximum:
        raise ConfigurationError(f"{name} 必须在 {minimum} 至 {maximum} 之间。")
    return value


def require(name: str, *, environ: Mapping[str, str] = os.environ) -> str:
    """读取必填值，但不把可能的秘密写入异常消息。"""
    value = environ.get(name, "").strip()
    if not value:
        raise ConfigurationError(f"缺少必要配置：{name}。")
    return value


def read_csv(name: str, *, required: bool, environ: Mapping[str, str] = os.environ) -> list[str]:
    """把逗号分隔配置规范化为非空条目列表。"""
    raw_value = require(name, environ=environ) if required else environ.get(name, "")
    values = [item.strip() for item in raw_value.split(",") if item.strip()]
    if required and not values:
        raise ConfigurationError(f"{name} 至少需要一个值。")
    return values


def ensure_private_directory(path: Path) -> Path:
    """确保应用私有目录存在，并把系统错误转换成稳定配置错误。

    SQLite 可以创建数据库文件，却不会替它创建父目录。本机档案必须在
    Django 启动迁移检查以前完成这个最小初始化，否则干净环境第一次
    启动会得到难以理解的 ``unable to open database file``。
    """
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        # 错误中不回显绝对路径，避免服务器日志泄露本机目录结构。
        raise ConfigurationError("无法创建或访问 PMS 应用数据目录。") from error
    if not path.is_dir():
        raise ConfigurationError("PMS 应用数据目录配置指向的不是目录。")
    return path
