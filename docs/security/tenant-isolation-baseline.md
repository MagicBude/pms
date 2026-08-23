# 租户隔离基线

状态：F-005 已验证基线

## 1. 核心模型

- `Tenant` 表示独立拥有业务数据和成员权限边界的企业；
- `Membership` 表示一个全局用户加入一个租户的关系；
- 同一用户可以加入多个租户，但数据库禁止同一用户在同一租户出现重复 membership；
- 停用 membership 只影响该用户在对应租户中的访问；停用 tenant 会拒绝其全部成员解析上下文。

tenant 与 membership 均使用 UUIDv7 主键。`Tenant.code` 是全局唯一、稳定且适合配置和诊断的短代码，不作为权限凭据；知道 code、tenant ID 或 membership ID 都不能获得访问权。

## 2. 可信上下文

`TenantContext` 同时保存：

- 已认证的 `user_id`；
- 服务端确认属于该用户的 `membership_id`；
- 从 membership 数据库关系解析出的 `tenant_id`。

解析入口不接受客户端声明的 `tenant_id`。客户端可以选择 membership，但服务端必须使用当前 Django 会话用户、membership 主键、membership 启用状态和 tenant 启用状态做联合查询。查询失败统一返回“当前成员关系不可用”，不能泄露其他用户或租户是否存在。

## 3. 数据库防线

- `UNIQUE (tenant_id, user_id)` 防止重复成员关系；
- membership 删除 tenant 和 user 时使用 `PROTECT`，避免身份或租户被级联删除而破坏历史；
- 查询索引覆盖 `(user_id, is_active)`，支持会话用户枚举其有效成员关系；
- SQLite 与 PostgreSQL 18 使用同一 Django migration；
- 后续每个租户级业务表仍必须显式包含 `tenant_id`，不能只依赖 membership。

## 4. 当前非目标

- 默认租户和管理员初始化，属于 F-009；
- 成员角色、权限和对象范围，属于 F-006；
- HTTP 中间件或租户选择页面；
- 平台运营人员跨租户访问；
- tenant 数据导出、删除和匿名化流程。

当前模块只建立可信上下文和持久化边界，不因本机版只有一个企业而省略 tenant。
