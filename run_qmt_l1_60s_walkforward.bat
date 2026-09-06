@echo off
setlocal EnableExtensions
chcp 65001 >nul

echo ======================================================
echo A-Stock QMT 60s Research Suite
echo V3 + iMACD + state filter + meta-label + cross-rank
echo Same frozen folds / 2bp / 60s non-overlap / no trading
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

echo [1/7] Downloading frozen research stack...
for %%F in (qmt_walkforward_pandas_compat.py qmt_walkforward_null_compat_v2.py qmt_l1_60s_walkforward_v1.py qmt_l1_60s_walkforward_v2.py qmt_l1_60s_walkforward_v3.py imacd_research_audit_v1.py run_imacd_audit_v1.py v4r_imacd_filter_audit_v1.py v4r_meta_label_audit_v1.py v4r_cross_sectional_rank_audit_v1.py train_l1_60s_model_v1.py train_l1_60s_model_v2.py train_l1_60s_model_v3.py train_l1_60s_model_v4.py train_l1_60s_model_v4r.py train_l1_60s_model_v5_challenger.py train_l1_60s_model_v5r.py train_l1_60s_model_v6_exec_aligned.py train_l2_60s_model_v3.py train_l2_60s_model_v4.py train_l2_60s_model_v5.py) do (
  curl.exe -L --fail --retry 3 -o "%SERVICEDIR%\%%F" "%BASE%/services/%%F" || goto :fail
)
findstr /C:"qmt-l1-60s-walkforward-v3-v6-comparison-20260906" "%SERVICEDIR%\qmt_l1_60s_walkforward_v3.py" >nul || goto :fail
findstr /C:"imacd-state-ranking-audit-v1-20260906" "%SERVICEDIR%\imacd_research_audit_v1.py" >nul || goto :fail
findstr /C:"v4r-imacd-regime-filter-audit-v1-20260906" "%SERVICEDIR%\v4r_imacd_filter_audit_v1.py" >nul || goto :fail
findstr /C:"v4r-meta-label-exec-audit-v1-20260906" "%SERVICEDIR%\v4r_meta_label_audit_v1.py" >nul || goto :fail
findstr /C:"v4r-cross-sectional-rank-audit-v1-20260906" "%SERVICEDIR%\v4r_cross_sectional_rank_audit_v1.py" >nul || goto :fail

echo [2/7] Syntax check...
"%PYEXE%" -m py_compile "%SERVICEDIR%\qmt_l1_60s_walkforward_v3.py" "%SERVICEDIR%\imacd_research_audit_v1.py" "%SERVICEDIR%\run_imacd_audit_v1.py" "%SERVICEDIR%\v4r_imacd_filter_audit_v1.py" "%SERVICEDIR%\v4r_meta_label_audit_v1.py" "%SERVICEDIR%\v4r_cross_sectional_rank_audit_v1.py"
if errorlevel 1 goto :fail

set "PYTHONPATH=%INSTALLDIR%;%PYTHONPATH%"
cd /d "%INSTALLDIR%"

set "FRESHV3=0"
for /f "delims=" %%I in ('"%PYEXE%" -c "from pathlib import Path;import time;p=Path.home()/'AStockData'/'qmt_l1_walkforward_v3';r=sorted([x/'walkforward_report_v3.json' for x in p.glob('*') if (x/'walkforward_report_v3.json').exists()],reverse=True) if p.exists() else [];print(1 if r and time.time()-r[0].stat().st_mtime ^< 43200 else 0)"') do set "FRESHV3=%%I"

if "%FRESHV3%"=="1" (
  echo [3/7] Reusing the fresh V3 Tick dataset already on this PC.
) else (
  echo [3/7] No fresh V3 dataset found; running 10-session QMT Tick replay first...
  echo Existing live recorder / ML daemon / shadow runner are NOT stopped.
  "%PYEXE%" -u -c "import services.qmt_walkforward_pandas_compat; import services.qmt_l1_60s_walkforward_v3 as m; import services.qmt_walkforward_null_compat_v2 as nf; nf.apply(); raise SystemExit(m.main())" --days 14
  if errorlevel 1 goto :runfail
)

echo [4/7] Running fixed iMACD predictor audit for reference...
set "ASTOCK_SKIP_RESEARCH_CHAIN=1"
"%PYEXE%" -u -m services.run_imacd_audit_v1
set "ASTOCK_SKIP_RESEARCH_CHAIN="
if errorlevel 1 goto :runfail

echo [5/7] Running V4R x iMACD validation-only regime-filter audit...
echo MACD does NOT choose direction here. It can only keep/reject an already-frozen V4R signal.
"%PYEXE%" -u -m services.v4r_imacd_filter_audit_v1
if errorlevel 1 goto :runfail

echo [6/7] Running V4R validation-split meta-label audit...
echo V4R chooses direction; meta model only predicts whether the frozen signal is worth trading.
echo First 60%% of validation signals fit meta model; last 40%% select threshold; TEST is untouched.
"%PYEXE%" -u -m services.v4r_meta_label_audit_v1
if errorlevel 1 goto :runfail

echo [7/7] Running fixed-universe cross-sectional ranking audit...
echo Absolute probability cutoffs do not choose trades; each 60s bucket ranks the 8-stock universe relatively.
echo Policy family is predeclared; validation chooses; TEST is untouched.
"%PYEXE%" -u -m services.v4r_cross_sectional_rank_audit_v1
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (
  echo [PASS] Research suite completed and cloud sync was attempted.
  echo [RULE] Upgrade requires Accuracy AND Net Edge improvement plus all hard gates.
  echo [RULE] Historical pass enters research/shadow only; production still requires unseen prospective days.
) else (
  echo [ERROR] Cross-sectional ranking audit failed with rc=%RC%.
  echo Existing production/live runtime was not modified.
)
echo.
pause
exit /b %RC%

:runfail
echo [ERROR] Research source/audit failed. Production was not modified.
pause
exit /b 1

:fail
echo [ERROR] Download or validation failed. Existing runtime was untouched.
pause
exit /b 1
