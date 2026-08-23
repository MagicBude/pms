# 首次安装与可重复初始化

状态：F-009 已验证基线（SQLite 本地与 PostgreSQL 18 CI）

## 1. 目的与边界

PMS 使用显式的 `initialize_pms` 管理命令建立默认租户、初始管理员、稳定权限、默认角色矩阵
以及管理员成员角色。应用启动本身不会自动写入这些数据，数据库迁移也只负责结构变化。

这条边界保证：

- 部署者清楚知道何时创建第一个高权限身份；
- 服务重启不会修改密码或重新启用已停用对象；
- 本机、内网和云端可复用同一初始化逻辑，只通过配置切换数据库；
- 初始化可以在事务中完整成功或完整回滚。

本命令不创建示例业务数据，不替代后续成员管理页面，也不是授权许可证“注册机”。

## 2. 本机空库初始化

首次安装在仓库根目录执行：

```powershell
uv sync --locked --all-groups
uv run python manage.py migrate --noinput

$initialPassword = Read-Host "请输入初始管理员密码" -AsSecureString
$env:PMS_INITIAL_ADMIN_PASSWORD = [Net.NetworkCredential]::new("", $initialPassword).Password
try {
    uv run python manage.py initialize_pms
} finally {
    Remove-Item Env:PMS_INITIAL_ADMIN_PASSWORD -ErrorAction SilentlyContinue
}

uv run python manage.py check
uv run python manage.py runserver 127.0.0.1:8000
```

浏览器访问 `http://127.0.0.1:8000/health/ready`，预期返回 `status: ready`；访问根路径会转到
本机工作台登录页。正式使用建议改用[本机启动器](../deployment/local-launcher.md)，而不是开发用
`runserver`。

不要把真实密码直接写在命令行参数、脚本、聊天记录或仓库文件中。PowerShell 环境变量只对子进程
及当前终端会话可见，上述 `finally` 会在成功或失败后清除它；部署平台应改用受控秘密注入机制。

## 3. 默认结果

不带非秘密参数时，首次命令建立：

| 数据 | 默认值 |
| --- | --- |
| 租户 | code 为 `local`，名称为“本机租户” |
| 管理员 | username 为 `admin`，使用 Django 密码哈希，不授予 Django superuser/staff |
| 成员关系 | 管理员作为默认租户的有效成员 |
| 权限 | `PermissionCode` 的全部稳定能力代码和中文名称 |
| 角色 | 已接受角色权限矩阵中的五个默认角色 |
| 管理员角色 | 默认成员获得 `tenant_admin`，业务授权仍通过权限代码判断 |

内网或受控云环境可以显式指定非秘密标识：

```powershell
uv run python manage.py initialize_pms `
    --tenant-code example-company `
    --tenant-name "示例公司" `
    --admin-username system-admin
```

初始密码仍只从 `PMS_INITIAL_ADMIN_PASSWORD` 读取，命令没有密码参数。

## 4. 重复执行语义

第二次及以后执行同一命令：

- 不重复创建租户、用户、成员、权限、角色或角色授权；
- 不读取也不重置已有管理员密码，因此成功初始化后可先清除密码环境变量；
- 把默认权限与角色名称、授权范围恢复为仓库中已接受的版本；
- 移除默认角色上不属于默认矩阵的多余授权，但保留使用其他 code 创建的自定义角色；
- 若默认管理员、租户或成员关系已停用，则停止并要求通过显式管理流程处理。
- 每次成功执行追加一条不含密码的系统初始化审计；审计事件本身不是可去重的配置状态。

可直接复验幂等性：

```powershell
uv run python manage.py initialize_pms
```

输出中的“新增”计数应全部为 0。输出不包含密码、UUID、数据库地址或本机路径。

## 5. 冲突与恢复

- 初次创建管理员时缺少密码或密码不符合 Django 验证策略，整次事务回滚。
- 指定 username 已存在但不属于目标 tenant 时，命令拒绝自动授予管理员权限。
- 目标 tenant 已有初始化管理员时，命令拒绝用另一 username 创建第二个管理员；后续增员必须走成员管理流程。
- 初始化失败后先阅读安全错误提示，修正参数或已有数据冲突，再完整重试；不要手工绕过唯一约束。
- 数据库迁移和初始化是两个独立步骤；必须先成功运行 `migrate`。

当前尚无真实业务数据时，可以删除专门用于测试的临时数据目录后从空库重演。开始录入真实数据后，
不得用删库代替恢复；备份与恢复流程会在正式本机交付前单独设计和演练。

## 6. 自动化烟雾验证

GitHub `Quality` 从空 SQLite 和 PostgreSQL 18 分别验证：

1. 首次迁移和重复迁移；
2. 首次初始化和无密码的重复初始化；
3. 默认数据计数、密码哈希、权限矩阵修复与权限误授反向场景；
4. Django system check；
5. Uvicorn 启动和 `/health/ready` 就绪响应。

所有临时 SQLite、附件目录和日志都位于 runner 临时目录，不进入 Git 工作区。
