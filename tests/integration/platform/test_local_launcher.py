"""F-011 本机启动前迁移、初始化和正式档案边界集成测试。"""

from pathlib import Path
from unittest.mock import patch

import pytest
from django.test import override_settings

from pms.platform.bootstrap import initialize_installation
from pms.platform.health import ReadinessReport
from pms.platform.local_launcher import (
    LocalLauncherConfiguration,
    LocalLauncherConfigurationError,
    LocalLauncherPreflightError,
    validate_local_runtime,
)

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.sqlite]


def build_configuration(tmp_path: Path) -> LocalLauncherConfiguration:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    return LocalLauncherConfiguration("127.0.0.1", 8000, data_dir, 30)


def test_preflight_rejects_uninitialized_then_accepts_initialized_installation(
    tmp_path: Path,
) -> None:
    configuration = build_configuration(tmp_path)
    attachment_root = configuration.data_dir / "attachments"
    attachment_root.mkdir()
    with override_settings(
        DEPLOYMENT_PROFILE="local",
        DEBUG=False,
        BIND_HOST=configuration.host,
        BIND_PORT=configuration.port,
        DATA_DIR=configuration.data_dir,
        ATTACHMENT_STORAGE_ROOT=attachment_root,
    ):
        with pytest.raises(LocalLauncherPreflightError, match="尚未完成首次初始化"):
            validate_local_runtime(configuration)

        initialize_installation(
            tenant_code="local",
            tenant_name="本机租户",
            admin_username="admin",
            initial_password="F011-test-only!5927",
        )

        validate_local_runtime(configuration)


def test_preflight_rejects_pending_migrations_with_actionable_command(tmp_path: Path) -> None:
    configuration = build_configuration(tmp_path)
    report = ReadinessReport(
        ready=False,
        checks={"database": "ok", "migrations": "pending"},
    )
    with (
        override_settings(
            DEPLOYMENT_PROFILE="local",
            DEBUG=False,
            BIND_HOST=configuration.host,
            BIND_PORT=configuration.port,
            DATA_DIR=configuration.data_dir,
        ),
        patch("pms.platform.local_launcher.check_readiness", return_value=report),
        pytest.raises(LocalLauncherPreflightError, match="migrate --noinput"),
    ):
        validate_local_runtime(configuration)


def test_preflight_rejects_debug_and_nonlocal_profiles(tmp_path: Path) -> None:
    configuration = build_configuration(tmp_path)
    with (
        override_settings(
            DEPLOYMENT_PROFILE="local",
            DEBUG=True,
            BIND_HOST=configuration.host,
            BIND_PORT=configuration.port,
            DATA_DIR=configuration.data_dir,
        ),
        pytest.raises(LocalLauncherConfigurationError, match="禁止 PMS_DEBUG"),
    ):
        validate_local_runtime(configuration)

    with (
        override_settings(DEPLOYMENT_PROFILE="lan", DEBUG=False),
        pytest.raises(LocalLauncherConfigurationError, match="只支持 local"),
    ):
        validate_local_runtime(configuration)
