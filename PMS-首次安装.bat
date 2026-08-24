@echo off
setlocal EnableExtensions DisableDelayedExpansion
title PMS First-Time Setup

rem PMS first-time setup entrypoint.
rem The repository is located from this BAT file via %%~dp0; no drive is fixed.
rem Detailed Chinese explanations live in scripts\windows\initialize-pms.ps1.
rem PowerShell execution-policy bypass applies only to each child process.

cd /d "%~dp0"
if errorlevel 1 goto :failed

PowerShell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows\initialize-pms.ps1"
if errorlevel 1 goto :failed

PowerShell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows\start-pms.ps1"
set "PMS_EXIT_CODE=%errorlevel%"
pause
exit /b %PMS_EXIT_CODE%

:failed
pause
exit /b 1
