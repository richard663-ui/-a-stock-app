@echo off
setlocal EnableExtensions
chcp 65001 >nul

echo ======================================================
echo A-Stock QMT L1 60s Historical Walk-Forward Audit V2
echo Parity + permutation-null / reference-only / no trading
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

echo [1/3] Downloading V2 audit plus frozen V4R/V5R stack...
for %%F in (qmt_walkforward_pandas_compat.py qmt_walkforward_null_compat_v2.py qmt_l1_60s_walkforward_v1.py qmt_l1_60s_walkforward_v2.py train_l1_60s_model_v1.py train_l1_60s_model_v2.py train_l1_60s_model_v3.py train_l1_60s_model_v4.py train_l1_60s_model_v4r.py train_l1_60s_model_v5_challenger.py train_l1_60s_model_v5r.py train_l2_60s_model_v3.py train_l2_60s_model_v4.py train_l2_60s_model_v5.py) do (
  curl.exe -L --fail --retry 3 -o "%SERVICEDIR%\%%F" "%BASE%/services/%%F" || goto :fail
)
findstr /C:"qmt-l1-60s-walkforward-v2-parity-null-20260905" "%SERVICEDIR%\qmt_l1_60s_walkforward_v2.py" >nul || goto :fail
findstr /C:"dense final test day" "%SERVICEDIR%\qmt_walkforward_null_compat_v2.py" >nul || goto :fail
findstr /C:"l1-60s-trainer-v5r-robust-challenger-20260904" "%SERVICEDIR%\train_l1_60s_model_v5r.py" >nul || goto :fail

echo [2/3] Syntax check...
"%PYEXE%" -m py_compile "%SERVICEDIR%\qmt_walkforward_pandas_compat.py" "%SERVICEDIR%\qmt_walkforward_null_compat_v2.py" "%SERVICEDIR%\qmt_l1_60s_walkforward_v1.py" "%SERVICEDIR%\qmt_l1_60s_walkforward_v2.py"
if errorlevel 1 goto :fail

echo [3/3] Running frozen historical replay + parity/null audit...
echo Existing live recorder/ML daemon are NOT stopped.
echo V2 does NOT alter V4R/V5R weights, thresholds, promotion, or live models.
set "PYTHONPATH=%INSTALLDIR%;%PYTHONPATH%"
cd /d "%INSTALLDIR%"
"%PYEXE%" -u -c "import services.qmt_walkforward_pandas_compat; import services.qmt_l1_60s_walkforward_v2 as m; import services.qmt_walkforward_null_compat_v2 as nf; nf.apply(); raise SystemExit(m.main())" --days 14
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (
  echo [PASS] Historical Audit V2 completed and cloud sync was attempted.
  echo [CHECK] Review historical-live parity and shuffled-label AUC before trusting historical metrics.
  echo [IMPORTANT] Historical results cannot tune/promote/deploy; future unseen days remain mandatory.
) else (
  echo [ERROR] Historical Audit V2 failed with rc=%RC%.
  echo Existing live runtime was not stopped or replaced.
)
echo.
pause
exit /b %RC%

:fail
echo [ERROR] Download or validation failed. Existing runtime was untouched.
pause
exit /b 1
