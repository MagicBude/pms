# PMS Windows 首次安装实现。
#
# 用户双击仓库根目录的“PMS-首次安装.bat”后，由 BAT 调用本脚本。
# BAT 负责提供容易发现的入口；PowerShell 负责隐藏密码输入、检查每个
# 外部命令退出码，并确保密码环境变量在成功或失败后都被清除。

#Requires -Version 5.1

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# Windows PowerShell 5.1 默认使用传统控制台代码页。这里同时设置控制台、
# 管道和 Python 为 UTF-8，避免 Django 的中文迁移或错误提示变成乱码。
$utf8WithoutBom = [System.Text.UTF8Encoding]::new($false)
try {
    [Console]::InputEncoding = $utf8WithoutBom
    [Console]::OutputEncoding = $utf8WithoutBom
}
catch {
    # 无控制台的自动化宿主可能不允许修改编码；安装逻辑仍可继续运行。
}
$OutputEncoding = $utf8WithoutBom
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

function Invoke-UvCommand {
    <#
    .SYNOPSIS
    执行一个 uv 子命令，并把非零退出码转换为可定位的安装失败。

    .DESCRIPTION
    初始管理员密码只通过进程环境传递，不属于 Arguments，因此错误消息
    可以安全显示执行步骤，而不会把密码写入终端、日志或命令历史。
    #>
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    & uv @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "命令执行失败：uv $($Arguments -join ' ')"
    }
}

$originalLocation = Get-Location
$exitCode = 0
$plainPassword = $null
$securePassword = $null

try {
    $repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
    Set-Location $repositoryRoot

    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        throw "未检测到 uv。请先安装 uv，再重新运行 PMS-首次安装.bat。"
    }

    # 首次安装和日常启动必须指向同一个仓库内 data 目录，避免换电脑后
    # 意外继承旧终端中的 PMS_DATA_DIR 并打开错误数据库。
    $env:DJANGO_SETTINGS_MODULE = "pms.settings.local"
    $env:PMS_DATA_DIR = Join-Path $repositoryRoot "data"
    $env:PMS_DEBUG = "false"
    Remove-Item Env:PMS_INITIAL_ADMIN_PASSWORD -ErrorAction SilentlyContinue

    Write-Host "[1/5] 安装或确认 Python 3.14.7……" -ForegroundColor Cyan
    Invoke-UvCommand -Arguments @("python", "install", "3.14.7")

    Write-Host "[2/5] 按 uv.lock 同步项目依赖……" -ForegroundColor Cyan
    Invoke-UvCommand -Arguments @("sync", "--locked", "--all-groups")

    Write-Host "[3/5] 创建或升级本机 SQLite 数据库……" -ForegroundColor Cyan
    Invoke-UvCommand -Arguments @("run", "python", "manage.py", "migrate", "--noinput")

    Write-Host "[4/5] 初始化默认租户和 admin 管理员……" -ForegroundColor Cyan
    Write-Host "如果数据库已经初始化，本次输入不会覆盖原有 admin 密码。" -ForegroundColor Yellow
    $securePassword = Read-Host "请设置或输入初始 admin 密码" -AsSecureString
    $plainPassword = [System.Net.NetworkCredential]::new("", $securePassword).Password
    if ([string]::IsNullOrWhiteSpace($plainPassword)) {
        throw "初始 admin 密码不能为空。"
    }

    $env:PMS_INITIAL_ADMIN_PASSWORD = $plainPassword
    try {
        Invoke-UvCommand -Arguments @("run", "python", "manage.py", "initialize_pms", "--no-color")
    }
    finally {
        Remove-Item Env:PMS_INITIAL_ADMIN_PASSWORD -ErrorAction SilentlyContinue
        $plainPassword = $null
        $securePassword = $null
    }

    Write-Host "[5/5] 检查本机配置和数据库状态……" -ForegroundColor Cyan
    Invoke-UvCommand -Arguments @("run", "python", "manage.py", "check")
    Write-Host "PMS 首次安装完成。" -ForegroundColor Green
}
catch {
    Remove-Item Env:PMS_INITIAL_ADMIN_PASSWORD -ErrorAction SilentlyContinue
    $plainPassword = $null
    $securePassword = $null
    Write-Host "PMS 首次安装失败：$($_.Exception.Message)" -ForegroundColor Red
    $exitCode = 1
}
finally {
    Set-Location $originalLocation
}

exit $exitCode
