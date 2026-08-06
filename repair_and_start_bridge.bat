@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONPATH=%CD%;%PYTHONPATH%"
set "NO_PROXY=.supabase.co,supabase.co"
set "no_proxy=.supabase.co,supabase.co"

echo [1/4] Checking Python dependencies...
py -c "import requests, urllib3, certifi; from xtquant import xtdata" >nul 2>&1
if errorlevel 1 (
  echo Installing required packages...
  py -m pip install --upgrade requests urllib3 certifi xtquant
  if errorlevel 1 (
    echo [ERROR] Required packages could not be installed.
    pause
    exit /b 1
  )
)

echo [2/4] Loading local configuration...
py .\services\bootstrap_bridge_config.py
if errorlevel 1 (
  echo.
  echo Starting the one-time setup wizard.
  echo You only need to paste the Supabase sb_secret_ key.
  py .\services\setup_local_config.py
  if errorlevel 1 (
    echo.
    echo [STOPPED] Configuration was not completed.
    pause
    exit /b 1
  )
)

echo [3/4] Testing Supabase connection...
py .\services\check_cloud_config.py
if errorlevel 1 (
  echo.
  echo [FAILED] The saved values are valid, but the Supabase connection test failed.
  echo If the message mentions SSL EOF, close VPN/proxy software or try a phone hotspot.
  pause
  exit /b 1
)

echo [4/4] Starting QMT cloud bridge...
py .\services\qmt_cloud_bridge.py
pause
