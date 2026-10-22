@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ========================================
echo A-Stock QMT Bridge Health Check
echo ========================================
py .\services\bridge_doctor.py
echo.
pause
