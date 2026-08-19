@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ========================================
echo MACD 10-Stock Event Study
echo ========================================
echo Research only. No orders will be sent.
echo.
py .\services\macd_event_study.py
echo.
echo Results are saved under .\runtime\
pause
