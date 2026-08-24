# F-001 依赖评审

状态：已接受（F-001）

## 1. 目的

本文记录 F-001 首次引入依赖的用途、替代方案、维护与兼容性、安全边界和许可证。精确版本以生成后的 `uv.lock` 为准，本文只重复记录需要人工理解的直接依赖结论。

## 2. 选择原则

- 只引入已接受 ADR 和 F-001 至 F-009 明确需要的依赖；
- 优先成熟、维护活跃且有明确官方文档的项目；
- 使用兼容范围表达升级边界，由 `uv.lock` 固定精确解析；
- 开发、测试、安全和服务器依赖分组，避免概念上都混入产品运行时；
- 依赖进入锁文件不代表其对应功能已经实现或验证。

## 3. 直接依赖

| 依赖 | 分组 | 用途 | 主要替代方案与选择理由 | 安全和维护边界 | 许可证 |
| --- | --- | --- | --- | --- | --- |
| Django | runtime | Web、ORM、迁移、认证、模板和表单 | FastAPI/SQLAlchemy 需要拼装更多基础能力；已由 ADR-0001 选择 Django 5.2 LTS | 锁定 5.2.x；跟踪安全公告和 LTS 支持期；F-002 后运行部署检查 | BSD-3-Clause |
| openpyxl | runtime | 后续只读解析 `.xlsx`/`.xlsm` BOM | pandas 对工作簿结构控制较弱且依赖更重；LibreOffice 自动化会执行更复杂外部行为 | 上传仍视为不可信；不执行 VBA、公式或外部链接；F-001 仅锁定不解析 | MIT |
| python-calamine | migration | 只读解析旧 `.xls/.xlsx/.xlsm/.xlsb` 核心数据库 | Excel COM 依赖 Office 并扩大宏/加载项风险；手工另存会失去可重复性；Calamine 可跨平台统一读取旧格式 | 仅进入 migration 依赖组；白名单源和精确表头；不启动 Excel、不执行 VBA/公式；当前为 Beta 且单维护者，锁定 0.8.x 并保留源哈希与对账 | MIT |
| Uvicorn | runtime | 后续提供正式 ASGI 运行入口 | Django `runserver` 不可交付；Gunicorn 是服务器进程管理补充而不是本机替代 | F-002 才创建 ASGI；部署时限制监听地址、代理和 worker | BSD-3-Clause |
| psycopg | server | PostgreSQL 18 驱动 | psycopg2 是旧一代驱动；其他数据库驱动不符合已接受 PostgreSQL 方案 | binary extra 用于可重复开发和 CI；正式服务器安装方式在部署批次复审 | LGPL-3.0-only |
| Ruff | dev | F-003 格式化和 Lint | Black + isort + Flake8 工具更多、规则易漂移 | F-001 只锁定；F-003 固定规则并执行 | MIT |
| mypy | dev | 静态检查领域、应用和公共边界 | Pyright 可行，但会增加另一套配置和 Node/独立工具管理 | 动态 Django 边界需插件或封装；不能用大量 ignore 制造假通过 | MIT |
| django-stubs | dev | 为 Django 提供 mypy 类型信息和插件 | 手写框架 stub 维护成本高 | 与 Django 5.2 和 mypy 锁版本共同验证；插件只服务类型检查 | MIT |
| types-openpyxl | dev | 为 BOM 解析边界提供 openpyxl 静态类型声明 | 手写 stub 容易漏掉只读单元格和工作簿 API；放弃检查会在不可信文件边界形成盲区 | 只进入开发组，不参与运行；版本随 openpyxl API 兼容性复核 | Apache-2.0 |
| pytest | test | 单元、集成和验收测试运行器 | unittest 属于标准库，但 fixture、标记和插件生态不如 pytest 适合计划矩阵 | F-003 注册标记并启用严格模式 | MIT |
| pytest-django | test | Django 测试设置和数据库生命周期 | 手工引导 Django 容易产生重复样板和隔离错误 | 数据库标记必须显式；不能让普通单元测试意外访问数据库 | BSD-3-Clause |
| pytest-cov | test | 覆盖率遗漏分析 | coverage.py 直接调用可行，插件减少 pytest 集成样板 | 覆盖率不替代业务场景；高风险边界关注分支而非总百分比 | MIT |
| pip-audit | security | 扫描锁定 Python 依赖的已知漏洞 | 仅依赖托管平台告警不足以覆盖本地锁文件 | 需要访问漏洞数据源；结果需人工判断，不允许无说明忽略 | Apache-2.0 |
| uv_build | build | 构建纯 Python 源码包和 wheel | Hatchling 更灵活，但当前包结构简单且无需构建脚本 | 与 uv 使用同一 0.12.x 兼容范围并设上限；仅支持纯 Python，符合当前工程 | MIT OR Apache-2.0 |

## 4. Python 与 Django 兼容基线

- `.python-version` 固定 CPython `3.14.7`；
- `requires-python` 限定 `>=3.14,<3.15`；
- Django 下限为 `5.2.8`，因为 Django 5.2 从该补丁起正式支持 Python 3.14；
- Django 上限 `<5.3`，避免锁文件在未评审时越过已接受的 5.2 LTS 系列；
- 所有候选直接依赖必须在 CPython 3.14.7 上完成实际解析、安装、导入或命令验证。

## 5. 供应链约束

- 只从 uv 默认的受信 Python 索引解析，不在 F-001 添加私有或额外索引；
- `uv.lock` 提交 Git，`.venv`、缓存和构建产物不提交；
- 不安装来自 Git 分支、任意 URL 或本地旧系统目录的包；
- 锁文件生成后运行 `pip-audit`，发现项必须记录结论；
- 发布产物签名和 SBOM 属于后续构建/发布批次，不在 F-001 伪造完成状态。

## 6. 执行结果

执行日期：2026-08-23。

### 6.1 解析和安装

- uv 版本：`0.12.5`；
- Python 版本：CPython `3.14.7`；
- uv 共解析并安装 56 个包；
- `uv lock --check` 通过；
- `uv sync --locked --all-groups` 从空 `.venv` 成功；
- `uv sync --locked --all-groups --offline --link-mode copy` 重复执行成功且没有依赖漂移；
- 初次同步因 uv 缓存与工作区无法硬链接而自动使用复制，只有性能影响，没有内容差异。

锁定的主要直接依赖：

| 依赖 | 锁定版本 |
| --- | --- |
| Django | 5.2.17 |
| openpyxl | 3.1.5 |
| Uvicorn | 0.52.4 |
| psycopg / psycopg-binary | 3.3.4 |
| Ruff | 0.16.4 |
| mypy | 1.20.2 |
| django-stubs | 5.2.9 |
| pytest | 9.1.1 |
| pytest-django | 4.14.0 |
| pytest-cov | 7.1.0 |
| pip-audit | 2.10.1 |
| types-openpyxl | 3.1.5.20260807 |
| python-calamine | 0.8.2 |

### 6.2 导入和构建

- `pms`、Django、openpyxl、python-calamine、Uvicorn 和 psycopg 在 CPython 3.14.7 中导入成功；
- uv_build 成功生成 `pms-0.0.0.tar.gz` 和 `pms-0.0.0-py3-none-any.whl`；
- `0.0.0` 是正式版本策略确定前的未发布工程占位符，不代表已经发布产品版本；
- 构建产物位于被 Git 忽略的 `dist/`，本批次不发布制品。

uv 使用内置的兼容 uv_build 后端完成构建，因此 uv_build 不单独安装在项目虚拟环境中；这与官方的 bundled build backend 行为一致。

### 6.3 许可证和安全

- 直接依赖的安装元数据与第 3 节许可证结论一致；
- `pip-audit --locked .` 不识别 `uv.lock`，明确返回“no lockfiles found”，不能作为审计结果；
- 改为对 `uv sync --locked --all-groups` 生成的精确虚拟环境执行 `pip-audit --local --skip-editable`；
- 审计结果为“未发现已知漏洞”；本地可编辑的 `pms` 包按预期跳过；
- 该结果只代表执行时漏洞数据库和当前锁版本，不是永久安全保证，依赖升级和发布前必须重新扫描。

## 7. 官方依据

- [uv 安装](https://docs.astral.sh/uv/getting-started/installation/)；
- [uv 项目与锁文件](https://docs.astral.sh/uv/guides/projects/)；
- [uv_build 构建后端](https://docs.astral.sh/uv/concepts/build-backend/)；
- [Django 5.2 与 Python 兼容性](https://docs.djangoproject.com/en/5.2/releases/5.2/)；
- [pip-audit](https://github.com/pypa/pip-audit)。
