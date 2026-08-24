@echo off
setlocal EnableExtensions DisableDelayedExpansion
title PMS Local Server

rem PMS daily start entrypoint.
rem The repository is located from this BAT file via %%~dp0; no drive is fixed.
rem Detailed Chinese explanations live in scripts\windows\start-pms.ps1.
rem Keep this window open while PMS is in use; press Ctrl+C to stop safely.

cd /d "%~dp0"
if errorlevel 1 goto :failed

PowerShell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows\start-pms.ps1"
set "PMS_EXIT_CODE=%errorlevel%"
pause
exit /b %PMS_EXIT_CODE%

:failed
pause
exit /b 1
