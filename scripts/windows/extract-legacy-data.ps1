# PMS 旧系统核心数据只读提取入口。
#
# 本脚本只读取仓库 .internal/legacy-pms 中经过白名单声明的核心数据库，
# 不启动 Excel、不运行 VBA、不修改旧文件。输出包含真实客户、订单、财务
# 和员工信息，只能保存在 Git 忽略区或其他受控介质中。

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

$originalLocation = Get-Location
$exitCode = 0

try {
    $repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
    Set-Location $repositoryRoot
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        throw "未检测到 uv。请先运行 PMS-首次安装.bat。"
    }

    $legacyRoot = Join-Path $repositoryRoot ".internal\legacy-pms"
    if (-not (Test-Path -LiteralPath $legacyRoot -PathType Container)) {
        throw "未找到 .internal\legacy-pms，无法提取旧数据。"
    }
    $migrationRoot = Join-Path $repositoryRoot ".internal\migration"
    New-Item -ItemType Directory -Path $migrationRoot -Force | Out-Null
    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $outputDirectory = Join-Path $migrationRoot "raw-$timestamp-core-v1"

    Write-Host "正在只读提取旧 PMS 核心数据库……" -ForegroundColor Cyan
    Write-Host "输出包含真实敏感数据，不得提交 Git 或通过不受控渠道传输。" -ForegroundColor Yellow
    & uv run python manage.py extract_legacy_data `
        --legacy-root $legacyRoot `
        --output $outputDirectory `
        --include-restricted
    if ($LASTEXITCODE -ne 0) {
        throw "旧数据提取命令执行失败。"
    }
    Write-Host "提取目录：$outputDirectory" -ForegroundColor Green
}
catch {
    Write-Host "旧数据提取失败：$($_.Exception.Message)" -ForegroundColor Red
    $exitCode = 1
}
finally {
    Set-Location $originalLocation
}

exit $exitCode
