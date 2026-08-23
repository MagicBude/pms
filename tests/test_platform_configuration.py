"""F-002 配置边界和最小 HTTP 响应测试。"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pms.settings.test")

import django
from django.test import Client, SimpleTestCase, override_settings

from pms.settings.environment import (
    ConfigurationError,
    ensure_private_directory,
    read_bool,
    read_csv,
    require,
)

django.setup()


class EnvironmentParsingTests(unittest.TestCase):
    """配置错误必须在启动阶段给出确定结果。"""

    def test_read_bool_rejects_ambiguous_value(self) -> None:
        with self.assertRaises(ConfigurationError):
            read_bool("PMS_DEBUG", default=False, environ={"PMS_DEBUG": "sometimes"})

    def test_read_bool_accepts_explicit_values_and_default(self) -> None:
        self.assertTrue(read_bool("FLAG", default=False, environ={"FLAG": "YES"}))
        self.assertFalse(read_bool("FLAG", default=True, environ={"FLAG": "off"}))
        self.assertTrue(read_bool("FLAG", default=True, environ={}))

    def test_require_rejects_missing_value_without_echoing_a_secret(self) -> None:
        with self.assertRaisesRegex(ConfigurationError, "PMS_SECRET_KEY"):
            require("PMS_SECRET_KEY", environ={"PMS_SECRET_KEY": "  "})

    def test_require_strips_value(self) -> None:
        self.assertEqual(require("NAME", environ={"NAME": " pms "}), "pms")

    def test_read_csv_normalizes_values_and_validates_required_list(self) -> None:
        self.assertEqual(
            read_csv("HOSTS", required=True, environ={"HOSTS": "a.example, b.example, "}),
            ["a.example", "b.example"],
        )
        self.assertEqual(read_csv("HOSTS", required=False, environ={}), [])
        with self.assertRaises(ConfigurationError):
            read_csv("HOSTS", required=True, environ={"HOSTS": ", ,"})

    def test_ensure_private_directory_rejects_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            file_path = Path(temporary_root) / "not-a-directory"
            file_path.write_text("occupied", encoding="utf-8")

            with self.assertRaises(ConfigurationError):
                ensure_private_directory(file_path)


@override_settings(ROOT_URLCONF="pms.urls", ALLOWED_HOSTS=["testserver"])
class PlatformHttpTests(SimpleTestCase):
    """最小入口不得依赖数据库。"""

    def test_index_returns_engineering_status(self) -> None:
        response = Client().get("/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "业务界面将在 SLICE-001 中提供")


class ProfileStartupTests(unittest.TestCase):
    """在独立进程导入档案，避免模块缓存掩盖环境变量差异。"""

    project_root = Path(__file__).resolve().parents[1]

    def import_profile(
        self, profile: str, environment: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        process_environment = os.environ.copy()
        for name in tuple(process_environment):
            if name.startswith("PMS_"):
                del process_environment[name]
        process_environment.update(environment)
        return subprocess.run(
            [sys.executable, "-c", f"import pms.settings.{profile}"],
            cwd=self.project_root,
            env=process_environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_local_rejects_non_loopback_bind_host(self) -> None:
        result = self.import_profile("local", {"PMS_BIND_HOST": "0.0.0.0"})

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("必须是 loopback", result.stderr)

    def test_local_defaults_are_loopback_only_and_debug_off(self) -> None:
        assertions = """
from pms.settings import local
assert local.DEBUG is False
assert local.BIND_HOST == "127.0.0.1"
assert set(local.ALLOWED_HOSTS) == {"127.0.0.1", "localhost", "[::1]"}
assert local.SESSION_COOKIE_SECURE is False
assert local.CSRF_COOKIE_SECURE is False
"""
        result = subprocess.run(
            [sys.executable, "-c", assertions],
            cwd=self.project_root,
            env={name: value for name, value in os.environ.items() if not name.startswith("PMS_")},
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_local_creates_missing_data_directory_before_sqlite_connects(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            data_directory = Path(temporary_root) / "nested" / "data"
            environment = {
                "DJANGO_SETTINGS_MODULE": "pms.settings.local",
                "PMS_DATA_DIR": str(data_directory),
            }
            assertions = """
from pathlib import Path
import django
django.setup()
from django.db import connection
with connection.cursor() as cursor:
    cursor.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    assert cursor.fetchall() == []
assert Path(connection.settings_dict["NAME"]).parent.is_dir()
"""
            result = subprocess.run(
                [sys.executable, "-c", assertions],
                cwd=self.project_root,
                env={**os.environ, **environment},
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)

    def test_lan_rejects_missing_required_configuration(self) -> None:
        result = self.import_profile("lan", {})

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PMS_SECRET_KEY", result.stderr)

    def test_cloud_enables_strict_transport_security(self) -> None:
        environment = {
            "PMS_SECRET_KEY": "test-secret-not-for-production",
            "PMS_ALLOWED_HOSTS": "pms.example.com",
            "PMS_DB_NAME": "pms",
            "PMS_DB_USER": "pms",
            "PMS_DB_PASSWORD": "test-password",
            "PMS_DB_HOST": "database.example.com",
            "PMS_DB_PORT": "5432",
        }
        assertions = """
from pms.settings import cloud
assert cloud.DEBUG is False
assert cloud.SESSION_COOKIE_SECURE
assert cloud.CSRF_COOKIE_SECURE
assert cloud.SECURE_SSL_REDIRECT
"""
        result = subprocess.run(
            [sys.executable, "-c", assertions],
            cwd=self.project_root,
            env={**os.environ, **environment},
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_lan_requires_secure_cookies_without_enabling_debug(self) -> None:
        environment = {
            "PMS_SECRET_KEY": "test-secret-not-for-production",
            "PMS_ALLOWED_HOSTS": "pms.example.internal",
            "PMS_DB_NAME": "pms",
            "PMS_DB_USER": "pms",
            "PMS_DB_PASSWORD": "test-password",
            "PMS_DB_HOST": "database.example.internal",
            "PMS_DB_PORT": "5432",
        }
        assertions = """
from pms.settings import lan
assert lan.DEBUG is False
assert lan.SESSION_COOKIE_SECURE
assert lan.CSRF_COOKIE_SECURE
assert lan.ALLOWED_HOSTS == ["pms.example.internal"]
"""
        result = subprocess.run(
            [sys.executable, "-c", assertions],
            cwd=self.project_root,
            env={**os.environ, **environment},
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
