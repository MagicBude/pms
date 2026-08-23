# 本机备份与恢复

状态：F-010 本地候选基线，等待 GitHub Actions 验证

## 1. 适用范围

本页只适用于 `local` 部署档案、SQLite 数据库和本地附件目录。它不适用于内网
PostgreSQL、云端对象存储，也不代表已经建立自动备份、保留策略或异地灾备。

备份命令会把数据库快照、所有 `AVAILABLE` 附件和版本化清单组成一个可独立搬运的目录。
恢复命令只写入不存在或明确为空的新数据目录，绝不覆盖当前 `PMS_DATA_DIR` 或非空目录。

## 2. 备份前准备

1. 正常停止 PMS 服务，避免操作窗口内继续录入或上传附件；
2. 确认应用已完成迁移和首次初始化；
3. 准备一个已存在的备份根目录，优先选择另一块物理磁盘或受保护的外部介质；
4. 确认备份目标不在当前 PMS 数据目录内，并有足够剩余空间；
5. 限制备份目录访问权限，因为其中包含业务数据库、密码哈希和附件原文。

SQLite 快照通过 Online Backup API 创建，附件复制前后都会核对对象集合、大小和 SHA-256。
这些机制能让并发漂移安全失败，但不能代替停止服务形成的明确操作窗口。

## 3. 创建并验证备份

以下 PowerShell 示例把备份写到 `D:\PMS-Backups`：

```powershell
uv run python manage.py backup_local --destination "D:\PMS-Backups"
```

成功后命令会输出新备份集的完整路径，例如：

```text
D:\PMS-Backups\pms-backup-20260823-01a02f1c234c
```

立即执行一次独立验证：

```powershell
uv run python manage.py verify_local_backup `
  --backup-set "D:\PMS-Backups\pms-backup-20260823-01a02f1c234c"
```

只有看到“备份验证通过”后，才把该目录视为候选恢复点。复制到其他介质后应再次执行同一验证。

## 4. 备份集结构

```text
pms-backup-YYYYMMDD-<随机标识>/
├── database/
│   └── pms.sqlite3
├── objects/
│   └── <attachment-id>
├── manifest.json
└── manifest.sha256
```

- `database/pms.sqlite3` 是 SQLite 一致性快照；
- `objects/` 使用扁平附件 ID，避免 Windows 深层路径超过传统路径长度限制；
- `manifest.json` 记录应用版本、迁移、表记录数、附件原存储键、大小和摘要；
- `manifest.sha256` 用于发现清单损坏或普通误改。

`manifest.sha256` 不是数字签名，不能证明备份来源可信，也不提供加密。不要人工修改、增删或重命名
备份集内任何文件；整个目录必须作为一个整体保存和复制。

## 5. 恢复到新目录

先停止 PMS，选择一个不存在或明确为空的新目录。其父目录必须已经存在：

```powershell
uv run python manage.py restore_local `
  --backup-set "D:\PMS-Backups\pms-backup-20260823-01a02f1c234c" `
  --target-data-dir "D:\PMS-Restore\data"
```

恢复会先重新验证整个备份集，再在目标旁的私有暂存目录中重建 SQLite 和附件；数据库完整性、
迁移、初始化数据、记录计数及全部附件摘要通过后，才原子发布目标目录。失败时不会留下一个看似
可用的半恢复目标，原数据目录也不会被修改。

恢复成功后，在当前 PowerShell 会话中验证新目录：

```powershell
$env:PMS_DATA_DIR = "D:\PMS-Restore\data"
uv run python manage.py check
uv run uvicorn pms.asgi:application --host 127.0.0.1 --port 8000
```

另开终端访问 `http://127.0.0.1:8000/health/ready`，确认全部依赖为 `ok`。验证完成前不要删除或
替换原数据目录；需要正式切换时，应先保留原目录的只读副本，再由后续部署流程调整
`PMS_DATA_DIR`。

## 6. 安全失败条件

出现以下任一情况时，命令会拒绝继续：

- 当前不是 `local + SQLite` 配置；
- 数据库尚未完成迁移、默认租户和管理员初始化；
- 附件缺失、多出、变化、摘要不符或包含符号链接；
- 清单、应用版本、迁移、表记录数或 SQLite 完整性不一致；
- 备份目标位于当前数据目录内；
- 恢复目标是当前数据目录、非空目录、符号链接或备份集内部路径；
- 备份成员包含路径穿越、额外文件或其他不受支持的格式。

不要通过删除清单字段、重新计算摘要或手工复制部分文件来绕过失败。先保留现场，再根据错误检查
源数据、介质和操作路径。

## 7. 当前限制与后续工作

- 只支持当前应用版本恢复，不自动执行跨版本升级；
- 不压缩、不加密，也不自动上传到远端；
- 尚未建立计划任务、保留周期、容量告警和多恢复点管理；
- PostgreSQL 及云端对象存储需要独立备份方案；
- `AC-S001-042` 的真实项目、BOM、请购和附件代表性恢复验收，要在业务模型完成后补充。

因此，F-010 建立的是可验证的本机恢复基础，不等于完整生产灾备方案。
