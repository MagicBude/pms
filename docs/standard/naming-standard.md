# 命名规范

状态：已接受

## 1. 原则

- 名称表达业务含义，不表达临时实现细节。
- 一个业务概念只使用一个标准名称；同义词进入业务术语表统一。
- 避免 `data`、`info`、`item`、`obj`、`temp`、`manager`、`helper` 等范围不清的名称。
- 缩写只使用团队确认的常见缩写，例如 ID、API、URL、BOM；首次出现在文档中要解释。
- 名称不包含类型噪声，例如 `orderListArray`；类型系统已经表达容器类型。

## 2. 语言

- 代码标识符、数据库对象、API 字段、目录和源码文件使用英文。
- 业务界面、业务文档和教学性说明优先使用中文。
- 拼音不作为英文业务术语的替代。找不到准确英文时先更新术语表。

## 3. 代码命名

具体大小写遵循所选语言社区标准，一般采用：

- 类、类型、枚举：`PascalCase`；
- 函数、方法、变量：Python 使用 `snake_case`，TypeScript 使用 `camelCase`；
- 常量：`UPPER_SNAKE_CASE`；
- Python 模块：`snake_case.py`；
- TypeScript 文件：选型 ADR 决定统一使用 `kebab-case.ts` 或其他风格，项目内不得混用。

### 3.1 用例

使用动词加业务对象：

```text
CreateProject
ConfirmPurchaseOrder
ReceiveMaterial
ApprovePaymentRequest
```

### 3.2 查询

查询名称体现返回语义：

```text
get_order
find_orders
list_payments
count_open_projects
```

- `get`：期望唯一对象，不存在通常为明确错误。
- `find`：可能不存在或返回筛选集合。
- `list`：返回可分页集合。
- `count`、`exists`：返回明确的聚合结果。

### 3.3 布尔值

使用 `is_`、`has_`、`can_`、`should_`：

```text
is_confirmed
has_permission
can_be_cancelled
should_send_notification
```

避免否定布尔名和双重否定。

## 4. 领域对象

- 实体使用单数名词：`PurchaseOrder`。
- 值对象使用含义明确的名词：`Money`、`OrderNumber`、`DateRange`。
- 领域事件使用已经发生的过去式：`PurchaseOrderConfirmed`。
- 命令使用祈使动作：`ConfirmPurchaseOrder`。
- 错误类型说明失败原因：`OrderAlreadyConfirmedError`。
- 仓储接口以领域聚合命名，不以数据表命名：`PurchaseOrderRepository`。

## 5. 数据库命名

- 表名、列名、索引和约束使用 `snake_case`。
- 表名统一采用复数或单数，由数据库 ADR 决定；确定后全库一致。
- 外键列使用 `<entity>_id`，例如 `tenant_id`。
- 时间列使用明确后缀，例如 `created_at`、`confirmed_at`。
- 布尔列使用 `is_` 或 `has_`。
- 约束和索引使用可诊断名称：

```text
uq_purchase_orders_tenant_number
fk_order_lines_purchase_order
ix_payments_tenant_status_created_at
```

- 业务编号与数据库主键分离，例如 `id` 与 `order_number`。

## 6. API 命名

- URL 使用名词资源和复数形式，例如 `/purchase-orders`。
- JSON 字段使用技术栈统一风格，优先 `snake_case` 或 `camelCase` 二选一，并在边界集中转换。
- 动作无法自然表达为资源状态变化时，使用明确子资源或动作端点，例如 `/purchase-orders/{id}/confirmation`。
- 错误码稳定且机器可读，例如 `ORDER_ALREADY_CONFIRMED`。
- API 不暴露内部表名、堆栈、绝对路径和 ORM 字段。

## 7. 文件和目录

- 目录按业务能力或职责命名，不按开发者姓名命名。
- 文档文件使用小写 `kebab-case.md`。
- 数据迁移和 ADR 使用编号前缀保证顺序。
- 测试文件名称对应被测行为或模块，不使用 `test1`、`new_test`。

## 8. 禁止命名

- 无意义序号：`module1`、`sheet2`、`function3`；
- 临时状态：`new_order2`、`final_final`；
- 个人姓名：`zhangsan_fix`；
- 含义不明缩写：`clm_exp`；
- 把实现当业务：`SqlOrderService`，除非它确实是基础设施适配器；
- 易误解词：把“客户订单”和“采购订单”都简称为 `order`。

## 9. 重命名纪律

发现名称错误时应同时更新代码、测试、文档、API 契约和迁移。公开接口重命名需要兼容期和迁移说明，不能只在一处改名造成同一概念多套名称。

