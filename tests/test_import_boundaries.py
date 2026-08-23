"""模块依赖方向的轻量静态守卫。

领域层尚未创建，但门槛必须先于业务代码落地。测试扫描未来所有
``domain`` 目录，拒绝 Django、ORM、HTTP 和 PMS 基础设施依赖。
"""

import ast
from pathlib import Path

import pytest

FORBIDDEN_ROOTS = {"django", "psycopg", "uvicorn"}
SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"


def imported_module(node: ast.Import | ast.ImportFrom) -> str:
    """返回 import 语句的模块名。"""
    if isinstance(node, ast.Import):
        return node.names[0].name
    return node.module or ""


@pytest.mark.unit
def test_domain_modules_do_not_import_framework_or_infrastructure() -> None:
    """领域规则必须保持为可脱离框架运行的纯 Python。"""
    violations: list[str] = []
    for source_file in SOURCE_ROOT.rglob("domain/**/*.py"):
        tree = ast.parse(source_file.read_text(encoding="utf-8"), filename=str(source_file))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Import | ast.ImportFrom):
                continue
            module = imported_module(node)
            root = module.partition(".")[0]
            if root in FORBIDDEN_ROOTS or (root == "pms" and ".infrastructure" in module):
                relative_path = source_file.relative_to(SOURCE_ROOT.parent)
                violations.append(f"{relative_path}:{node.lineno} imports {module}")

    assert violations == [], "领域层存在禁止依赖：\n" + "\n".join(violations)
