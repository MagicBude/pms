"""就绪检查必须安全收敛依赖状态。"""

from unittest.mock import patch

import pytest

from pms.platform.health import check_readiness


@pytest.mark.unit
def test_readiness_is_true_when_database_and_migrations_are_ready() -> None:
    with (
        patch("pms.platform.health.connection") as mocked_connection,
        patch("pms.platform.health.MigrationExecutor") as executor_class,
    ):
        executor = executor_class.return_value
        executor.loader.graph.leaf_nodes.return_value = ["leaf"]
        executor.migration_plan.return_value = []

        report = check_readiness()

    mocked_connection.cursor.return_value.__enter__.return_value.execute.assert_called_once_with(
        "SELECT 1"
    )
    assert report.ready is True
    assert report.checks == {"database": "ok", "migrations": "ok"}


@pytest.mark.unit
def test_readiness_reports_pending_migrations_without_internal_details() -> None:
    with (
        patch("pms.platform.health.connection"),
        patch("pms.platform.health.MigrationExecutor") as executor_class,
    ):
        executor = executor_class.return_value
        executor.loader.graph.leaf_nodes.return_value = ["leaf"]
        executor.migration_plan.return_value = [("migration", False)]

        report = check_readiness()

    assert report.ready is False
    assert report.checks == {"database": "ok", "migrations": "pending"}


@pytest.mark.unit
def test_readiness_hides_database_failure_details() -> None:
    with patch("pms.platform.health.connection") as mocked_connection:
        mocked_connection.cursor.side_effect = RuntimeError(
            "password=must-not-leak host=private-db.example"
        )

        report = check_readiness()

    assert report.ready is False
    assert report.checks == {"database": "unavailable", "migrations": "unknown"}
    assert "must-not-leak" not in str(report)
    assert "private-db.example" not in str(report)
