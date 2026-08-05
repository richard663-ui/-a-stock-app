@echo off
chcp 65001 >nul
cd /d "%~dp0"

py -c "from xtquant import xtdata" >nul 2>&1
if errorlevel 1 (
  echo [ERROR] xtquant is not installed in the current Python.
  echo Run once: py -m pip install xtquant
  pause
  exit /b 1
)

py -c "import streamlit" >nul 2>&1
if errorlevel 1 (
  echo [ERROR] streamlit is not installed.
  echo Run once: py -m pip install -r requirements.txt
  pause
  exit /b 1
)

py -c "import streamlit_autorefresh" >nul 2>&1
if errorlevel 1 (
  echo Installing streamlit-autorefresh...
  py -m pip install streamlit-autorefresh
)

echo Starting A-share Monitor V17...
py -m streamlit run app_v17.py
pause
