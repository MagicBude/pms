@echo off
setlocal EnableExtensions DisableDelayedExpansion
title PMS Legacy Master Data Import

rem PMS legacy customer and supplier import entrypoint.
rem The repository is located from this BAT file via %%~dp0; no drive is fixed.
rem Detailed Chinese explanations live in scripts\windows\import-legacy-master-data.ps1.

cd /d "%~dp0"
if errorlevel 1 goto :failed

PowerShell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows\import-legacy-master-data.ps1"
set "PMS_EXIT_CODE=%errorlevel%"
pause
exit /b %PMS_EXIT_CODE%

:failed
pause
exit /b 1
