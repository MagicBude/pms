# Phase 1 退出审查

状态：未通过（F-010 已关闭，F-011 本地候选待远端验证）

## 1. 审查结论

F-001 至 F-009 已全部完成，工程计划定义的 `SLICE-001` 编码前检查项也均有本地与
PostgreSQL 18 证据。但是，已接受的[项目蓝图](../product/blueprint.md)和
[ADR-0004](../architecture/adr/ADR-0004-deployment-profiles.md)仍要求 Phase 1 提供本机启动器；
[路线图](roadmap.md)还要求建立本机备份恢复能力。F-010 已由 GitHub Actions 运行
`32648596251` 关闭；F-011 启动器已形成并通过本地候选验证，但仍待远端 Linux 文件锁和完整
回归。因此本审查不把 Phase 1 标记为已通过，也不提前开始业务代码。

本结论采用较严格的上位文档门槛，避免只依据 F-001 至 F-009 的编号完成情况忽略可交付性。
启动器的最终桌面打包形式仍可晚于源码级启动器决定，但“检查、启动 Uvicorn、等待 ready、
打开浏览器”的可靠入口必须先存在。

## 2. 工程计划进入业务切片检查

| 检查项 | 证据 | 结果 |
| --- | --- | --- |
| F-001 至 F-009 全部完成并有独立提交 | 工程计划、工作日志、Git 历史 | 通过 |
| 空 SQLite 可迁移、初始化和启动 | F-009 本地临时空库、Uvicorn 和 ready 证据 | 通过 |
| 空 PostgreSQL 18 可迁移、初始化和启动 | GitHub `Quality` 运行 `32644256715` | 通过 |
| Ruff、mypy、pytest、Django checks、迁移漂移和依赖审计 | 本地质量链与 GitHub `Quality` | 通过 |
| 自有用户模型在首批迁移固定 | `identity.0001_initial`，不存在 `auth_user` | 通过 |
| tenant、membership、权限和审计边界 | F-005、F-006、F-009 及对应反向测试 | 通过 |
| 跨租户读取与修改反向测试 | tenancy、authorization、attachments 集成测试 | 通过 |
| 本地附件适配器和失败补偿 | F-008 实现、对账与双数据库验证 | 通过 |
| 开发搭建在另一空目录复验 | 本审查第 3 节 | 通过 |
| `SLICE-001` Given/When/Then 已接受 | `slice-001-acceptance-cases.md` | 通过 |

这张表说明领域切片所需的代码底座已经成立；它不覆盖蓝图中的本机交付包装与数据恢复门槛。

## 3. 另一空目录复验

2026-08-23 从本地提交 `48d1e3b` 克隆到被 Git 忽略的全新隔离目录，未复用原仓库 `.venv`
或 `data/`，并按开发文档执行：

1. `uv python install 3.14.7`；
2. `uv sync --locked --all-groups`，新建环境并安装 56 个锁定包；
3. Ruff format、Ruff Lint、严格 mypy 和 95 个 pytest；
4. 首次迁移、首次初始化、无变化重复迁移和无密码重复初始化；
5. Django system check、迁移漂移和 pip-audit；
6. Uvicorn 监听 loopback，访问根入口和 `/health/ready`。

结果：全部命令通过，ready 报告 database、migrations、attachment_storage 均为 `ok`；复验
仓库 `git status --porcelain --untracked-files=all` 为空。生成的 `.venv`、SQLite、附件目录和
检查缓存都处于预期忽略范围，没有新系统文件依赖 `.internal/legacy-pms/`。

## 4. Phase 1 全量范围检查

| Phase 1 能力 | 当前状态 | 结论 |
| --- | --- | --- |
| 模块化单体工程骨架 | identity、tenancy、authorization、audit、attachments、platform 已按边界建立 | 通过 |
| 质量工具与 CI | SQLite 本地和 PostgreSQL 18 `Quality` 持续验证 | 通过 |
| 配置、日志、健康检查和迁移 | 四套档案、结构化日志、live/ready 和版本化迁移已建立 | 通过 |
| tenant、user、membership、role、audit | F-004 至 F-006、F-009 已建立并测试 | 通过 |
| 本机显式初始化 | `initialize_pms` 可安全且幂等执行 | 通过 |
| localhost 正式服务器 | `launch_local` 从 settings 建立 Uvicorn 单 worker，禁止 DEBUG 和 reload | 本地候选，待远端 CI |
| 本机启动器 | 已建立迁移/初始化前置检查、操作系统实例锁、ready 等待和浏览器单次打开 | 本地候选，待远端 CI |
| 本机备份与恢复 | 一致性备份、清单、离线验证、空目录恢复及恢复启动已通过运行 `32648596251` | 通过 |

## 5. 补救批次提案

### F-010：建立本机备份与恢复基础

当前进度：已按本节范围形成候选并通过本地质量链与恢复演练；GitHub `Quality` 运行
`32648596251` 验证成功，F-010 已关闭。

建议提交：`feat(platform): 建立本机备份与恢复基础`

- 仅支持 `local` 档案，拒绝把备份写回当前数据目录；
- 为 SQLite、附件和非秘密版本信息生成一个带摘要的备份集；
- 恢复只写入明确的空目标目录，不覆盖现有运行数据；
- 恢复后验证数据库迁移、默认数据、附件清单和摘要；
- 建立备份篡改、缺失附件、非空目标和路径边界反向测试；
- 不把备份文件、真实数据或秘密纳入 Git。

这批先实现本机工程基础。`AC-S001-042` 对真实项目/BOM/请购数据的代表性恢复验收，要在
`SLICE-001` 数据模型和业务链完成后补充。

### F-011：建立本机正式启动器基础

当前进度：已按本节范围形成候选；Windows 真实启动、ready、重复实例、锁释放和 Ctrl+C 正常
停止均已验证，等待候选提交的 GitHub `Quality` 验证后关闭。

建议提交：`feat(platform): 建立本机服务启动器`

- 从 local settings 获取 loopback 地址和受控端口，不让参数与配置分离漂移；
- 启动前检查数据目录、迁移、初始化状态和同一数据目录重复实例；
- 使用 Uvicorn 单进程、单 worker，禁止 `DEBUG` 和自动重载；
- 启动后轮询 ready，成功才打开默认浏览器，失败时输出安全可操作提示；
- 测试端口占用、迁移待执行、未初始化、ready 超时和浏览器只打开一次；
- 首版提供源码级跨平台入口，桌面图标、安装包和运行时封装另行决策。

### F-012：重新执行 Phase 1 退出审查

- 在另一空目录验证安装、初始化、启动、备份、恢复和完整质量链；
- 确认恢复目录可启动且 Git 工作区没有未忽略运行文件；
- 关闭 Phase 1 后再把项目状态切换到 `SLICE-001` 业务实现。

## 6. 不阻塞补救批次的后续事项

- 最终桌面打包技术和 Python 运行时交付方式；
- 内网 PostgreSQL 定时备份与服务监管；
- 云端对象存储、时间点恢复和商业授权；
- `AC-S001-043` 所需的业务确认脱敏案例。

这些事项不属于当前两个工程缺口，但必须在对应部署或最终验收阶段关闭。
