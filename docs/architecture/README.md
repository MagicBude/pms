# Architecture

状态：草案

本目录记录系统结构以及跨模块技术决策。

计划包含：

- 系统上下文和外部参与者；
- 模块化单体边界；
- 领域模型和模块依赖；
- SQLite 与 PostgreSQL 兼容策略；
- 多租户数据隔离；
- API、事件和后台任务边界；
- 附件存储抽象；
- 性能、可靠性和可观测性要求；
- Architecture Decision Records（ADR）。

当前总览见 [项目蓝图](../product/blueprint.md)。技术栈尚未正式决定，后续应通过 ADR 确认。

## ADR 命名

```text
ADR-0001-short-title.md
ADR-0002-short-title.md
```

每份 ADR 至少包含：状态、背景、决策、备选方案、后果和复审条件。

