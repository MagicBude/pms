# PMS

PMS 是一套面向生产型企业的项目、订单、采购、生产、库存、付款、发票和经营分析系统。

本仓库正在进行从 Excel/VBA 文件型系统到浏览器访问的模块化业务系统的重新设计。计划以同一套代码支持三个部署阶段：

1. 本机单用户：localhost、SQLite、本地附件目录。
2. 公司内网：统一服务机、多人访问、集中数据库和备份。
3. 商业云端：域名、PostgreSQL、多租户、授权或订阅。

当前阶段是 **Phase 0：项目蓝图和工程规范**。在关键业务边界、架构决策和验收标准明确前，不开始大规模业务编码。

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
- `src/`、`web/`、`tests/` 等源码目录将在技术选型 ADR 通过后创建。
