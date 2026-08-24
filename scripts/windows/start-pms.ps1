# PMS Windows 日常启动实现。
#
# 用户只需要双击仓库根目录的“PMS-启动.bat”。BAT 保持为纯 ASCII，
# 避免传统 cmd.exe 在不同系统代码页下把 UTF-8 中文误解析为命令；所有
# 中文提示和实际启动逻辑集中在这个带 UTF-8 BOM 的 PowerShell 脚本中。

#Requires -Version 5.1

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# Windows PowerShell 5.1 默认使用传统控制台代码页。这里同时设置控制台、
# 管道和 Python 为 UTF-8，避免 Django 的中文启动提示变成乱码。
$utf8WithoutBom = [System.Text.UTF8Encoding]::new($false)
try {
    [Console]::InputEncoding = $utf8WithoutBom
    [Console]::OutputEncoding = $utf8WithoutBom
}
catch {
    # 无控制台的自动化宿主可能不允许修改编码；启动逻辑仍可继续运行。
}
$OutputEncoding = $utf8WithoutBom
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$originalLocation = Get-Location
$exitCode = 0

try {
    $repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
    Set-Location $repositoryRoot

    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        throw "未检测到 uv。新电脑请先安装 uv，然后双击 PMS-首次安装.bat。"
    }

    # 双击入口始终使用仓库自己的 data 目录和 loopback 地址，防止继承旧
    # 终端中的环境变量后打开错误数据库，或意外把 SQLite 服务暴露到内网。
    $env:DJANGO_SETTINGS_MODULE = "pms.settings.local"
    $env:PMS_DATA_DIR = Join-Path $repositoryRoot "data"
    $env:PMS_BIND_HOST = "127.0.0.1"
    $env:PMS_BIND_PORT = "8000"
    $env:PMS_STARTUP_TIMEOUT_SECONDS = "30"
    $env:PMS_DEBUG = "false"
    Remove-Item Env:PMS_INITIAL_ADMIN_PASSWORD -ErrorAction SilentlyContinue

    Write-Host "正在启动 PMS，请保持此窗口打开。" -ForegroundColor Cyan
    Write-Host "网站地址：http://127.0.0.1:8000/" -ForegroundColor Cyan
    Write-Host "结束使用时，请在此窗口按 Ctrl+C。" -ForegroundColor Yellow
    Write-Host ""

    & uv run python manage.py launch_local
    $exitCode = $LASTEXITCODE
    if ($exitCode -eq 0) {
        Write-Host "PMS 已正常停止。" -ForegroundColor Green
    }
    else {
        Write-Host "PMS 未能正常启动或运行中发生错误。" -ForegroundColor Red
        Write-Host "如果上方提示未初始化，请先双击 PMS-首次安装.bat。" -ForegroundColor Yellow
    }
}
catch {
    Write-Host "PMS 启动失败：$($_.Exception.Message)" -ForegroundColor Red
    $exitCode = 1
}
finally {
    Set-Location $originalLocation
}

exit $exitCode
