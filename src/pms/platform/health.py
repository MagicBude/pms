"""数据库连接与迁移状态组成的就绪检查。"""

from dataclasses import dataclass

from django.db import connection
from django.db.migrations.executor import MigrationExecutor


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    """只包含可公开状态、不包含连接细节的就绪检查结果。"""

    ready: bool
    checks: dict[str, str]


def check_readiness() -> ReadinessReport:
    """检查数据库可访问且没有尚未应用的迁移。

    这里不返回异常消息、数据库地址或 SQL。附件存储将在 F-008 建立稳定
    端口后加入检查；在此之前，数据库是唯一运行时必要外部依赖。
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        executor = MigrationExecutor(connection)
        pending_migrations = executor.migration_plan(executor.loader.graph.leaf_nodes())
    # 健康边界必须把全部依赖失败收敛为安全状态，不能让诊断异常反过来击穿端点。
    except Exception:
        return ReadinessReport(
            ready=False,
            checks={"database": "unavailable", "migrations": "unknown"},
        )

    if pending_migrations:
        return ReadinessReport(
            ready=False,
            checks={"database": "ok", "migrations": "pending"},
        )
    return ReadinessReport(
        ready=True,
        checks={"database": "ok", "migrations": "ok"},
    )
