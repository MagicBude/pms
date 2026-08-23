"""本机交付档案的单实例 Uvicorn 启动编排。

启动器不是开发服务器包装。它只接受 ``local + SQLite``，在当前进程中
运行一个 Uvicorn worker，并把迁移、初始化、附件可写性和 loopback
边界都放在打开浏览器之前验证。同一数据目录的操作系统文件锁会随进程
退出自动释放，避免崩溃遗留的普通标记文件永久阻止后续启动。
"""

import importlib
import ipaddress
import json
import os
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import BinaryIO, Protocol, Self, cast

import uvicorn
from django.conf import settings
from django.db import DatabaseError, connection, connections

from pms.authorization.domain.permissions import RoleCode
from pms.authorization.infrastructure.django.models import MembershipRole
from pms.platform.health import check_readiness


class _WindowsLockingModule(Protocol):
    """Windows 标准库 byte-range lock 的受控类型表面。"""

    LK_NBLCK: int
    LK_UNLCK: int

    def locking(self, file_descriptor: int, mode: int, byte_count: int) -> None: ...


class _PosixLockingModule(Protocol):
    """POSIX 标准库 flock 的受控类型表面。"""

    LOCK_EX: int
    LOCK_NB: int
    LOCK_UN: int

    def flock(self, file_descriptor: int, operation: int) -> None: ...


_WINDOWS_LOCKING: _WindowsLockingModule | None = None
_POSIX_LOCKING: _PosixLockingModule | None = None
if os.name == "nt":
    _WINDOWS_LOCKING = cast(
        _WindowsLockingModule,
        importlib.import_module("msvcrt"),
    )
else:
    _POSIX_LOCKING = cast(
        _PosixLockingModule,
        importlib.import_module("fcntl"),
    )

READY_PATH = "/health/ready"
LOCK_FILENAME = ".pms-launcher.lock"
READY_REQUEST_TIMEOUT_SECONDS = 0.5
READY_POLL_INTERVAL_SECONDS = 0.2


class LocalLauncherError(RuntimeError):
    """本机服务不能安全启动或未能完成就绪。"""


class LocalLauncherConfigurationError(LocalLauncherError):
    """部署档案、监听参数或本机数据目录不满足启动器约束。"""


class LocalLauncherPreflightError(LocalLauncherError):
    """迁移、初始化或必要存储尚未达到可服务状态。"""


class LocalInstanceAlreadyRunningError(LocalLauncherError):
    """同一数据目录已经由另一个本机启动器持有。"""


class LocalPortUnavailableError(LocalLauncherError):
    """配置的 loopback 端口当前不能被本机服务占用。"""


class LocalStartupTimeoutError(LocalLauncherError):
    """Uvicorn 没有在受控时间内通过 ready 探针。"""


@dataclass(frozen=True, slots=True)
class LocalLauncherConfiguration:
    """经过 settings 边界解析的本机启动参数。"""

    host: str
    port: int
    data_dir: Path
    startup_timeout_seconds: float

    @property
    def base_url(self) -> str:
        """返回可直接交给浏览器的 loopback 根地址。"""
        address = ipaddress.ip_address(self.host)
        display_host = f"[{self.host}]" if address.version == 6 else self.host
        return f"http://{display_host}:{self.port}"


class ServerController(Protocol):
    """启动监视线程控制 Uvicorn 生命周期所需的最小接口。"""

    should_exit: bool

    def run(self) -> None:
        """阻塞运行服务，直到用户停止或 ``should_exit`` 被设置。"""


ServerFactory = Callable[[LocalLauncherConfiguration], ServerController]
ReadyProbe = Callable[[str, float], bool]
BrowserOpener = Callable[[str], bool]
Notifier = Callable[[str], None]
RuntimeValidator = Callable[[LocalLauncherConfiguration], None]
PortChecker = Callable[[str, int], None]


@dataclass(slots=True)
class _StartupOutcome:
    """主线程和就绪监视线程之间只传递最终状态，不传递底层异常。"""

    ready: bool = False
    error: LocalLauncherError | None = None


class LocalInstanceLock:
    """持有数据目录级跨平台排他锁，防止同一 SQLite 被重复启动。

    锁文件会保留用于人工诊断，但真正的互斥来自 Windows byte-range lock
    或 POSIX flock。进程崩溃时内核会释放锁，因此不能用文件是否存在判断
    实例是否运行，也不要在释放后删除文件造成 inode 竞态。
    """

    def __init__(self, path: Path, *, host: str, port: int) -> None:
        self.path = path
        self.host = host
        self.port = port
        self._stream: BinaryIO | None = None

    def __enter__(self) -> Self:
        """取得排他锁并写入不含路径和秘密的诊断元数据。"""
        if self.path.is_symlink():
            raise LocalLauncherConfigurationError("本机实例锁不能是符号链接。")
        stream: BinaryIO | None = None
        try:
            stream = self.path.open("a+b")
            if self.path.stat().st_size == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            _acquire_os_lock(stream)
        except BlockingIOError as error:
            if stream is not None:
                stream.close()
            raise LocalInstanceAlreadyRunningError(
                "同一 PMS 数据目录已经有本机服务在运行。"
            ) from error
        except OSError as error:
            if stream is not None:
                stream.close()
            raise LocalLauncherConfigurationError("无法取得本机服务实例锁。") from error
        except LocalLauncherError:
            if stream is not None:
                stream.close()
            raise

        try:
            metadata = {
                "format": "pms-local-launcher-lock",
                "pid": os.getpid(),
                "started_at": datetime.now(tz=UTC).isoformat(),
                "host": self.host,
                "port": self.port,
            }
            stream.seek(0)
            stream.truncate()
            stream.write((json.dumps(metadata, sort_keys=True) + "\n").encode("utf-8"))
            stream.flush()
        except OSError as error:
            _release_os_lock(stream)
            stream.close()
            raise LocalLauncherConfigurationError("无法记录本机服务实例状态。") from error
        self._stream = stream
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """释放内核锁；锁文件故意保留，不能被当作运行状态。"""
        del exc_type, exc_value, traceback
        if self._stream is None:
            return
        try:
            _release_os_lock(self._stream)
        finally:
            self._stream.close()
            self._stream = None


def configuration_from_settings() -> LocalLauncherConfiguration:
    """从已经加载的 local settings 建立不可漂移的启动参数。"""
    if getattr(settings, "DEPLOYMENT_PROFILE", None) != "local":
        raise LocalLauncherConfigurationError("本机启动器只支持 local 部署档案。")
    host = getattr(settings, "BIND_HOST", None)
    port = getattr(settings, "BIND_PORT", None)
    data_dir = getattr(settings, "DATA_DIR", None)
    timeout = getattr(settings, "STARTUP_TIMEOUT_SECONDS", None)
    if (
        not isinstance(host, str)
        or not isinstance(port, int)
        or isinstance(port, bool)
        or not isinstance(data_dir, str | os.PathLike)
        or not isinstance(timeout, int | float)
        or isinstance(timeout, bool)
    ):
        raise LocalLauncherConfigurationError("本机启动器配置缺失或类型无效。")
    try:
        address = ipaddress.ip_address(host)
    except ValueError as error:
        raise LocalLauncherConfigurationError("本机启动器监听地址必须是 IP loopback。") from error
    if not address.is_loopback or not 1024 <= port <= 65535 or not 1 <= timeout <= 120:
        raise LocalLauncherConfigurationError("本机启动器监听端口或超时配置不安全。")
    resolved_data_dir = Path(data_dir).resolve()
    if not resolved_data_dir.is_dir():
        raise LocalLauncherConfigurationError("本机数据目录不可用。")
    return LocalLauncherConfiguration(host, port, resolved_data_dir, float(timeout))


def validate_local_runtime(configuration: LocalLauncherConfiguration) -> None:
    """在启动 Uvicorn 前验证交付档案、迁移、初始化和必要存储。

    本检查不自动迁移或创建管理员，因为两项操作都可能改变数据或需要
    临时秘密。失败信息只给出下一条安全操作，不回显数据库和文件路径。
    """
    if getattr(settings, "DEPLOYMENT_PROFILE", None) != "local" or connection.vendor != "sqlite":
        raise LocalLauncherConfigurationError("本机启动器只支持 local 档案的 SQLite。")
    if settings.DEBUG:
        raise LocalLauncherConfigurationError("本机正式启动器禁止 PMS_DEBUG。")
    configured_data_dir = getattr(settings, "DATA_DIR", None)
    if (
        getattr(settings, "BIND_HOST", None) != configuration.host
        or getattr(settings, "BIND_PORT", None) != configuration.port
        or not isinstance(configured_data_dir, str | os.PathLike)
        or Path(configured_data_dir).resolve() != configuration.data_dir
    ):
        raise LocalLauncherConfigurationError("启动参数与 local settings 不一致。")
    readiness = check_readiness()
    if readiness.checks.get("migrations") == "pending":
        raise LocalLauncherPreflightError(
            "数据库迁移尚未完成，请先运行 python manage.py migrate --noinput。"
        )
    if not readiness.ready:
        raise LocalLauncherPreflightError("数据库或附件存储尚未就绪，不能启动本机服务。")
    try:
        initialized = MembershipRole.objects.filter(
            role_id=RoleCode.TENANT_ADMIN,
            membership__is_active=True,
            membership__tenant__is_active=True,
            membership__user__is_active=True,
        ).exists()
    except DatabaseError as error:
        raise LocalLauncherPreflightError("无法验证 PMS 首次初始化状态。") from error
    if not initialized:
        raise LocalLauncherPreflightError(
            "PMS 尚未完成首次初始化，请先运行 python manage.py initialize_pms。"
        )


def ensure_port_available(host: str, port: int) -> None:
    """在创建 Uvicorn 前给出稳定的端口占用错误。

    bind 检查与 Uvicorn 真正监听之间仍存在极短竞态，因此 Uvicorn 的绑定
    失败也会由启动未就绪边界收敛；这里主要为常见占用提供可操作提示。
    """
    address = ipaddress.ip_address(host)
    family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as probe:
            # Linux 会为刚关闭且处理过连接的监听端口保留 TIME_WAIT。Uvicorn
            # 的 asyncio 监听器允许安全复用这种本地地址；预检必须采用相同
            # 语义，否则备份恢复烟雾测试紧接着重启时会产生假“端口占用”。
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind((host, port))
    except OSError as error:
        raise LocalPortUnavailableError(
            f"本机端口 {port} 已被占用，请停止占用程序或设置 PMS_BIND_PORT。"
        ) from error


def launch_local_server(
    configuration: LocalLauncherConfiguration,
    *,
    open_browser: bool = True,
    runtime_validator: RuntimeValidator = validate_local_runtime,
    port_checker: PortChecker = ensure_port_available,
    server_factory: ServerFactory | None = None,
    ready_probe: ReadyProbe | None = None,
    browser_opener: BrowserOpener = webbrowser.open,
    notifier: Notifier = print,
) -> None:
    """取得数据目录锁，启动 Uvicorn，并在 ready 后只打开一次浏览器。

    Args:
        configuration: 从 local settings 读取的 loopback 地址、端口和数据目录。
        open_browser: 默认为真；CI 或无图形环境可显式关闭自动打开。
        runtime_validator: 启动前只读检查；测试可注入确定替身。
        port_checker: loopback 监听能力检查；测试可注入确定替身。
        server_factory: Uvicorn 控制器工厂；省略时建立单 worker 正式服务器。
        ready_probe: 返回公开 ready 是否通过；省略时发起不使用代理的 HTTP 请求。
        browser_opener: ready 后调用一次的系统浏览器入口。
        notifier: 输出不含秘密和绝对路径的用户提示。

    Raises:
        LocalLauncherError: 配置、前置状态、实例锁、端口或 ready 失败。

    Side Effects:
        在当前进程运行 Uvicorn，持有数据目录锁，并可能打开默认浏览器。
        Ctrl+C 由 Uvicorn 正常收敛；函数返回时内核实例锁必定释放。
    """
    factory = server_factory or _create_uvicorn_server
    probe = ready_probe or probe_ready_endpoint
    lock_path = configuration.data_dir / LOCK_FILENAME
    with LocalInstanceLock(lock_path, host=configuration.host, port=configuration.port):
        runtime_validator(configuration)
        port_checker(configuration.host, configuration.port)
        # 前置检查可能打开数据库连接；进入 ASGI 事件循环前关闭当前线程连接，
        # 让 Django 按请求上下文重新取得连接，避免跨执行上下文复用 SQLite。
        connections.close_all()
        server = factory(configuration)
        stop_monitor = threading.Event()
        outcome = _StartupOutcome()
        monitor = threading.Thread(
            target=_monitor_startup,
            kwargs={
                "configuration": configuration,
                "server": server,
                "stop_monitor": stop_monitor,
                "outcome": outcome,
                "ready_probe": probe,
                "open_browser": open_browser,
                "browser_opener": browser_opener,
                "notifier": notifier,
            },
            name="pms-local-ready-monitor",
            daemon=True,
        )
        monitor.start()
        interrupted_by_user = False
        try:
            server.run()
        except KeyboardInterrupt:
            # Python 3.14 的 asyncio.Runner 在 Windows Ctrl+C 后会在 Uvicorn
            # 已完成 shutdown 的情况下再次抛出 KeyboardInterrupt。用户主动
            # 停止属于正常生命周期，不能显示误导性的应用故障堆栈。
            interrupted_by_user = True
        except SystemExit as error:
            raise LocalLauncherError("本机 Uvicorn 未能安全启动。") from error
        except Exception as error:
            raise LocalLauncherError("本机 Uvicorn 运行失败，服务已经停止。") from error
        finally:
            stop_monitor.set()
            monitor.join(timeout=READY_REQUEST_TIMEOUT_SECONDS + 1)
        if interrupted_by_user:
            return
        if outcome.error is not None:
            raise outcome.error
        if not outcome.ready:
            raise LocalLauncherError("本机 Uvicorn 在就绪前停止。")


def probe_ready_endpoint(url: str, timeout_seconds: float) -> bool:
    """不使用系统代理访问 loopback ready，并只接受稳定就绪结构。"""
    parsed = urllib.parse.urlsplit(url)
    try:
        is_loopback = (
            parsed.hostname is not None and ipaddress.ip_address(parsed.hostname).is_loopback
        )
    except ValueError:
        return False
    if parsed.scheme != "http" or not is_loopback:
        return False
    # URL 已在上方限定为 HTTP loopback，不允许 file、自定义 scheme 或远端主机。
    request = urllib.request.Request(url, method="GET")  # noqa: S310
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            if response.status != 200:
                return False
            payload = json.loads(response.read(4096))
    except OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError:
        return False
    return isinstance(payload, dict) and payload.get("status") == "ready"


def _monitor_startup(
    *,
    configuration: LocalLauncherConfiguration,
    server: ServerController,
    stop_monitor: threading.Event,
    outcome: _StartupOutcome,
    ready_probe: ReadyProbe,
    open_browser: bool,
    browser_opener: BrowserOpener,
    notifier: Notifier,
) -> None:
    """轮询 ready；超时会请求 Uvicorn 退出，浏览器最多调用一次。"""
    ready_url = f"{configuration.base_url}{READY_PATH}"
    deadline = time.monotonic() + configuration.startup_timeout_seconds
    while not stop_monitor.is_set():
        if ready_probe(ready_url, READY_REQUEST_TIMEOUT_SECONDS):
            outcome.ready = True
            notifier(f"PMS 已就绪：{configuration.base_url}")
            if open_browser:
                try:
                    opened = browser_opener(configuration.base_url)
                except Exception:
                    opened = False
                if not opened:
                    notifier(f"无法自动打开浏览器，请手动访问 {configuration.base_url}")
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            outcome.error = LocalStartupTimeoutError("本机服务未在规定时间内就绪，已经安全停止。")
            server.should_exit = True
            return
        stop_monitor.wait(min(READY_POLL_INTERVAL_SECONDS, remaining))


def _create_uvicorn_server(configuration: LocalLauncherConfiguration) -> ServerController:
    """建立固定单 worker、无 reload 的 Uvicorn 控制器。"""
    config = uvicorn.Config(
        "pms.asgi:application",
        host=configuration.host,
        port=configuration.port,
        workers=1,
        reload=False,
        log_level="info",
    )
    return uvicorn.Server(config)


def _acquire_os_lock(stream: BinaryIO) -> None:
    """在当前平台对锁文件第一个 byte 取得非阻塞排他锁。"""
    stream.seek(0)
    if os.name == "nt":
        windows_locking = _WINDOWS_LOCKING
        if windows_locking is None:
            raise LocalLauncherConfigurationError("当前 Windows 文件锁不可用。")
        try:
            windows_locking.locking(
                stream.fileno(),
                windows_locking.LK_NBLCK,
                1,
            )
        except OSError as error:
            raise BlockingIOError from error
    else:
        posix_locking = _POSIX_LOCKING
        if posix_locking is None:
            raise LocalLauncherConfigurationError("当前 POSIX 文件锁不可用。")
        try:
            posix_locking.flock(
                stream.fileno(),
                posix_locking.LOCK_EX | posix_locking.LOCK_NB,
            )
        except OSError as error:
            raise BlockingIOError from error


def _release_os_lock(stream: BinaryIO) -> None:
    """释放与 ``_acquire_os_lock`` 对称的内核锁。"""
    stream.seek(0)
    if os.name == "nt":
        windows_locking = _WINDOWS_LOCKING
        if windows_locking is None:
            raise LocalLauncherConfigurationError("当前 Windows 文件锁不可用。")
        windows_locking.locking(stream.fileno(), windows_locking.LK_UNLCK, 1)
    else:
        posix_locking = _POSIX_LOCKING
        if posix_locking is None:
            raise LocalLauncherConfigurationError("当前 POSIX 文件锁不可用。")
        posix_locking.flock(stream.fileno(), posix_locking.LOCK_UN)
