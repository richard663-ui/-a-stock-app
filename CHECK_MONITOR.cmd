@echo off
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0services\start_monitor.ps1" -CheckOnly
