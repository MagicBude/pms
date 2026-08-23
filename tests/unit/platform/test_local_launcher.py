"""本机启动器的锁、监听和 ready 编排单元测试。"""

import json
import socket
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.management import call_command

from pms.platform.local_launcher import (
    LOCK_FILENAME,
    LocalInstanceAlreadyRunningError,
    LocalInstanceLock,
    LocalLauncherConfiguration,
    LocalLauncherError,
    LocalPortUnavailableError,
    LocalStartupTimeoutError,
    ensure_port_available,
    launch_local_server,
    probe_ready_endpoint,
)


class FakeServer:
    """模拟 Uvicorn 的阻塞生命周期，让监视线程决定何时退出。"""

    def __init__(self, release: threading.Event, *, exit_immediately: bool = False) -> None:
        self.release = release
        self.exit_immediately = exit_immediately
        self.should_exit = False

    def run(self) -> None:
        if self.exit_immediately:
            return
        deadline = time.monotonic() + 2
        while not self.should_exit and not self.release.is_set():
            if time.monotonic() >= deadline:
                raise RuntimeError("fake server did not receive a stop condition")
            time.sleep(0.001)


class InterruptingServer:
    """模拟 Windows 上 Uvicorn 完成 shutdown 后重新抛出的 Ctrl+C。"""

    should_exit = False

    def run(self) -> None:
        raise KeyboardInterrupt


def build_configuration(tmp_path: Path, *, timeout: float = 1) -> LocalLauncherConfiguration:
    """为单元测试建立不接触真实 PMS 数据的 loopback 配置。"""
    return LocalLauncherConfiguration(
        host="127.0.0.1",
        port=8000,
        data_dir=tmp_path,
        startup_timeout_seconds=timeout,
    )


@pytest.mark.unit
def test_configuration_formats_ipv4_and_ipv6_browser_urls(tmp_path: Path) -> None:
    ipv4 = build_configuration(tmp_path)
    ipv6 = LocalLauncherConfiguration("::1", 8765, tmp_path, 30)

    assert ipv4.base_url == "http://127.0.0.1:8000"
    assert ipv6.base_url == "http://[::1]:8765"


@pytest.mark.unit
def test_instance_lock_rejects_duplicate_and_can_be_reacquired(tmp_path: Path) -> None:
    lock_path = tmp_path / LOCK_FILENAME
    first = LocalInstanceLock(lock_path, host="127.0.0.1", port=8000)
    second = LocalInstanceLock(lock_path, host="127.0.0.1", port=8000)

    with first, pytest.raises(LocalInstanceAlreadyRunningError, match="已经有本机服务"):
        second.__enter__()

    with LocalInstanceLock(lock_path, host="127.0.0.1", port=8000):
        pass
    metadata = json.loads(lock_path.read_text(encoding="utf-8"))
    assert metadata["format"] == "pms-local-launcher-lock"
    assert metadata["port"] == 8000
    assert "data" not in metadata


@pytest.mark.unit
def test_port_check_reports_an_existing_loopback_listener() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        occupied_port = listener.getsockname()[1]

        with pytest.raises(LocalPortUnavailableError, match=str(occupied_port)):
            ensure_port_available("127.0.0.1", occupied_port)


@pytest.mark.unit
def test_launcher_opens_browser_once_only_after_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("pms.platform.local_launcher.READY_POLL_INTERVAL_SECONDS", 0.001)
    release = threading.Event()
    server = FakeServer(release)
    probes: list[str] = []
    browser_urls: list[str] = []
    notifications: list[str] = []

    def ready_probe(url: str, timeout: float) -> bool:
        del timeout
        probes.append(url)
        return len(probes) >= 2

    def browser_opener(url: str) -> bool:
        browser_urls.append(url)
        release.set()
        return True

    launch_local_server(
        build_configuration(tmp_path),
        runtime_validator=lambda configuration: None,
        port_checker=lambda host, port: None,
        server_factory=lambda configuration: server,
        ready_probe=ready_probe,
        browser_opener=browser_opener,
        notifier=notifications.append,
    )

    assert len(probes) == 2
    assert browser_urls == ["http://127.0.0.1:8000"]
    assert notifications == ["PMS 已就绪：http://127.0.0.1:8000"]


@pytest.mark.unit
def test_launcher_keeps_service_available_when_browser_cannot_open(tmp_path: Path) -> None:
    release = threading.Event()
    server = FakeServer(release)
    browser_calls = 0
    notifications: list[str] = []

    def browser_opener(url: str) -> bool:
        nonlocal browser_calls
        del url
        browser_calls += 1
        release.set()
        return False

    launch_local_server(
        build_configuration(tmp_path),
        runtime_validator=lambda configuration: None,
        port_checker=lambda host, port: None,
        server_factory=lambda configuration: server,
        ready_probe=lambda url, timeout: True,
        browser_opener=browser_opener,
        notifier=notifications.append,
    )

    assert browser_calls == 1
    assert notifications[-1] == "无法自动打开浏览器，请手动访问 http://127.0.0.1:8000"


@pytest.mark.unit
def test_launcher_no_browser_mode_never_calls_browser(tmp_path: Path) -> None:
    release = threading.Event()
    server = FakeServer(release)
    browser_calls = 0

    def ready_probe(url: str, timeout: float) -> bool:
        del url, timeout
        release.set()
        return True

    def browser_opener(url: str) -> bool:
        nonlocal browser_calls
        del url
        browser_calls += 1
        return True

    launch_local_server(
        build_configuration(tmp_path),
        open_browser=False,
        runtime_validator=lambda configuration: None,
        port_checker=lambda host, port: None,
        server_factory=lambda configuration: server,
        ready_probe=ready_probe,
        browser_opener=browser_opener,
        notifier=lambda message: None,
    )

    assert browser_calls == 0


@pytest.mark.unit
def test_launcher_timeout_stops_server_without_opening_browser(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("pms.platform.local_launcher.READY_POLL_INTERVAL_SECONDS", 0.001)
    release = threading.Event()
    server = FakeServer(release)
    browser_calls = 0

    def browser_opener(url: str) -> bool:
        nonlocal browser_calls
        del url
        browser_calls += 1
        return True

    with pytest.raises(LocalStartupTimeoutError, match="安全停止"):
        launch_local_server(
            build_configuration(tmp_path, timeout=0.01),
            runtime_validator=lambda configuration: None,
            port_checker=lambda host, port: None,
            server_factory=lambda configuration: server,
            ready_probe=lambda url, timeout: False,
            browser_opener=browser_opener,
            notifier=lambda message: None,
        )

    assert server.should_exit is True
    assert browser_calls == 0


@pytest.mark.unit
def test_launcher_reports_server_exit_before_ready(tmp_path: Path) -> None:
    release = threading.Event()
    server = FakeServer(release, exit_immediately=True)

    with pytest.raises(LocalLauncherError, match="就绪前停止"):
        launch_local_server(
            build_configuration(tmp_path),
            runtime_validator=lambda configuration: None,
            port_checker=lambda host, port: None,
            server_factory=lambda configuration: server,
            ready_probe=lambda url, timeout: False,
            notifier=lambda message: None,
        )


@pytest.mark.unit
def test_launcher_treats_user_keyboard_interrupt_as_normal_stop(tmp_path: Path) -> None:
    launch_local_server(
        build_configuration(tmp_path),
        runtime_validator=lambda configuration: None,
        port_checker=lambda host, port: None,
        server_factory=lambda configuration: InterruptingServer(),
        ready_probe=lambda url, timeout: False,
        notifier=lambda message: None,
    )


@pytest.mark.unit
def test_ready_probe_rejects_non_loopback_without_network_access() -> None:
    with patch("pms.platform.local_launcher.urllib.request.build_opener") as build_opener:
        assert probe_ready_endpoint("https://example.com/health/ready", 0.5) is False

    build_opener.assert_not_called()


@pytest.mark.unit
def test_management_command_forwards_headless_mode(tmp_path: Path) -> None:
    configuration = build_configuration(tmp_path)
    with (
        patch(
            "pms.platform.management.commands.launch_local.configuration_from_settings",
            return_value=configuration,
        ),
        patch("pms.platform.management.commands.launch_local.launch_local_server") as launch,
    ):
        call_command("launch_local", no_browser=True, no_color=True)

    assert launch.call_args.kwargs["open_browser"] is False
