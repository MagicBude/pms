# 本机正式启动器

状态：F-011 本地候选基线，等待 GitHub Actions 验证

## 1. 用途与边界

`launch_local` 是本机交付档案的正式源码级入口。它使用当前 Python 进程运行 Uvicorn，固定为
单进程、单 worker、无自动重载，并且只监听 IP loopback。它不支持内网或云端档案，也不替代
首次安装、数据库迁移、备份和升级流程。

当前仍需从仓库或源码安装目录执行命令。桌面快捷方式、安装包、内置 Python 运行时和系统托盘
属于后续交付包装，不在 F-011 范围内。

## 2. 启动前条件

首次使用必须先完成：

```powershell
uv sync --locked --all-groups
uv run python manage.py migrate --noinput
$env:PMS_INITIAL_ADMIN_PASSWORD = Read-Host "请输入初始管理员密码"
uv run python manage.py initialize_pms
Remove-Item Env:PMS_INITIAL_ADMIN_PASSWORD
```

启动器不会自动迁移，也不会隐式创建管理员。这两项会改变持久数据或需要临时秘密，必须由使用者
明确执行。正式启动还要求：

- `DJANGO_SETTINGS_MODULE` 为 `pms.settings.local`；
- `PMS_DEBUG` 未启用；
- SQLite、全部迁移和附件目录通过 ready 前置检查；
- 至少存在一个活动租户管理员；
- 配置端口可在 loopback 上监听；
- 同一 `PMS_DATA_DIR` 没有另一个启动器实例。

## 3. 正式启动与停止

在仓库根目录运行：

```powershell
uv run python manage.py launch_local
```

启动器从 local settings 同时取得监听地址、端口、数据目录和 ready 超时，不需要再手工拼装
Uvicorn 参数。只有 `/health/ready` 返回就绪后，默认浏览器才会打开一次：

```text
正在启动本机 PMS：http://127.0.0.1:8000
PMS 已就绪：http://127.0.0.1:8000
```

关闭终端或按 `Ctrl+C` 会先让 Uvicorn 正常停止，再释放数据目录实例锁。Windows 上由
Python 3.14 产生的末尾 `KeyboardInterrupt` 已被收敛为正常停止，不显示应用故障堆栈。

CI、远程终端或无图形环境可以关闭自动打开浏览器，但不会绕过其他检查：

```powershell
uv run python manage.py launch_local --no-browser
```

## 4. 配置

| 环境变量 | 默认值 | 规则 |
| --- | --- | --- |
| `PMS_BIND_HOST` | `127.0.0.1` | 必须是 IPv4 或 IPv6 loopback IP |
| `PMS_BIND_PORT` | `8000` | 十进制整数，范围 1024 至 65535 |
| `PMS_STARTUP_TIMEOUT_SECONDS` | `30` | 十进制整数，范围 1 至 120 秒 |
| `PMS_DATA_DIR` | 仓库下 `data/` | SQLite、附件和实例锁所属的本机私有目录 |
| `PMS_DEBUG` | `false` | 正式启动器必须保持关闭 |

例如端口 8000 已被可信程序占用时，可以在启动前选择另一个非特权端口：

```powershell
$env:PMS_BIND_PORT = "8765"
uv run python manage.py launch_local
```

不要把 `PMS_BIND_HOST` 改为 `0.0.0.0`、局域网地址或主机名。需要其他电脑访问时，应进入
ADR-0004 定义的 `lan + PostgreSQL` 部署，而不是把本机 SQLite 服务暴露到内网。

## 5. 单实例保护

启动器在数据目录中维护 `.pms-launcher.lock`。文件本身只包含 PID、启动时间和 loopback
监听参数，不包含数据路径、密码或令牌；真正的互斥来自操作系统锁：

- Windows 使用非阻塞 byte-range lock；
- Linux/macOS 使用非阻塞 `flock`；
- 进程正常退出或崩溃后，内核都会释放锁；
- 锁文件会保留，不能根据“文件是否存在”判断服务是否运行，也不要人工删除它。

如果同一数据目录已有启动器，第二次启动会安全失败。不同数据目录试图使用同一端口时，则会得到
端口占用提示。锁的目标是防止两个 PMS 进程同时使用同一个 SQLite 和附件目录，不是用户认证或
商业许可证。

## 6. 常见失败与处理

| 提示 | 安全处理 |
| --- | --- |
| 数据库迁移尚未完成 | 先停止其他 PMS，再运行 `uv run python manage.py migrate --noinput` |
| PMS 尚未完成首次初始化 | 按首次安装说明临时提供密码并运行 `initialize_pms` |
| 同一数据目录已有服务 | 使用已打开的页面；不要删除锁文件或强制启动第二实例 |
| 本机端口已被占用 | 确认占用来源，停止可信旧实例或设置另一个 `PMS_BIND_PORT` |
| 数据库或附件存储未就绪 | 检查磁盘、目录权限和数据完整性，不要绕过 ready |
| 服务未在规定时间内就绪 | 查看安全运行日志；启动器已经请求服务停止，不会打开浏览器 |
| 无法自动打开浏览器 | 服务仍保持运行，手动访问命令输出的 loopback 地址 |

错误提示不会回显数据库连接、数据目录、附件内容或底层异常。遇到来源不明的端口占用进程时，
先确认身份，不要直接结束其他程序。

## 7. 与开发服务器的区别

`runserver` 仍可用于开发调试，但不是本机交付入口：

- `runserver` 可能启用自动重载，启动参数需人工拼装；
- `launch_local` 强制正式配置、单 worker、实例锁和 ready 门槛；
- `launch_local` 只在服务真正可用后打开浏览器；
- 后续桌面快捷方式应调用启动器能力，而不是调用 `runserver`。
