# 模块边界

状态：草案

## 1. 结论

PMS 采用模块化单体。所有模块运行在一个可部署应用中，但拥有明确职责、依赖方向、应用服务和数据所有权。模块不能通过任意 ORM 查询越过边界。

## 2. 首版模块

| 模块 | 拥有的能力与数据 | 可以依赖 |
| --- | --- | --- |
| `tenancy` | 租户、成员关系、租户上下文 | `identity` 的用户身份接口 |
| `identity` | 用户、密码、会话、基础认证 | 平台配置；不依赖业务模块 |
| `authorization` | 权限代码、角色授权和对象范围策略 | `identity`、`tenancy` |
| `audit` | 追加式审计记录与查询 | 身份和租户标识；不回调业务模块 |
| `master_data` | 客户、物料、单位、分类和零件属性 | `tenancy`、`audit` 接口 |
| `projects` | 项目及其生命周期 | `master_data` 客户只读接口 |
| `attachments` | 附件元数据、版本、存储和下载 | `tenancy`、`authorization`、`audit` |
| `bom` | BOM 导入、校验、版本、BOM 行和差异 | `projects`、`master_data`、`attachments` 接口 |
| `production` | 投产批次和投产需求 | `projects`、`bom`、`master_data` 只读接口 |
| `procurement` | 生产请购、编号和防重复 | `production`、`master_data` 只读接口 |
| `platform` | 配置、健康检查、启动和基础技术适配 | 不包含业务规则 |

`platform` 是工程层名称，不是业务模块；不得把无法归类的代码都放入其中。

## 3. 依赖方向

```text
identity ───┐
            ├──> tenancy ──> authorization
            │         └────> audit
            │
master_data ──> projects ──> bom ──> production ──> procurement
                         └────┬─────┘
                              └──> attachments（通过应用接口）
```

上游模块不能反向导入下游实现。例如 `bom` 不得导入 `procurement`，项目页面需要展示请购摘要时应由查询服务组合结果，而不是让项目模型读取请购表。

## 4. 模块内部层次

建议每个有业务规则的模块按以下结构组织：

```text
src/pms/<module>/
├── domain/          # 实体、值对象、状态、规则、领域错误；不导入 Django
├── application/     # 用例、权限编排、事务边界、端口和 DTO
├── infrastructure/  # Django ORM、文件、外部系统和端口实现
├── presentation/    # Django views、forms、urls、templates
├── migrations/      # 模块拥有的数据迁移
└── apps.py           # Django 应用声明
```

小模块可以合并物理文件，但依赖方向不能改变：

```text
presentation → application → domain
                         ↑
               infrastructure 实现端口
```

领域层不导入 Django、ORM、HTTP、SQLite、PostgreSQL、文件路径或云 SDK。应用层决定用例和事务，基础设施层实现持久化与外部交互。

## 5. 数据所有权

- 每张业务表只有一个模块拥有迁移和写入权。
- 其他模块不能直接更新该表，必须调用拥有模块的应用服务。
- 跨模块读取优先使用稳定查询接口或只读 DTO。
- 外键可以表达稳定的一致性关系，但不能成为绕过应用服务写入对方表的理由。
- 报表需要跨模块数据时使用专门查询层；查询层只读，不能承载状态迁移。
- 附件二进制由 `attachments` 管理，业务模块拥有附件与业务对象的关联记录。

## 6. 用例与事务

每个改变状态的入口映射到一个命名用例，例如：

- `CreateProject`、`ActivateProject`；
- `ImportBom`、`PublishBomVersion`；
- `ReleaseProduction`；
- `CreatePurchaseRequest`、`SubmitPurchaseRequest`。

用例负责：

1. 建立租户与操作者上下文；
2. 执行权限检查；
3. 加载领域所需数据；
4. 调用领域规则决定结果；
5. 在短事务中保存状态和审计；
6. 事务提交后触发非关键派生动作。

不得把核心业务流程隐藏在 Django signal、模型 `save()` 副作用、模板或 JavaScript 中。Signal 只允许处理框架级、非关键且有测试的辅助动作。

## 7. 跨模块事件

P0 使用同进程、事务提交后的显式内部事件，不引入消息队列。

- 事件名称使用已经发生的事实，例如 `BomVersionPublished`。
- 关键一致性结果必须在原事务内完成，不能依赖异步事件最终补救。
- 事件处理器必须幂等，并记录失败；不得形成循环依赖。
- 真正需要跨进程重试时再通过 ADR 引入 outbox 和任务队列。

## 8. 禁止模式

- 视图直接编排多个模型保存并决定状态；
- 模块 A 任意导入模块 B 的 ORM 模型后写表；
- 使用全局可变租户变量；
- 使用 Django signal 隐式生成请购或编号；
- 为每个 ORM 模型机械创建无价值的 repository 接口；
- 把共享工具目录变成没有职责边界的代码仓库；
- 为未来猜测的微服务提前设计网络 RPC。

## 9. 验证方式

- 增加导入边界检查，禁止 `domain` 导入 Django 和基础设施。
- 模块应用服务具有单元或集成测试，视图测试不替代领域测试。
- 对每个状态用例测试权限、租户、事务回滚和幂等性。
- 代码评审检查数据所有权和反向依赖。
- 如果某模块必须长期绕过边界，先更新本文或新增 ADR，而不是添加例外导入。
