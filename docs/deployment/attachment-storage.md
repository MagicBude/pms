# 附件元数据与本地存储基线

状态：F-008 本地候选基线，等待 PostgreSQL 18 CI 验证

## 1. 实施范围

本基线落实 [ADR-0003](../architecture/adr/ADR-0003-attachment-storage.md) 的工程底座：

- 数据库只保存租户级附件元数据和一致性状态；
- `BinaryStorage` 隔离应用层与本机路径、共享卷或未来对象存储；
- `LocalBinaryStorage` 在应用私有目录完成临时写入、大小限制、SHA-256 和原子移动；
- `AttachmentService` 编排 PENDING、AVAILABLE、FAILED 和故障补偿；
- reconciliation 只读报告缺失、篡改和意外残留对象。

本批不提供上传页面、BOM 解析、业务对象关联表、对象级下载权限、病毒扫描或云存储
SDK。这些能力分别由 SLICE-001 业务模块、后续安全评审和云端基础设施实现。

## 2. 元数据

`attachments_attachment` 保存：

- UUIDv7 附件 ID、tenant 和创建人；
- 原始文件名与安全显示名；
- 可信上传边界检测后的媒体类型和显示扩展名；
- byte 大小、小写 SHA-256 摘要；
- 随机 storage key、后端、存储版本和来源；
- PENDING、AVAILABLE、QUARANTINED、FAILED、DELETED 状态及安全失败代码；
- 带时区创建和更新时间。

AVAILABLE 记录必须同时具有大小与摘要。原文件名只作元数据，不能成为路径、唯一键或
业务状态。业务模块拥有“项目/BOM 等对象关联哪个附件”的关系，并负责验证关联两端 tenant。

## 3. 存储键与路径安全

本地存储版本 1 使用以下内部格式：

```text
tenants/<tenant-uuid>/<yyyy>/<mm>/<attachment-uuid>/<object-uuid>
```

两个对象 UUID 均由服务端生成，键中没有原文件名、客户名、项目名或其他业务信息。
适配器不只检查 `..`：它要求 tenant 前缀匹配、段数固定、年月合法、两个对象段均为 UUID，
再解析并确认最终路径仍位于配置根目录。知道其他租户 storage key 不能直接打开文件。

本机档案把附件保存在 `PMS_DATA_DIR/attachments/`。该目录、SQLite 和未来备份应作为同一
应用数据集管理；不得通过 Web 服务器公开映射，也不得让浏览器或其他电脑共享打开目录。

## 4. 原子写入与失败窗口

上传底层顺序如下：

1. 规范原文件名并生成附件 ID 与随机 storage key；
2. 提交 PENDING 元数据，使进程中断后仍有可对账入口；
3. 在同一存储根的 `.staging` 流式写入，同时累计 byte 大小和 SHA-256；
4. 超限、分块错误或 I/O 失败时删除临时文件并把元数据标记 FAILED；
5. 完整写入并 `fsync` 后，以原子重命名进入正式随机键；
6. 使用 tenant、附件 ID、PENDING 状态和 storage key 条件更新为 AVAILABLE；
7. 最终元数据更新失败时删除正式对象并尽力标记 FAILED。

随机键已经把碰撞降到极低，适配器仍在写入前和移动前检查并拒绝覆盖既有对象。文件系统与
数据库不能共享事务；若进程恰好在故障窗口终止，PENDING/FAILED 和对账结果负责显式暴露，
不能把半成品视为可下载附件。

## 5. 读取与租户隔离

应用读取同时使用可信 `TenantContext.tenant_id` 和 attachment ID 查询 AVAILABLE 元数据。
跨租户 ID、未知 ID、PENDING、FAILED、QUARANTINED 和 DELETED 对调用方统一表现为不可用，
避免泄露其他租户对象是否存在。存储适配器再次校验 storage key tenant 前缀，形成纵深防御。

F-008 只验证附件本身的 tenant 边界。正式下载还必须由业务模块证明成员对关联项目、BOM
等对象有权限，再检查 `attachment.download` 并记录成功或拒绝审计；不能直接暴露 storage key。

## 6. 对账

对账按单个 tenant 稳定遍历，不自动修改或删除证据：

| 代码 | 含义 |
| --- | --- |
| `missing_object` | AVAILABLE 元数据没有对应文件，或核验期间文件消失 |
| `size_mismatch` | 实际 byte 大小与元数据不一致 |
| `digest_mismatch` | 实际 SHA-256 与元数据不一致 |
| `unexpected_object` | 非 AVAILABLE 状态仍存在正式对象 |

后续备份、恢复与运维命令应复用这些事实，并在任何自动修复前生成审计和备份清单。

## 7. 就绪检查

local 档案启动时创建私有附件目录。`/health/ready` 会写入并清理同目录探针文件，只有数据库、
迁移和已配置的附件目录均可用才返回 ready；响应不会包含绝对路径或底层异常。test 档案不
配置生产附件根，测试各自使用临时目录。lan 共享卷与 cloud 对象存储将在对应部署阶段配置。
