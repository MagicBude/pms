"""客户、物料、单位和分类共同使用的主数据值规则。

本模块只负责把外部文本转换为稳定业务值，不知道表单、数据库或租户。
租户内唯一性由应用仓储和数据库约束共同保证。
"""

import re
import unicodedata

CODE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class MasterDataValidationError(ValueError):
    """表示主数据字段在进入持久化前不满足稳定格式。"""


class DuplicateMasterDataError(ValueError):
    """表示同一租户已存在相同稳定代码或规范化名称。"""


def normalize_code(value: str, *, field_name: str, maximum_length: int = 64) -> str:
    """规范化人工代码并限制为可移植的 ASCII 标识。

    代码统一转为大写，使 ``mat-001`` 与 ``MAT-001`` 在 SQLite 和
    PostgreSQL 中具有相同业务含义。仅允许字母、数字、点、下划线和
    连字符，避免数据库排序规则差异改变唯一性结果。
    """
    normalized = unicodedata.normalize("NFKC", value).strip().upper()
    if not normalized or len(normalized) > maximum_length or not CODE_PATTERN.fullmatch(normalized):
        raise MasterDataValidationError(
            f"{field_name}必须为 1 至 {maximum_length} 个字母、数字、点、下划线或连字符。"
        )
    return normalized


def normalize_name(value: str, *, field_name: str, maximum_length: int = 200) -> tuple[str, str]:
    """返回用于显示的名称和用于租户内比较的规范化键。

    显示值保留大小写，但折叠首尾及连续空白；比较键再执行 Unicode
    NFKC 和 ``casefold``。数据库保存两者，是为了让名称去重不依赖具体
    数据库的大小写与 Unicode 排序规则。
    """
    display_name = " ".join(unicodedata.normalize("NFKC", value).split())
    if not display_name or len(display_name) > maximum_length:
        raise MasterDataValidationError(f"{field_name}必须为 1 至 {maximum_length} 个字符。")
    return display_name, display_name.casefold()


def normalize_optional_text(value: str, *, maximum_length: int, field_name: str) -> str:
    """清理可选说明字段，同时保留空字符串作为明确的“未提供”。"""
    normalized = " ".join(unicodedata.normalize("NFKC", value).split())
    if len(normalized) > maximum_length:
        raise MasterDataValidationError(f"{field_name}不能超过 {maximum_length} 个字符。")
    return normalized
