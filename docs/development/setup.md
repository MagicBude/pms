# 开发环境搭建

状态：F-001 基线

## 1. 当前能力

本页只覆盖 F-001 的 Python、uv、依赖同步和包构建。Django settings、ASGI、数据库和启动命令将在后续批次建立，当前不能使用 `runserver` 或假设应用已经可访问。

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

## 4. F-001 验证

```powershell
uv run python --version
uv run python -c "import pms; print(pms.__name__)"
uv lock --check
uv sync --locked --all-groups
uv build
```

预期：

- Python 为 `3.14.7`；
- 顶层包可以输出 `pms`；
- 锁文件与 `pyproject.toml` 一致；
- 第二次同步没有依赖漂移；
- `dist/` 中可以生成源码包和 wheel，且构建产物不进入 Git。

本批次尚未配置 Ruff、mypy、pytest 或 CI 命令。它们已经锁入依赖，但只有 F-003 配置完成并实际运行后才能报告检查通过。

## 5. 更新依赖

依赖更新必须是独立、可评审任务：

1. 查证目标版本的 Python、Django 和平台兼容性；
2. 更新 `pyproject.toml` 的必要约束；
3. 使用 `uv lock --upgrade-package <name>` 更新锁文件；
4. 重新执行同步、构建和受影响检查；
5. 更新 [依赖评审](dependency-review.md)与工作日志；
6. 使用完整提交正文记录原因、版本变化、安全影响和验证结果。

禁止直接手工编辑 `uv.lock`，也不能只更新本机 `.venv` 而不提交声明和锁文件。
