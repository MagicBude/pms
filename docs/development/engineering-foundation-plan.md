# 工程骨架与质量工具实施计划

状态：已接受

## 1. 目的

本计划把已接受的架构决策拆成可以独立实现、验证、提交和回档的工程批次。它回答 Phase 1 应按什么顺序建立工程基础，但不在本文档批次创建 Django 工程或安装依赖。

实施必须遵守：

- [ADR-0001：技术栈与模块化单体](../architecture/adr/ADR-0001-technology-stack-and-modular-monolith.md)；
- [ADR-0002：数据库兼容与多租户](../architecture/adr/ADR-0002-database-and-multi-tenancy.md)；
- [ADR-0003：附件存储与上传安全](../architecture/adr/ADR-0003-attachment-storage.md)；
- [ADR-0004：部署配置](../architecture/adr/ADR-0004-deployment-profiles.md)；
- [模块边界](../architecture/module-boundaries.md)和 [工程规范](../standard/README.md)。

## 2. 前置条件

开始第一个代码批次前必须满足：

- 本计划由用户接受；
- 首个切片的业务术语、角色、状态和范围保持已接受；
- 工作树干净，当前提交已经推送或用户明确接受仅本地回档；
- 再次查证计划依赖与 Python 3.14、Django 5.2 LTS 的兼容性；
- 不读取、不运行、不打包 `.internal/legacy-pms/` 中的宏或可执行文件。

## 3. 本计划交付范围

### 3.1 包含

- Python 与 Django 最小工程；
- 依赖锁定和开发环境说明；
- 本机、内网、云端配置档案；
- Ruff、mypy、pytest、Django system checks 和 CI；
- 自有用户模型；
- tenant、membership、role、permission 和 audit 基础；
- SQLite 与 PostgreSQL 迁移验证；
- 健康检查、结构化日志和基础错误边界；
- 附件存储端口与本地适配器骨架；
- 本机开发启动、数据库初始化和测试命令。

### 3.2 不包含

- `SLICE-001` 的项目、BOM、投产和请购业务实现；
- 完整本机安装器或桌面打包；
- 内网正式反向代理、TLS 和生产容器发布；
- 云端基础设施、对象存储供应商和订阅计费；
- 后台任务队列、消息中间件、微服务或 SPA；
- 旧系统真实数据迁移。

## 4. 技术基线

| 类别 | 已接受选择 | 骨架阶段动作 |
| --- | --- | --- |
| Python | CPython 3.14 最新补丁 | 写入 `.python-version` 和 `requires-python` |
| Web | Django 5.2 LTS 最新补丁 | 创建 ASGI 工程和自有用户模型 |
| 页面 | Django Templates、HTMX 2.x、原生 JavaScript | 只建立静态资源和基础布局，不做业务页面 |
| 本机数据库 | SQLite | 作为默认开发和本机配置，不允许共享访问 |
| 服务器数据库 | PostgreSQL 18 最新补丁 | CI 和集成测试从首批迁移开始覆盖 |
| 依赖管理 | uv | 提交 `pyproject.toml`、`.python-version` 和 `uv.lock` |
| 格式与 Lint | Ruff | 固定配置并同时检查源码、测试和脚本 |
| 类型检查 | mypy | 对领域、应用和公共边界采用严格设置 |
| 测试 | pytest、pytest-django | 区分单元、SQLite 集成、PostgreSQL 集成和验收测试 |
| Excel | openpyxl | 骨架仅锁定依赖，不提前实现 BOM 导入 |
| 应用服务器 | Uvicorn | 本机交付和服务器配置共用 ASGI 入口 |

依赖版本在实际创建骨架时锁定，本文不重复维护容易过期的补丁版本。每个新依赖仍需在对应提交正文说明用途、替代方案、维护状态和安全影响。

## 5. 目标目录

初始工程建议形成以下结构：

```text
.
├── .github/
│   └── workflows/
│       └── quality.yml
├── docs/
│   └── development/
│       ├── setup.md
│       └── engineering-foundation-plan.md
├── scripts/
├── src/
│   └── pms/
│       ├── asgi.py
│       ├── urls.py
│       ├── settings/
│       │   ├── base.py
│       │   ├── local.py
│       │   ├── lan.py
│       │   ├── cloud.py
│       │   └── test.py
│       ├── platform/
│       ├── identity/
│       ├── tenancy/
│       ├── authorization/
│       └── audit/
├── tests/
│   ├── unit/
│   ├── integration/
│   └── acceptance/
├── .env.example
├── .python-version
├── manage.py
├── pyproject.toml
└── uv.lock
```

只创建当前批次实际使用的包。`master_data`、`projects`、`attachments`、`bom`、`production` 和 `procurement` 不为保持目录对称而提前生成空 Django app；进入对应业务批次时再建立。

## 6. 模块内部结构

有业务规则的 Django app 按以下职责组织：

```text
src/pms/<module>/
├── domain/
├── application/
├── infrastructure/
│   └── django/
├── presentation/
├── migrations/
├── apps.py
└── models.py
```

根级 `models.py` 只承担 Django 模型发现桥接，导入 `infrastructure/django/` 中的 ORM 映射；它不得包含业务规则。这样既满足 Django 发现约定，又保留 `domain` 不依赖 Django 的边界。该桥接方式必须有模块注释和导入边界测试。

简单模块允许减少物理层次，但以下依赖方向不变：

```text
presentation -> application -> domain
                         ^
infrastructure ----------|
```

## 7. 配置设计

### 7.1 共同规则

- `base.py` 只放所有部署形态共享且安全的配置；
- 环境变量通过集中配置模块读取、解析和验证，业务模块不直接读取；
- 未知配置、缺少必要秘密和不安全组合启动即失败；
- `.env.example` 只提供无秘密示例，不提交真实 `.env`；
- `DEBUG` 默认关闭，必须由开发档案显式打开；
- 日志不输出连接串、秘密、绝对附件路径或完整环境变量。

### 7.2 配置档案

| 档案 | 数据库 | 监听与安全 | 存储 |
| --- | --- | --- | --- |
| `local` | 本地 SQLite | 仅允许 loopback，开发与交付参数分开 | 本地私有数据目录 |
| `lan` | PostgreSQL | 受信代理、HTTPS、安全 Cookie | 服务机受控存储卷 |
| `cloud` | PostgreSQL | 域名、HTTPS、代理头和严格安全配置 | 对象存储适配器 |
| `test` | 按测试标记选择 | 固定、隔离、快速失败 | 临时目录或内存替身 |

配置档案不能改变领域规则、权限代码、状态机或 tenant 归属方式。

## 8. 依赖分组与评审

计划使用以下依赖类别，确切版本在骨架提交时由 uv 锁定：

| 分组 | 候选依赖 | 用途与约束 |
| --- | --- | --- |
| 运行时 | `Django` | Web、ORM、迁移、认证、表单和模板 |
| 运行时 | `uvicorn` | ASGI 应用服务器，不使用 `runserver` 交付 |
| 运行时 | `openpyxl` | 以后只读解析 BOM Excel，禁止执行宏 |
| 服务器 | `psycopg` 3.x | PostgreSQL 驱动；实际安装方式兼顾部署和编译环境 |
| 开发 | `ruff` | 格式化和 Lint |
| 开发 | `mypy`、兼容的 Django 类型支持 | 静态检查公共与核心边界 |
| 测试 | `pytest`、`pytest-django`、`pytest-cov` | 单元、集成、验收和覆盖率分析 |
| 安全 | `pip-audit` 或等价工具 | 检查已锁定 Python 依赖的已知漏洞 |

不为读取 `.env`、任务执行、DTO、repository 或日志包装随意增加小型依赖。标准库和 Django 已能满足时优先复用。HTMX 使用固定、自托管的静态文件，不引入 Node 构建链。

## 9. 质量命令契约

工程骨架完成后，开发者和 CI 至少可以从仓库根目录执行：

```text
uv sync --locked --all-groups
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
uv run pytest
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
```

以上是目标命令，不是当前已经配置或通过的结果。首次实现可以按提交批次逐项建立，但 Phase 1 工程基础退出前必须全部可重复运行。

格式化和 Lint 修复命令与只读检查命令分开，CI 只执行不会改文件的检查。测试标记必须注册，未知标记视为失败。

## 10. 测试矩阵

| 层级 | 默认依赖 | 必须证明 |
| --- | --- | --- |
| 领域单元 | 无 Django、无数据库 | 不变量、状态、数量、编号策略和错误类型 |
| 应用单元 | 端口替身 | 用例顺序、权限调用、事务意图和失败结果 |
| SQLite 集成 | SQLite 临时库 | ORM、迁移、约束、本机配置和文件适配器 |
| PostgreSQL 集成 | PostgreSQL 18 | 唯一约束、事务、并发、类型和查询兼容 |
| HTTP/页面 | Django 测试客户端 | 输入、认证、权限、CSRF、错误和基础可访问性 |
| 验收 | 完整应用边界 | `SLICE-001` 的 Given/When/Then 业务结果 |

租户级能力必须包含租户 A 无法读取、猜测、修改、删除、导出或下载租户 B 数据的反向测试。不能只测试列表过滤。

## 11. 实施批次

每个批次完成全部验收项后单独提交。下一批次不得提前混入当前提交。

### F-001：建立 Python 工程和依赖锁

实施状态：已完成（2026-08-23）

建议提交：`build(repo): 建立 Python 工程与锁定依赖`

主要内容：

- 建立 `pyproject.toml`、`.python-version`、`uv.lock` 和最小 `src` 布局；
- 声明运行、开发、测试和安全依赖分组；
- 更新 `.gitignore`，排除虚拟环境、缓存、数据库和本地运行数据；
- 编写依赖评审说明和干净环境安装步骤。

验收：

- 干净目录中 `uv sync --locked --all-groups` 成功；
- `python --version` 和依赖版本符合已接受 ADR；
- 不需要 `.internal/legacy-pms/`；
- 锁文件没有平台无关性错误。

### F-002：建立 Django ASGI 和配置档案

实施状态：已完成（2026-08-23）

建议提交：`feat(platform): 建立 ASGI 与分环境配置`

主要内容：

- 创建 `manage.py`、ASGI 入口、URL 和 settings 包；
- 建立 `local`、`lan`、`cloud`、`test` 配置档案与启动验证；
- 创建无秘密 `.env.example`；
- `local` 强制 loopback 和应用私有数据目录。
- 暂不启用 Django Admin 和认证应用，也不执行数据库迁移；F-004 必须先建立自有用户模型，避免默认用户表进入迁移历史。

验收：

- 本机配置可启动并返回最小响应；
- `python manage.py check` 通过；
- 缺少必要配置或危险组合时启动失败；
- `DEBUG`、主机和 Cookie 安全配置具有测试。
- 启动过程不创建默认 Django 用户表或任何业务数据库结构。

### F-003：建立自动化质量门槛

实施状态：已完成（2026-08-23）

建议提交：`chore(repo): 建立代码质量与持续集成门槛`

主要内容：

- 配置 Ruff、mypy、pytest 和覆盖率边界；
- 添加导入边界检查，禁止领域层依赖 Django；
- 建立 GitHub Actions，使用缓存但以锁文件为准；
- CI 启动 PostgreSQL 18 服务；F-004 首批迁移建立后开始执行双数据库迁移和关键测试；
- 加入依赖安全检查和迁移漂移检查。

验收：

- 第 9 节质量命令在干净环境通过；
- CI 不访问真实数据或未声明网络服务；
- 测试失败能明确指出所属层级和数据库；
- 不使用 `--no-verify` 或忽略错误制造假通过。

### F-004：建立自有用户模型

建议提交：`feat(identity): 建立自有用户与认证基础`

主要内容：

- 在项目第一次迁移中创建基于 `AbstractUser` 的自有用户模型；
- 明确登录标识、启用状态、密码与会话边界；
- 设置 `AUTH_USER_MODEL`，其他模块只通过配置引用用户模型；
- 管理入口只用于受控初始化和诊断。
- 启用认证和 Admin 前先注册自有用户模型；F-004 是第一次允许执行 `migrate` 的批次。

验收：

- 从空 SQLite 和 PostgreSQL 均可执行首批迁移；
- 创建、认证、停用用户行为有测试；
- 日志和错误不包含密码、会话或令牌；
- 迁移历史中不存在先使用默认用户再替换的路径。

### F-005：建立租户与成员关系

建议提交：`feat(tenancy): 建立租户上下文与成员关系`

主要内容：

- 创建 tenant 和 membership；
- 本机初始化默认租户，但仍使用真实 `tenant_id`；
- 定义显式 `TenantContext` 和服务端解析入口；
- 禁止进程全局可变租户状态。

验收：

- 用户只能选择有效且启用的成员关系；
- 客户端伪造 tenant ID 不能改变可信上下文；
- SQLite 与 PostgreSQL 的唯一约束一致；
- 跨租户读取和修改反向测试通过。

### F-006：建立权限与审计基础

建议提交：`feat(authorization): 建立权限策略与审计边界`

主要内容：

- 建立稳定权限代码、角色授权和对象范围策略；
- 将 [角色权限矩阵](../product/role-permission-matrix.md) 作为默认配置来源；
- 建立追加式审计记录端口和 ORM 映射；
- 服务端默认拒绝，页面隐藏不能替代用例权限检查。

验收：

- 有权限、无权限、对象范围不足和跨租户场景均有测试；
- 重要成功与失败动作记录租户、操作者、对象、结果和时间；
- 审计记录不保存秘密或附件正文；
- 权限代码与产品矩阵一致且不在各模块重复定义。

### F-007：建立健康检查、日志和错误边界

建议提交：`feat(platform): 建立健康检查与错误处理基础`

主要内容：

- 分离 `live` 与 `ready`；
- 建立 request ID、结构化日志和稳定错误码；
- 在 HTTP 边界把内部错误转换为安全用户提示；
- 配置环境相关日志级别与敏感字段过滤。

验收：

- `live` 不访问外部依赖；
- `ready` 检查数据库迁移和必要存储；
- 未预期异常记录 request ID，但响应不泄露堆栈或绝对路径；
- 日志敏感字段测试通过。

### F-008：建立附件端口和本地存储适配器

建议提交：`feat(attachments): 建立附件端口与本地存储适配器`

主要内容：

- 只建立附件元数据、状态和存储端口所需最小能力；
- 实现本地临时写入、摘要、原子移动、读取和补偿清理；
- 使用随机存储键，原文件名仅作为元数据；
- 保留对象存储适配点，不安装具体云供应商 SDK。

验收：

- 路径穿越、跨租户读取、超限和失败补偿测试通过；
- 半成品文件不能下载；
- 数据库记录与文件丢失可被对账检查发现；
- 测试使用临时目录，不写入仓库或真实用户目录。

### F-009：验证可重复初始化

建议提交：`test(repo): 验证双数据库初始化与基础边界`

主要内容：

- 从空 SQLite 和 PostgreSQL 运行完整迁移；
- 执行默认租户、管理员和权限初始化；
- 验证重复初始化幂等；
- 固化 Phase 1 工程基础的烟雾测试和开发文档。

验收：

- 空环境安装、迁移、初始化、启动和检查步骤可复制；
- 第二次执行不产生重复租户、角色或权限；
- 所有质量命令通过；
- 工作区不产生未忽略的数据库、日志、附件或缓存文件。

## 12. 提交与回档策略

- 每个 `F-xxx` 是一个建议逻辑提交，不强制为了编号而合并过大的变更；必要时继续拆小；
- 提交使用 [Git 规范](../standard/git-standard.md) 的完整正文；
- 数据库迁移与相应模型、测试和文档在同一提交；
- 每个提交完成后确认工作树干净，再开始下一批次；
- 未推送提交可以由用户决定何时统一推送，但交接记录必须注明本地领先数量；
- 已共享历史使用 `git revert` 回退，不用强制推送改写其他电脑已经获取的提交。

## 13. 进入业务切片前的完成标准

只有同时满足以下条件，才能开始 `SLICE-001` 业务实现：

- F-001 至 F-009 全部完成并有独立提交；
- 从空 SQLite 和 PostgreSQL 均可迁移、初始化和启动；
- Ruff、mypy、pytest、Django checks、迁移漂移和依赖安全检查通过；
- 自有用户模型已在首批迁移中固定；
- tenant、membership、权限和审计边界成立；
- 跨租户读取与修改反向测试通过；
- 本地附件适配器和补偿机制通过测试；
- 开发搭建文档在另一空目录完成复验；
- `SLICE-001` Given/When/Then 验收案例已经接受。

## 14. 风险与复核点

| 风险 | 开工前或实施中的控制 |
| --- | --- |
| Python 3.14 较新，部分工具兼容滞后 | F-001 前查证并试装全部候选依赖，必要时用新 ADR 调整运行时 |
| Django 模型发现与纯领域分层冲突 | F-002 建立最小桥接并用导入边界测试固定 |
| SQLite 测试通过但 PostgreSQL 失败 | F-003 起在 CI 使用 PostgreSQL 18，不推迟到内网阶段 |
| 自有用户模型创建过晚 | F-004 必须先于引用用户的业务模型和迁移 |
| tenant 过滤依赖开发者记忆 | 使用显式上下文、查询入口、约束和反向测试多层防护 |
| 基础设施批次过大 | 按 F-001 至 F-009 独立提交，超出单一评审目标时继续拆分 |
| 为未来模块创建无价值空壳 | 只创建当批需要的包，业务模块进入切片时再建 |
| 教材级注释演变为语法复述 | 评审按注释规范检查背景、原因、边界和副作用 |

## 15. 评审结论

用户已于 2026-08-23 确认继续下一步，本计划转为“已接受”。完成 Phase 0 退出审查后，可以按照 F-001 至 F-009 的顺序进入工程实施。

接受计划不等于一次性授权所有后续重大决策。实施中如果需要改变已接受 ADR、加入未评审的生产依赖或扩大产品范围，必须暂停对应批次并先更新文档。
