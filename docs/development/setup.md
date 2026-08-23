# 开发环境搭建

状态：F-002 基线

## 1. 当前能力

本页覆盖 Python、uv、依赖同步，以及 F-002 的最小 Django ASGI 启动。当前根路径只是工程连通性提示，不是正式业务界面；不得据此创建数据库或录入真实数据。

## 2. 前置条件

- Windows、Linux 或 macOS 的受支持开发环境；
- Git；
- uv 0.12.x；
- 可访问项目声明的软件包索引；
- 不需要安装系统级 Python，uv 会根据 `.python-version` 获取 CPython 3.14.7。

uv 的官方安装方式见 [Installing uv](https://docs.astral.sh/uv/getting-started/installation/)。不要从来源不明的镜像下载可执行文件。

## 3. 克隆后同步

在仓库根目录执行：

```powershell
uv python install 3.14.7
uv sync --locked --all-groups
```

说明：

- `.python-version` 固定开发默认解释器；
- `pyproject.toml` 声明直接依赖的兼容范围；
- `uv.lock` 固定完整、跨平台的解析结果；
- `.venv/` 是本机生成环境，已被 Git 忽略；
- `--locked` 禁止同步时悄悄改变锁文件；
- `--all-groups` 为开发与完整检查安装 server、dev、test、security 全部分组。

生产部署不会直接照搬全部开发分组，具体安装方式在部署批次确定。

## 4. 本机启动

开发调试可执行：

```powershell
uv run python manage.py runserver 127.0.0.1:8000
```

浏览器访问 `http://127.0.0.1:8000/`，应看到“PMS 工程基础已启动”的文本提示。首次启动会自动创建被 Git 忽略的本机数据目录和空 SQLite 文件，但 F-004 前不会创建任何用户表或业务表。开发服务器只用于开发；本机交付将使用 Uvicorn 和后续启动器。

验证 ASGI 正式入口可执行：

```powershell
uv run uvicorn pms.asgi:application --host 127.0.0.1 --port 8000
```

`local` 档案会拒绝 `PMS_BIND_HOST=0.0.0.0` 等非 loopback 配置。启动命令的 `--host` 必须与 `PMS_BIND_HOST` 保持一致；当前尚未建立自动拼装参数的启动器。

## 5. 验证

```powershell
uv run python --version
uv run python -c "import pms; print(pms.__name__)"
uv lock --check
uv sync --locked --all-groups
uv build
uv run python manage.py check
uv run python -m unittest discover -v
uv run python manage.py makemigrations --check --dry-run
```

预期：

- Python 为 `3.14.7`；
- 顶层包可以输出 `pms`；
- 锁文件与 `pyproject.toml` 一致；
- 第二次同步没有依赖漂移；
- `dist/` 中可以生成源码包和 wheel，且构建产物不进入 Git。
- Django system checks 和 F-002 配置测试通过；
- 迁移检查显示 `No changes detected`，启动过程不创建用户表或业务表。

本批次尚未配置 Ruff、mypy、pytest 或 CI 命令。它们已经锁入依赖，但只有 F-003 配置完成并实际运行后才能报告检查通过。

配置档案和环境变量见[部署配置档案](../deployment/configuration-profiles.md)。F-003 才会把 Ruff、mypy、pytest 和 CI 配置为统一质量门槛。

## 6. 更新依赖

依赖更新必须是独立、可评审任务：

1. 查证目标版本的 Python、Django 和平台兼容性；
2. 更新 `pyproject.toml` 的必要约束；
3. 使用 `uv lock --upgrade-package <name>` 更新锁文件；
4. 重新执行同步、构建和受影响检查；
5. 更新 [依赖评审](dependency-review.md)与工作日志；
6. 使用完整提交正文记录原因、版本变化、安全影响和验证结果。

禁止直接手工编辑 `uv.lock`，也不能只更新本机 `.venv` 而不提交声明和锁文件。
