# 核心状态机

状态：已接受（SLICE-001 范围）

## 1. 通用规则

- 状态只能通过命名业务动作迁移，禁止任意修改状态字段。
- 服务端在事务内检查当前状态、权限、业务前置条件和租户边界。
- 成功迁移记录操作者、时间和变更摘要；失败或拒绝记录必要审计结果。
- 取消不是物理删除；已经被下游引用的对象不能通过取消破坏历史一致性。
- 状态代码使用稳定英文大写值，中文只作为界面显示。

## 2. 项目状态机

```text
DRAFT ──activate──> ACTIVE ──close──> CLOSED
  │                   │
  └────cancel─────────┴────cancel──> CANCELLED
```

| 动作 | 起始状态 | 目标状态 | 前置条件 | 主要副作用 |
| --- | --- | --- | --- | --- |
| `activate_project` | `DRAFT` | `ACTIVE` | 项目编号、客户和必填日期有效 | 项目允许导入 BOM 和创建业务记录 |
| `close_project` | `ACTIVE` | `CLOSED` | 不存在阻止关闭的活动业务；具体规则随下游模块补充 | 禁止新增 BOM、投产和请购 |
| `cancel_project` | `DRAFT` | `CANCELLED` | 无已发布 BOM 或下游正式记录 | 保存取消原因并转为只读 |
| `cancel_project` | `ACTIVE` | `CANCELLED` | P0 仅在无不可撤销下游记录时允许 | 保存取消原因并转为只读 |

`CLOSED` 重开规则尚未定义，P0 不提供重开动作。

## 3. BOM 版本状态机

```text
DRAFT ──publish──> PUBLISHED ──new version published──> SUPERSEDED
  │                     │
  └────cancel────────────┴────cancel if allowed───────> CANCELLED
```

| 动作 | 起始状态 | 目标状态 | 前置条件 | 主要副作用 |
| --- | --- | --- | --- | --- |
| `publish_bom` | `DRAFT` | `PUBLISHED` | 全部阻断错误已解决；物料已确认；项目为 `ACTIVE` | 固化版本内容、来源附件和发布人 |
| `publish_bom` | 旧 `PUBLISHED` | `SUPERSEDED` | 同项目的新版本成功发布 | 旧版本仍可查询并供历史批次引用 |
| `cancel_bom` | `DRAFT` | `CANCELLED` | 无正式下游引用 | 保存原因，禁止继续编辑或发布 |
| `cancel_bom` | `PUBLISHED` | `CANCELLED` | 未被已发布投产批次引用 | 保存原因，不删除版本内容 |

发布动作要么同时完成新版本发布与旧版本替代，要么全部回滚。校验失败只是草稿的校验结果，不是单独业务状态。

## 4. 投产批次状态机

```text
DRAFT ──release──> RELEASED
  │                    │
  └────cancel───────────┴────cancel if allowed──> CANCELLED
```

| 动作 | 起始状态 | 目标状态 | 前置条件 | 主要副作用 |
| --- | --- | --- | --- | --- |
| `release_production` | `DRAFT` | `RELEASED` | 项目为 `ACTIVE`；BOM 为 `PUBLISHED`；投产台数为正整数 | 固化 BOM 版本、投产台数和需求数量 |
| `cancel_production` | `DRAFT` | `CANCELLED` | 始终允许授权人员取消 | 保存原因并转为只读 |
| `cancel_production` | `RELEASED` | `CANCELLED` | 不存在未取消正式请购 | 恢复可用业务边界但不删除历史 |

新 BOM 发布不改变已经 `RELEASED` 的投产批次。需要采用新 BOM 时必须创建新投产批次。

## 5. 生产请购状态机

```text
DRAFT ──submit──> SUBMITTED ──cancel if allowed──> CANCELLED
  └────cancel────────────────────────────────────> CANCELLED
```

| 动作 | 起始状态 | 目标状态 | 前置条件 | 主要副作用 |
| --- | --- | --- | --- | --- |
| `submit_purchase_request` | `DRAFT` | `SUBMITTED` | 来源投产已发布；行有效；剩余可请购数量充足；权限有效 | 原子生成请购号、固化行快照并写审计 |
| `cancel_purchase_request` | `DRAFT` | `CANCELLED` | 授权人员提供原因 | 草稿转为只读 |
| `cancel_purchase_request` | `SUBMITTED` | `CANCELLED` | 尚未形成采购订单等不可撤销下游记录 | 释放相应可请购数量并保留完整历史 |

P0-B 不支持分批请购。采购订单模块建立后，履约状态采用计算或投影，不直接重写已经提交的请购事实。

## 6. 状态与时间字段

每个核心对象至少记录：

- `status`：当前稳定状态代码；
- `created_at`、`created_by`：创建时间和操作者；
- `updated_at`、`updated_by`：最近修改时间和操作者；
- 对关键迁移保存专用时间与操作者，例如 `published_at`、`submitted_at`；
- 取消时保存 `cancelled_at`、`cancelled_by` 和 `cancellation_reason`。

时间使用带时区时间保存，按租户时区显示。不得只保存类似 `yyyymmdd-hhmmss` 的字符串代替审计字段。

## 7. 测试不变量

- 不能从终态通过普通编辑回到草稿。
- 重复执行同一幂等动作不会重复产生下游记录。
- 无权限、跨租户或前置状态不符时，状态和业务数据均不改变。
- 状态迁移和其副作用在同一事务内成功或失败。
- 数据重启、迁移和备份恢复后状态及历史引用保持一致。
- 前端提交的目标状态不可信；服务端根据动作和当前状态决定目标状态。

