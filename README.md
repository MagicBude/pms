# PMS

PMS 是一套面向生产型企业的项目、订单、采购、生产、库存、付款、发票和经营分析系统。

本仓库正在进行从 Excel/VBA 文件型系统到浏览器访问的模块化业务系统的重新设计。计划以同一套代码支持三个部署阶段：

1. 本机单用户：localhost、SQLite、本地附件目录。
2. 公司内网：统一服务机、多人访问、集中数据库和备份。
3. 商业云端：域名、PostgreSQL、多租户、授权或订阅。

当前处于 **Phase 2 业务签收与 Phase 3A 第一批并行推进**。`SLICE-001` 本机技术候选已经能够在
浏览器中从登录走到已提交生产请购，并完成安装、升级、备份、恢复和 45 条案例退出追踪。客户与
供应商真实原始数据已经版本化映射并在隔离库完成首次/重复导入；一个真实 10 行项目/BOM/投产/
请购切片也已完成隔离对账，业务含义仍待使用者在本机复核页签收。

## 开发环境

项目使用 Python 3.14、uv 和跨平台依赖锁。首次搭建执行：

```powershell
uv python install 3.14.7
uv sync --locked --all-groups
```

完整说明见 [开发环境搭建](docs/development/setup.md)和 [依赖评审](docs/development/dependency-review.md)。
完成迁移和初始化后，使用经过前置检查与 ready 等待的
[本机正式启动器](docs/deployment/local-launcher.md)打开工作台；首版页面操作顺序见
[本机工作台操作路径](docs/product/local-workbench.md)。

## Windows 双击使用

在已经安装 uv 的 Windows 电脑上：

1. 新电脑第一次使用，双击仓库根目录的 `PMS-首次安装.bat`；它会安装锁定的 Python、同步
   依赖、迁移数据库、安全提示设置 admin 密码，并在成功后打开 PMS。
2. 以后日常使用只需双击 `PMS-启动.bat`，保持弹出的终端窗口打开；结束时在窗口中按
   `Ctrl+C`。

两个 BAT 都按自身位置寻找仓库和 `data/`，不写死盘符或 `D:\Github\pms`。换电脑时仍需先
安装 uv，并完整复制或克隆仓库；需要保留业务数据时还必须按备份恢复文档迁移 `data/`，不能只
复制源码。

旧 PMS 批量迁移不需要逐条手工录入：双击 `PMS-提取旧数据.bat` 可以把白名单内的核心
`.xlsb` 数据库只读提取到 Git 忽略的 `.internal/migration/`。原始包含真实敏感数据，只供后续
映射、导入和对账；换电脑保存新 PMS 的完整数据仍应使用正式备份与恢复。

已经生成客户/供应商规范包后，关闭 PMS 并双击 `PMS-导入旧主数据.bat`：脚本会迁移数据库、
先创建完整备份，再幂等导入 9 个客户和 115 个供应商并生成无敏感值对账报告。成功后重新双击
`PMS-启动.bat`，可在左侧“客户”和“供应商”页面查看。

已提交的生产请购现在可以直接在详情页录入多家供应商报价、核算含税/未税金额并确定供应商；
不同币种分别汇总，重选会保留历史版本。该能力是采购侧价格核算，整机成本与客户报价仍在后续
`SAL-001` 批次。

已有本机数据在拉取本批代码后，先停止 PMS，在仓库终端执行 `uv sync --locked --all-groups`、
`uv run python manage.py migrate --noinput` 和 `uv run python manage.py initialize_pms`。最后一条对既有
管理员不要求重新输入密码，只会幂等补齐新增报价权限；完成后再双击 `PMS-启动.bat`。

## 阅读顺序

1. [当前项目状态](PROJECT_STATUS.md)
2. [项目蓝图](docs/product/blueprint.md)
3. [文档导航](docs/README.md)
4. [新系统功能目录](docs/product/feature-catalog.md)
5. [工程规范索引](docs/standard/README.md)
6. [AI 与跨电脑交接说明](docs/development/handoff.md)
7. [Agent 协作规则](AGENTS.md)

## 连续工作记录

- [项目状态](PROJECT_STATUS.md)：当前阶段、完成项、下一步和阻塞，是唯一当前状态页。
- [项目路线图](docs/development/roadmap.md)：分阶段计划与退出条件。
- [工作日志](docs/development/work-log.md)：按时间追加的执行事实和验证结果。
- [变更记录](CHANGELOG.md)：仓库对使用者产生的可见变化。
- [旧系统迁移追踪](docs/migration/legacy-to-new-traceability.md)：旧能力到新功能、规则和验收的映射。

## 旧系统

旧 Excel/VBA 系统位于本机的 `.internal/legacy-pms/`。它只作为需求来源、业务规则参考、数据迁移样本和验收基线，不属于新系统源码。

`.internal/legacy-pms/` 已被 Git 忽略。新系统必须能够在该目录不存在时独立构建、测试和运行。

## 当前仓库边界

- `docs/`：产品、架构、部署、开发、安全、迁移和规范文档。
- `.internal/`：本机私有参考材料，除说明文件外均不提交。
- `src/pms/`：模块化单体源码，包含领域、应用、基础设施、Web 和迁移入口。
- `tests/`：单元、SQLite/PostgreSQL 集成、浏览器工作流和安全回归测试。
