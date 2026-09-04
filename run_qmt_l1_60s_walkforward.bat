@echo off
setlocal EnableExtensions
chcp 65001 >nul

echo ======================================================
echo A-Stock QMT L1 60s Historical Walk-Forward Audit
echo Reference-only / anti-overfit / no trading
echo ======================================================

set "INSTALLDIR=%LOCALAPPDATA%\AStockQMT"
set "SERVICEDIR=%INSTALLDIR%\services"
set "BASE=https://raw.githubusercontent.com/richard663-ui/-a-stock-app/main"

if not exist "%INSTALLDIR%" (
  echo [ERROR] AStock QMT runtime not found: %INSTALLDIR%
  pause
  exit /b 1
)
if not exist "%SERVICEDIR%" mkdir "%SERVICEDIR%" >nul 2>&1

set "PYEXE="
for /f "delims=" %%I in ('py -c "import sys;print(sys.executable)" 2^>nul') do set "PYEXE=%%I"
if not defined PYEXE for /f "delims=" %%I in ('python -c "import sys;print(sys.executable)" 2^>nul') do set "PYEXE=%%I"
if not defined PYEXE (
  echo [ERROR] Python not found.
  pause
  exit /b 1
)

"%PYEXE%" -c "from xtquant import xtdata; import sklearn,joblib,pandas,numpy" >nul 2>&1
if errorlevel 1 (
  echo [ERROR] xtquant / ML Python dependencies are not available in this interpreter.
  pause
  exit /b 1
)

echo [1/3] Downloading frozen walk-forward code and current V4R/V5R research stack...
for %%F in (qmt_l1_60s_walkforward_v1.py train_l1_60s_model_v1.py train_l1_60s_model_v2.py train_l1_60s_model_v3.py train_l1_60s_model_v4.py train_l1_60s_model_v4r.py train_l1_60s_model_v5_challenger.py train_l1_60s_model_v5r.py train_l2_60s_model_v3.py train_l2_60s_model_v4.py train_l2_60s_model_v5.py) do (
  curl.exe -L --fail --retry 3 -o "%SERVICEDIR%\%%F" "%BASE%/services/%%F" || goto :fail
)
findstr /C:"qmt-l1-60s-walkforward-v1-20260905" "%SERVICEDIR%\qmt_l1_60s_walkforward_v1.py" >nul || goto :fail
findstr /C:"l1-60s-trainer-v5r-robust-challenger-20260904" "%SERVICEDIR%\train_l1_60s_model_v5r.py" >nul || goto :fail

echo [2/3] Syntax check...
"%PYEXE%" -m py_compile "%SERVICEDIR%\qmt_l1_60s_walkforward_v1.py"
if errorlevel 1 goto :fail

echo [3/3] Running historical QMT tick replay + chronological walk-forward...
echo This may take several minutes. Existing live recorder/ML daemon are NOT stopped.
echo No orders are placed. No model is auto-promoted or copied to live.
set "PYTHONPATH=%INSTALLDIR%;%PYTHONPATH%"
cd /d "%INSTALLDIR%"
"%PYEXE%" -u "%SERVICEDIR%\qmt_l1_60s_walkforward_v1.py" --days 14
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (
  echo [PASS] Historical walk-forward completed and report sync was attempted.
  echo [IMPORTANT] Result is reference-only; future unseen days remain mandatory.
) else (
  echo [ERROR] Historical walk-forward failed with rc=%RC%.
  echo Existing live runtime was not stopped or replaced.
)
echo.
pause
exit /b %RC%

:fail
echo [ERROR] Download or validation failed. Existing runtime was untouched.
pause
exit /b 1
