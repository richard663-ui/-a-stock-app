@echo off
setlocal EnableExtensions
chcp 65001 >nul

echo ======================================================
echo A-Stock QMT L1 60s Historical Walk-Forward V3
echo 10-session Tick comparison: V4R vs V5R vs V6
echo Same folds / same 2bp hurdle / development only / no trading
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

echo [1/3] Downloading frozen audit stack plus V6 challenger...
for %%F in (qmt_walkforward_pandas_compat.py qmt_walkforward_null_compat_v2.py qmt_l1_60s_walkforward_v1.py qmt_l1_60s_walkforward_v2.py qmt_l1_60s_walkforward_v3.py train_l1_60s_model_v1.py train_l1_60s_model_v2.py train_l1_60s_model_v3.py train_l1_60s_model_v4.py train_l1_60s_model_v4r.py train_l1_60s_model_v5_challenger.py train_l1_60s_model_v5r.py train_l1_60s_model_v6_exec_aligned.py train_l2_60s_model_v3.py train_l2_60s_model_v4.py train_l2_60s_model_v5.py) do (
  curl.exe -L --fail --retry 3 -o "%SERVICEDIR%\%%F" "%BASE%/services/%%F" || goto :fail
)
findstr /C:"qmt-l1-60s-walkforward-v3-v6-comparison-20260906" "%SERVICEDIR%\qmt_l1_60s_walkforward_v3.py" >nul || goto :fail
findstr /C:"qmt-l1-60s-walkforward-v2-parity-null-20260905" "%SERVICEDIR%\qmt_l1_60s_walkforward_v2.py" >nul || goto :fail
findstr /C:"dense final test day" "%SERVICEDIR%\qmt_walkforward_null_compat_v2.py" >nul || goto :fail
findstr /C:"l1-60s-trainer-v4r-asymmetric-rotating-thin-20260904" "%SERVICEDIR%\train_l1_60s_model_v4r.py" >nul || goto :fail
findstr /C:"l1-60s-trainer-v5r-robust-challenger-20260904" "%SERVICEDIR%\train_l1_60s_model_v5r.py" >nul || goto :fail
findstr /C:"l1-60s-trainer-v6-exec-aligned-stock-intercept-robust-20260905" "%SERVICEDIR%\train_l1_60s_model_v6_exec_aligned.py" >nul || goto :fail

echo [2/3] Syntax check...
"%PYEXE%" -m py_compile "%SERVICEDIR%\qmt_walkforward_pandas_compat.py" "%SERVICEDIR%\qmt_walkforward_null_compat_v2.py" "%SERVICEDIR%\qmt_l1_60s_walkforward_v1.py" "%SERVICEDIR%\qmt_l1_60s_walkforward_v2.py" "%SERVICEDIR%\qmt_l1_60s_walkforward_v3.py" "%SERVICEDIR%\train_l1_60s_model_v6_exec_aligned.py"
if errorlevel 1 goto :fail

echo [3/3] Running QMT Tick replay: V4R vs V5R vs V6...
echo Expected lookback: about 10 trading sessions inside 14 calendar days.
echo Existing live recorder / ML daemon / shadow runner are NOT stopped.
echo Test folds are frozen. No test result is used to tune thresholds in this run.
echo V6 result is DEVELOPMENT BACKTEST because its architecture saw earlier Sep-02..Sep-04 research evidence.
set "PYTHONPATH=%INSTALLDIR%;%PYTHONPATH%"
cd /d "%INSTALLDIR%"
"%PYEXE%" -u -c "import services.qmt_walkforward_pandas_compat; import services.qmt_l1_60s_walkforward_v3 as m; import services.qmt_walkforward_null_compat_v2 as nf; nf.apply(); raise SystemExit(m.main())" --days 14
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (
  echo [PASS] V3 same-fold historical comparison completed and cloud sync was attempted.
  echo [CHECK] Compare Accuracy + Coverage + Net Edge + exact ask-to-future-bid edge.
  echo [RULE] No upgrade unless Accuracy and Net Edge improve with non-trivial coverage.
  echo [IMPORTANT] Historical V6 results can eliminate bad ideas but cannot certify pristine OOS.
) else (
  echo [ERROR] Historical V3 comparison failed with rc=%RC%.
  echo Existing live runtime was not stopped or replaced.
)
echo.
pause
exit /b %RC%

:fail
echo [ERROR] Download or validation failed. Existing runtime was untouched.
pause
exit /b 1
