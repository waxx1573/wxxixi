@echo off
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0WxPiPi.ps1" -DiagnoseOnly
pause
