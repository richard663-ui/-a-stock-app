@echo off
chcp 65001 >nul
cd /d "%~dp0"

py -c "from xtquant import xtdata" >nul 2>&1
if errorlevel 1 (
  echo [ERROR] xtquant is not installed for this Python.
  echo Run once: py -m pip install xtquant
  pause
  exit /b 1
)

if not exist ".streamlit\secrets.toml" (
  echo [ERROR] Missing .streamlit\secrets.toml
  echo Copy .streamlit\secrets.toml.example to secrets.toml and fill in Supabase values.
  pause
  exit /b 1
)

echo Starting QMT cloud bridge...
py .\services\qmt_cloud_bridge.py
pause
