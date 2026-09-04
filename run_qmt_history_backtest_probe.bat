@echo off
setlocal EnableExtensions
chcp 65001 >nul

echo ======================================================
echo A-Stock QMT Historical Tick Backtest Probe V1
echo No orders. No recorder stop. No live deployment.
echo ======================================================

set "INSTALLDIR=%LOCALAPPDATA%\AStockQMT"
set "SERVICEDIR=%INSTALLDIR%\services"
set "BASE=https://raw.githubusercontent.com/richard663-ui/-a-stock-app/main"
set "SCRIPT=%SERVICEDIR%\qmt_history_backtest_probe_v1.py"

if not exist "%INSTALLDIR%" (
  echo [ERROR] AStock QMT runtime not found: %INSTALLDIR%
  pause
  exit /b 1
)
if not exist "%SERVICEDIR%" mkdir "%SERVICEDIR%" >nul 2>&1

set "PYEXE="
for /f "delims=" %%I in ('py -c "from xtquant import xtdata;import sys;print(sys.executable)" 2^>nul') do set "PYEXE=%%I"
if not defined PYEXE for /f "delims=" %%I in ('python -c "from xtquant import xtdata;import sys;print(sys.executable)" 2^>nul') do set "PYEXE=%%I"
if not defined PYEXE (
  echo [ERROR] No Python interpreter with xtquant is available.
  echo Keep QMT open/logged in and use the same Python environment as the working L1 recorder.
  pause
  exit /b 1
)

echo [1/3] Downloading latest probe...
curl.exe -L --fail --retry 3 -o "%SCRIPT%.tmp" "%BASE%/services/qmt_history_backtest_probe_v1.py"
if errorlevel 1 goto :fail
findstr /C:"qmt-history-backtest-probe-v1-20260904" "%SCRIPT%.tmp" >nul
if errorlevel 1 goto :fail
move /Y "%SCRIPT%.tmp" "%SCRIPT%" >nul

echo [2/3] Syntax checking...
set "PYTHONPATH=%INSTALLDIR%;%PYTHONPATH%"
"%PYEXE%" -m py_compile "%SCRIPT%"
if errorlevel 1 goto :fail

echo [3/3] Probing QMT native backtest API + historical tick/five-level data...
echo Default: 301236.SZ + 000400.SZ, last 14 calendar days.
echo This can take a few minutes if QMT needs to download tick history.
echo.
cd /d "%INSTALLDIR%"
"%PYEXE%" -u "%SCRIPT%" --days 14 --symbols "301236.SZ,000400.SZ"
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (
  echo ======================================================
  echo [READY] QMT historical L1 replay capability confirmed.
  echo The report was also sent to cloud when bridge config was available.
  echo ======================================================
) else (
  echo ======================================================
  echo [INFO] Probe finished with RC=%RC%.
  echo Read the WARN/FAIL line above; no existing runtime was changed.
  echo ======================================================
)
pause
exit /b %RC%

:fail
if exist "%SCRIPT%.tmp" del /q "%SCRIPT%.tmp" >nul 2>&1
echo [ERROR] Probe download or syntax validation failed. Existing runtime untouched.
pause
exit /b 1
