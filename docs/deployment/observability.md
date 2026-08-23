# 健康检查、运行日志与错误响应

状态：已验证（SQLite 本地与 PostgreSQL 18 CI）

## 1. 健康端点

PMS 提供两个无需认证、只返回非敏感状态的端点：

| 路径 | 成功状态 | 目的 | 外部依赖 |
| --- | --- | --- | --- |
| `/health/live` | `200` | 证明 Web 进程仍能接收并响应 HTTP | 不访问数据库或文件存储 |
| `/health/ready` | `200` | 证明实例可以安全承接正常请求 | 数据库连接、完整迁移历史、已配置的必要存储 |

两个响应都禁止缓存，避免代理使用过期健康状态。

`live` 失败时，进程管理器可以重启实例；不能因为数据库短暂不可用就用它触发重启风暴。
`ready` 未通过时返回 `503` 和稳定代码 `SERVICE_NOT_READY`，反向代理或负载均衡器应停止
向该实例分配新流量，但不要把数据库地址、异常消息或待执行迁移名称展示给调用方。

成功示例：

```json
{"status":"ready","checks":{"database":"ok","migrations":"ok"}}
```

未就绪示例包含 `status`、安全的检查摘要以及统一 `error` 对象。F-008 起，local 档案还会
实际写入并清理附件目录中的探针文件；失败只返回 `attachment_storage=unavailable`，不显示路径。

## 2. Request ID

每个经过 Django 中间件的响应都带 `X-Request-ID`。上游代理提供的请求编号只有在长度不
超过 64 且仅包含字母、数字、点、下划线和连字符时才会保留；其他值会替换为 UUIDv7，
从而避免换行注入和无界日志字段。用户报告故障时应同时提供这个编号。

## 3. 结构化运行日志

PMS 运行日志使用单行 JSON，固定包含时间、级别、稳定事件名和 logger；按需允许
request、tenant、actor、operation、entity、result、duration、error code 和 HTTP 状态等
诊断字段。任意 `extra` 不会自动序列化，避免密码、令牌、Cookie、连接字符串和完整
业务对象误入日志。

未预期异常记录 `error_type` 与 request ID，不记录异常消息或堆栈绝对路径。详细业务
动作证据仍写入 F-006 追加式审计表，不能用可轮转的运行日志替代审计。

部署档案级别：

- local：默认 `INFO`，仅在显式 `PMS_DEBUG=true` 时 PMS logger 使用 `DEBUG`；
- lan、cloud：PMS logger 为 `INFO`，依赖和根 logger 从 `WARNING` 开始；
- test：PMS logger 为 `CRITICAL`，测试通过断言验证行为而不是制造终端噪音。

## 4. 安全错误响应

平台边界当前定义以下稳定错误代码：

| 错误代码 | HTTP 状态 | 含义 |
| --- | ---: | --- |
| `INVALID_REQUEST` | 400 | 请求格式或内容被框架安全检查拒绝 |
| `PERMISSION_DENIED` | 403 | 当前身份没有所需权限 |
| `RESOURCE_NOT_FOUND` | 404 | 资源不存在或不应向调用方暴露 |
| `SERVICE_NOT_READY` | 503 | 实例依赖或迁移尚未就绪 |
| `INTERNAL_ERROR` | 500 | 未预期系统错误 |

错误响应只包含稳定代码、可行动中文提示和 request ID，不包含堆栈、SQL、绝对路径、
内部类型或异常原文。后续业务模块可以增加自己的稳定代码，但必须在最接近 HTTP 的边界
映射，不能直接把底层异常字符串返回给浏览器。

## 5. 运维接入

- 本机开发可以直接访问两个端点诊断，不需要启用 GitHub Pages；
- 内网和云端反向代理应保留响应 `X-Request-ID`，并分别配置 live 与 ready 探针；
- 探针超时和频率应结合部署环境设置，不能依赖响应正文中的内部实现细节；
- 日志采集器按“一行一个 JSON 对象”读取 stdout/stderr，并实施访问控制与保留策略；
- 发现 `INTERNAL_ERROR` 时使用 request ID 关联运行日志和审计记录，不向用户索要秘密输入。
