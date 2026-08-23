# 部署配置档案

状态：F-002 基线

## 1. 目的

PMS 使用同一套业务代码，通过 `local`、`lan`、`cloud` 和 `test` 配置档案切换基础设施。环境变量只在 `pms.settings` 平台边界读取；业务模块不得直接读取。

## 2. 选择档案

通过 `DJANGO_SETTINGS_MODULE` 选择：

| 档案 | 模块 | 用途 | 数据库 |
| --- | --- | --- | --- |
| 本机 | `pms.settings.local` | 单机开发和未来 localhost 交付 | 私有数据目录中的 SQLite |
| 内网 | `pms.settings.lan` | 公司服务机多人访问 | PostgreSQL |
| 云端 | `pms.settings.cloud` | 域名和 HTTPS 服务 | PostgreSQL |
| 测试 | `pms.settings.test` | 自动化测试 | 内存 SQLite |

`manage.py` 和 ASGI 入口只在未显式选择时默认使用 `local`。服务器部署必须显式选择档案。

## 3. 环境变量

无秘密示例见仓库根目录 `.env.example`。PMS 当前不自动加载 `.env` 文件，部署者应通过进程环境或秘密管理机制注入。

| 名称 | 档案 | 规则 |
| --- | --- | --- |
| `PMS_SECRET_KEY` | lan、cloud 必填 | 每套部署独立生成，不记录到日志或 Git |
| `PMS_ALLOWED_HOSTS` | lan、cloud 必填 | 逗号分隔的可信主机名 |
| `PMS_DB_NAME/USER/PASSWORD/HOST/PORT` | lan、cloud 必填 | PostgreSQL 连接参数，缺失即拒绝启动 |
| `PMS_BIND_HOST` | local 可选 | 默认 `127.0.0.1`，仅接受 IP loopback 地址 |
| `PMS_DEBUG` | local 可选 | 默认关闭，仅接受明确布尔值 |
| `PMS_DATA_DIR` | local 可选 | 默认仓库下被忽略的 `data/`；交付版应使用应用私有目录 |

`local` 在启动时创建缺失的数据目录及其 `attachments/` 私有子目录。Django 的启动迁移
检查可能同时创建 SQLite 文件；附件原文不会写入数据库，也不会存放到仓库可跟踪目录。

## 4. 安全差异

- `base` 默认关闭 `DEBUG`，启用安全、CSRF、Host 和防点击劫持中间件。
- `local` 只允许 localhost Host，Cookie 暂不要求 TLS，因为仅限 loopback。
- `lan` 要求安全 Cookie 和受控 HTTPS 反向代理。
- `cloud` 额外强制 HTTPS 与 HSTS；正式启用前必须确认所有子域均支持 HTTPS。
- `lan` 和 `cloud` 绝不在配置缺失时回退 SQLite。

F-002 只验证配置结构，不连接 PostgreSQL，也不执行迁移。正式部署说明将在相应阶段补充。

## 5. 可观测性差异

F-007 起，所有档案启用统一的 request ID、安全错误响应和 JSON 运行日志。local 默认
`INFO`，显式启用 `PMS_DEBUG` 时仅 PMS logger 提升为 `DEBUG`；lan 和 cloud 保持
`INFO`，test 使用 `CRITICAL` 减少自动化噪音。健康探针与反向代理接入方式见
[健康检查、运行日志与错误响应](observability.md)。
