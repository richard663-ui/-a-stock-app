@echo off
setlocal
chcp 65001 >nul
set "INSTALLDIR=%LOCALAPPDATA%\AStockQMT"
set "SCRIPT=%INSTALLDIR%\services\train_l2_60s_model_v3.py"
set "PYEXE="
for /f "delims=" %%I in ('py -c "import sys;print(sys.executable)" 2^>nul') do set "PYEXE=%%I"
if not defined PYEXE for /f "delims=" %%I in ('python -c "import sys;print(sys.executable)" 2^>nul') do set "PYEXE=%%I"
if not defined PYEXE echo Python not found & pause & exit /b 1
if not exist "%SCRIPT%" echo Training pipeline not installed. Run update_l2_training_pipeline.bat first. & pause & exit /b 1

echo ==============================================
echo Priority ML model: 301236.SZ
 echo ==============================================
"%PYEXE%" "%SCRIPT%" --symbol 301236.SZ --min-samples 200

echo.
echo ==============================================
echo Pooled ML model: ALL collected stocks
 echo ==============================================
"%PYEXE%" "%SCRIPT%" --symbol ALL --min-samples 300

echo.
echo V3 uses chronological/purged splits, non-overlapping test metrics,
echo and probability-threshold abstention curves.
echo Research only. Nothing was deployed to live trading.
pause
