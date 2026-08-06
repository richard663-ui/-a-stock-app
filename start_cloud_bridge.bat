@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONPATH=%CD%;%PYTHONPATH%"
set "NO_PROXY=.supabase.co,supabase.co"
set "no_proxy=.supabase.co,supabase.co"

py -c "from xtquant import xtdata" >nul 2>&1
if errorlevel 1 (
  echo [ERROR] xtquant is not installed for this Python.
  echo Run once: py -m pip install xtquant
  pause
  exit /b 1
)

py -c "import requests, urllib3, certifi" >nul 2>&1
if errorlevel 1 (
  echo Installing bridge network dependencies...
  py -m pip install --upgrade requests urllib3 certifi
  if errorlevel 1 (
    echo [ERROR] Could not install requests/urllib3/certifi.
    pause
    exit /b 1
  )
)

if not exist ".streamlit\secrets.toml" (
  echo [ERROR] Missing .streamlit\secrets.toml
  echo Copy .streamlit\secrets.toml.example to secrets.toml and fill in Supabase values.
  pause
  exit /b 1
)

echo Checking Supabase configuration and HTTPS connection...
py .\services\check_cloud_config.py
if errorlevel 1 (
  echo.
  echo If the error mentions SSL EOF, temporarily turn off VPN/proxy/HTTPS scanning and retry.
  pause
  exit /b 1
)

echo Starting QMT cloud bridge...
py .\services\qmt_cloud_bridge.py
pause
