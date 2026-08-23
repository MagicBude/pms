"""就绪检查必须安全收敛依赖状态。"""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from django.test import override_settings

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


@pytest.mark.unit
def test_readiness_checks_configured_attachment_storage() -> None:
    with (
        tempfile.TemporaryDirectory() as temporary_root,
        override_settings(ATTACHMENT_STORAGE_ROOT=Path(temporary_root)),
        patch("pms.platform.health.connection"),
        patch("pms.platform.health.MigrationExecutor") as executor_class,
    ):
        executor_class.return_value.loader.graph.leaf_nodes.return_value = ["leaf"]
        executor_class.return_value.migration_plan.return_value = []

        report = check_readiness()

    assert report.ready is True
    assert report.checks["attachment_storage"] == "ok"


@pytest.mark.unit
def test_readiness_hides_attachment_storage_failure_path() -> None:
    with tempfile.TemporaryDirectory() as temporary_root:
        private_path = Path(temporary_root) / "private-storage-file"
        private_path.write_text("not a directory", encoding="utf-8")
        with (
            override_settings(ATTACHMENT_STORAGE_ROOT=private_path),
            patch("pms.platform.health.connection"),
            patch("pms.platform.health.MigrationExecutor") as executor_class,
        ):
            executor_class.return_value.loader.graph.leaf_nodes.return_value = ["leaf"]
            executor_class.return_value.migration_plan.return_value = []

            report = check_readiness()

    assert report.ready is False
    assert report.checks["attachment_storage"] == "unavailable"
    assert "private" not in str(report).lower()
