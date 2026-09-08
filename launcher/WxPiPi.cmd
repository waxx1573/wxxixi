@echo off
setlocal
title WxPiPi一键启动
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0WxPiPi.ps1"
set "exit_code=%ERRORLEVEL%"
if not "%exit_code%"=="0" pause
exit /b %exit_code%
