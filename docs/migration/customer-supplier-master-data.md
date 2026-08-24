# 客户与供应商主数据迁移

状态：映射和隔离导入验证完成；正式 `data/` 待用户双击确认导入

## 1. 数据链

```text
.internal/legacy-pms/Database/*.xlsb（只读）
  → pms-legacy-raw-v1（原始类型与来源行）
  → pms-legacy-master-data-v1（客户/供应商规范字段）
  → 正式应用服务（权限、事务、审计、租户）
  → SQLite 客户/供应商表 + 无敏感值对账报告
```

原始包和规范包都含真实敏感数据，位于 `.internal/migration/` 并由 Git 忽略。仓库测试只使用
完全虚构资料，Git 提交中不包含真实客户、供应商、税号、账户或联系方式。

## 2. 字段映射

| 旧数据集 | 旧列 | 新字段 |
| --- | --- | --- |
| `clients` | 简称 | `Customer.short_name` |
| `clients` | 客户名称 | `Customer.name` |
| `clients` | 客户税号、客户地址、电话 | `tax_identifier`、`address`、`phone` |
| `clients` | 开户行、账号、行号 | `bank_name`、`bank_account`、`bank_routing_number` |
| `suppliers` | 简称、供应商 | `Supplier.short_name`、`Supplier.name` |
| `suppliers` | 联系人、电话、地址、服务 | 对应联系和服务字段 |
| `suppliers` | 税号、银行行号、开户银行、银行账号 | 对应敏感字段 |
| `suppliers` | 英文名、英文地址 | `english_name`、`english_address` |

空单元格映射为空字符串；文本执行 Unicode NFKC 并折叠多余空白；文本字段只接受原始 `text`
或 `number` 类型，日期、布尔等结构漂移会停止。代码按来源行号生成，规则见
[供应商主数据基线](../product/phase-3a-supplier-master-data.md)。

## 3. 真实包验证结果

- 源码确认：客户 8 列、供应商 12 列与白名单表头完全一致。
- 本机验证：客户 9 条、供应商 115 条完成规范映射。
- 本机隔离验证：空库首次导入新增客户 9、供应商 115；第二次新增均为 0，复用客户 9、供应商
  115；系统检查无问题。
- 当前边界：以上验证使用 `.tmp/p3a-master-data-verify-20260824/` 隔离数据库，没有修改正式
  `data/pms.sqlite3`。

## 4. 用户执行方式

关闭正在运行的 PMS 后，双击仓库根目录 `PMS-导入旧主数据.bat`。入口会：

1. 使用或生成版本化规范包；
2. 执行数据库迁移；
3. 在 `.internal/migration/pre-import-backups/` 创建完整数据和附件备份；
4. 通过正式应用用例导入，并在 `.internal/migration/` 生成带时间戳的对账报告；
5. 执行 Django 系统检查。

成功后双击 `PMS-启动.bat`，登录后从左侧“客户”和“供应商”查看。重复双击导入不会重复创建
完全一致的记录；若现有记录内容冲突，整个批次回滚。

换电脑仍使用正式备份/恢复包迁移整个数据库和附件，不用本迁移包代替灾难恢复。面向租户的可
编辑业务交换导出属于后续独立格式，本批没有把 SQLite 转储冒充公开导出格式。
