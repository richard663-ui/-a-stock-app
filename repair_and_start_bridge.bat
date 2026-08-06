@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONPATH=%CD%;%PYTHONPATH%"
set "NO_PROXY=.supabase.co,supabase.co"
set "no_proxy=.supabase.co,supabase.co"

echo [1/4] Updating HTTPS libraries...
py -m pip install --upgrade requests urllib3 certifi
if errorlevel 1 (
  echo [ERROR] Network libraries could not be updated.
  pause
  exit /b 1
)

echo [2/4] Recovering persistent local configuration...
py .\services\bootstrap_bridge_config.py
if errorlevel 1 (
  pause
  exit /b 1
)

echo [3/4] Testing Supabase HTTPS connection...
py .\services\check_cloud_config.py
if errorlevel 1 (
  echo.
  echo [FAILED] Supabase connection test failed.
  echo Close VPN/proxy software and temporarily disable antivirus HTTPS scanning, then run this file again.
  pause
  exit /b 1
)

echo [4/4] Starting QMT cloud bridge...
py .\services\qmt_cloud_bridge.py
pause
