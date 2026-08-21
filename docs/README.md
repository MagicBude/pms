# PMS 文档中心

文档按职责分类。每一项重要决定都应进入对应目录，避免把产品、架构、编码规则和部署操作混在同一份文档中。

## 分类

- [product](product/README.md)：产品愿景、范围、用户、业务能力、路线图和验收标准。
- [architecture](architecture/README.md)：系统上下文、模块边界、数据设计、接口和 ADR。
- [deployment](deployment/README.md)：本机、内网、云端部署以及备份和升级。
- [development](development/README.md)：开发环境、工作流、调试和发布过程。
- [standard](standard/README.md)：代码、注释、命名、测试、日志、安全和 Git 规范。
- [security](security/README.md)：身份、权限、多租户隔离、许可证和威胁模型。
- [migration](migration/README.md)：旧系统功能映射、数据清洗、迁移与验收。

## 文档状态

文档应在标题下使用下列状态之一：

- `草案`：内容正在形成，不能作为稳定契约。
- `评审中`：等待业务或技术确认。
- `已接受`：当前实现必须遵守。
- `已废弃`：保留历史原因，但不能用于新实现。

## 文档原则

- 业务事实和技术方案分开表达。
- 已确认内容、假设、待决策问题必须明确区分。
- 重大决策必须记录原因和后果，不能只记录最终结论。
- 文档与实现发生冲突时，先判断哪一方过期，再同时修正。
- 从旧系统提炼出的规则应给出来源位置，但不得复制敏感业务数据到仓库。

## 连续工作入口

- 当前事实：[项目状态](../PROJECT_STATUS.md)
- 阶段计划：[路线图](development/roadmap.md)
- 执行历史：[工作日志](development/work-log.md)
- 跨电脑/跨 AI：[交接说明](development/handoff.md)
- 产品范围：[功能目录](product/feature-catalog.md)
- 旧新映射：[迁移追踪矩阵](migration/legacy-to-new-traceability.md)
