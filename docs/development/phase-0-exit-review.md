# Phase 0 退出审查

状态：已通过

## 1. 结论

用户已于 2026-08-23 接受 [SLICE-001 验收案例](../product/slice-001-acceptance-cases.md)。Phase 0 所需的产品范围、业务基线、权限、状态、验收、架构、工程规范和实施计划均已具备，本审查通过。

项目进入 Phase 1。工程实施必须从 F-001 开始，不能因为 Phase 0 通过而跳过依赖查证、独立验证和逐任务提交。

## 2. 退出条件检查

| 检查项 | 证据 | 结果 |
| --- | --- | --- |
| 旧系统与新仓库隔离 | `.internal/legacy-pms/`、`.gitignore`、项目状态 | 通过 |
| 产品方向与三阶段边界 | [项目蓝图](../product/blueprint.md) | 通过 |
| P0 范围与首个切片 | [P0 范围评审](../product/p0-scope-review.md)、[SLICE-001](../product/first-slice-project-bom-procurement.md) | 通过 |
| 稳定业务术语 | [业务术语表](../product/glossary.md) | 通过 |
| 核心数据与业务规则 | SLICE-001 第 7、8、11 节、[业务规则基线](../product/business-rules.md) | 通过，切片外规则继续评审 |
| 角色、权限和对象范围 | [角色与权限矩阵](../product/role-permission-matrix.md) | 通过 |
| 核心状态机 | [核心状态机](../product/state-models.md) | 通过 |
| Given/When/Then 验收案例 | [SLICE-001 验收案例](../product/slice-001-acceptance-cases.md) | 通过 |
| 系统上下文与模块边界 | [系统上下文](../architecture/system-context.md)、[模块边界](../architecture/module-boundaries.md) | 通过 |
| 关键技术决策 | [ADR 索引](../architecture/adr/README.md) | 通过 |
| 安全、租户与附件边界 | 安全规范、权限矩阵、ADR-0002、ADR-0003 | 通过，相关实现批次仍需专项测试 |
| 工程与注释规范 | [工程规范索引](../standard/README.md) | 通过 |
| 可实施工程计划 | [工程骨架实施计划](engineering-foundation-plan.md) | 通过 |
| 连续工作与回档机制 | 项目状态、工作日志、交接说明、Git 规范 | 通过 |

## 3. 不阻塞 Phase 1 工程基础的待办

以下事项仍重要，但不影响从 F-001 建立通用工程基础：

- 访谈确认首个切片之外旧功能的真实使用频率和例外；
- 本机启动器的最终打包形式；
- 云端对象存储供应商；
- 私有化许可证设备绑定和离线策略；
- 旧系统全量数据保留与脱敏策略；
- `AC-S001-043` 所需的业务确认脱敏案例。

这些事项在进入对应实现或发布批次前必须完成，不能因为本次不阻塞而永久搁置。

## 4. Phase 1 开始前的即时检查

即使本审查转为“已通过”，执行 F-001 前仍要：

1. 确认工作树干净，并记录本地与远程提交差异；
2. 查证 Python 3.14、Django 5.2 LTS 和全部候选依赖的当前兼容性；
3. 评估每个首次加入依赖的用途、替代方案、维护状态、许可证和安全影响；
4. 确认没有真实数据、数据库、附件、密钥或旧系统文件进入暂存区；
5. 按 [工程骨架实施计划](engineering-foundation-plan.md) 只执行 F-001，不提前混入 F-002。

## 5. 复审结果

- 验收案例已转为“已接受”；
- 本审查已转为“已通过”；
- 项目状态和路线图进入 Phase 1；
- 本批次创建独立 Git 提交作为 Phase 0 基线点；
- 未创建 Git tag。是否打标签或发布版本仍需用户另行决定。
