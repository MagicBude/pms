# PMS 旧客户与供应商的一键正式导入入口。
#
# 本脚本不会直接修改旧工作簿。它使用已经生成并受 Git 忽略的版本化规范
# 包，通过正式应用服务导入当前仓库 data 目录。导入前会升级数据库结构并
# 创建完整本机备份；重复运行只复用完全一致的记录，不会重复新增。

#Requires -Version 5.1

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$utf8WithoutBom = [System.Text.UTF8Encoding]::new($false)
try {
    [Console]::InputEncoding = $utf8WithoutBom
    [Console]::OutputEncoding = $utf8WithoutBom
}
catch {
    # 无控制台宿主仍可执行，只有中文显示可能取决于宿主编码。
}
$OutputEncoding = $utf8WithoutBom
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

function Invoke-UvCommand {
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

try {
    $repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
    Set-Location $repositoryRoot
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        throw "未检测到 uv。请先运行 PMS-首次安装.bat。"
    }

    # 数据导入要求独占维护窗口。默认本机网站仍在监听时直接拒绝，避免
    # 浏览器请求在 124 条主数据尚未全部提交时并发读写。
    $portInUse = [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners() |
        Where-Object { $_.Port -eq 8000 }
    if ($portInUse) {
        throw "本机端口 8000 仍有网站运行。请关闭 PMS 启动窗口后重新双击。"
    }

    $migrationRoot = Join-Path $repositoryRoot ".internal\migration"
    $packagePath = Join-Path $migrationRoot "master-data-20260824-v1.json"
    $rawPath = Join-Path $migrationRoot "raw-20260824-core-v1"
    if (-not (Test-Path -LiteralPath $packagePath -PathType Leaf)) {
        if (-not (Test-Path -LiteralPath $rawPath -PathType Container)) {
            throw "缺少原始迁移包。请先双击 PMS-提取旧数据.bat。"
        }
        Write-Host "[1/5] 映射客户与供应商规范包……" -ForegroundColor Cyan
        Invoke-UvCommand -Arguments @(
            "run", "python", "manage.py", "map_legacy_master_data",
            "--raw", $rawPath, "--output", $packagePath, "--no-color"
        )
    }
    else {
        Write-Host "[1/5] 使用已经复核的客户与供应商规范包。" -ForegroundColor Cyan
    }

    $env:DJANGO_SETTINGS_MODULE = "pms.settings.local"
    $env:PMS_DATA_DIR = Join-Path $repositoryRoot "data"
    $env:PMS_DEBUG = "false"
    Write-Host "[2/5] 升级本机数据库结构……" -ForegroundColor Cyan
    Invoke-UvCommand -Arguments @("run", "python", "manage.py", "migrate", "--noinput")

    # 备份放在 .internal 而不是 data 内部；这样备份工具能够验证它不是
    # 当前数据库的子目录，也不会随源代码进入 Git。
    $backupRoot = Join-Path $migrationRoot "pre-import-backups"
    New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
    Write-Host "[3/5] 备份导入前的当前数据和附件……" -ForegroundColor Cyan
    Invoke-UvCommand -Arguments @(
        "run", "python", "manage.py", "backup_local", "--destination", $backupRoot
    )

    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $reportPath = Join-Path $migrationRoot "master-data-import-$timestamp.json"
    Write-Host "[4/5] 通过正式业务用例导入并对账……" -ForegroundColor Cyan
    Invoke-UvCommand -Arguments @(
        "run", "python", "manage.py", "import_legacy_master_data",
        "--input", $packagePath, "--report", $reportPath, "--no-color"
    )

    Write-Host "[5/5] 检查数据库和应用配置……" -ForegroundColor Cyan
    Invoke-UvCommand -Arguments @("run", "python", "manage.py", "check")
    Write-Host "旧客户与供应商导入完成。现在可以双击 PMS-启动.bat 查看。" -ForegroundColor Green
    Write-Host "对账报告：$reportPath" -ForegroundColor Green
}
catch {
    Write-Host "旧主数据导入失败：$($_.Exception.Message)" -ForegroundColor Red
    $exitCode = 1
}
finally {
    Remove-Item Env:PMS_DATA_DIR -ErrorAction SilentlyContinue
    Set-Location $originalLocation
}

exit $exitCode
