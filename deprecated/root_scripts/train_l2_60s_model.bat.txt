@echo off
setlocal
chcp 65001 >nul
set "INSTALLDIR=%LOCALAPPDATA%\AStockQMT"
set "SCRIPT=%INSTALLDIR%\services\train_l2_60s_model_v4.py"
set "PYEXE="
for /f "delims=" %%I in ('py -c "import sys;print(sys.executable)" 2^>nul') do set "PYEXE=%%I"
if not defined PYEXE for /f "delims=" %%I in ('python -c "import sys;print(sys.executable)" 2^>nul') do set "PYEXE=%%I"
if not defined PYEXE echo Python not found & pause & exit /b 1
if not exist "%SCRIPT%" echo Training pipeline not installed. Run update_l2_training_pipeline.bat first. & pause & exit /b 1

echo ==============================================
echo Priority ML model: 301236.SZ
 echo ==============================================
"%PYEXE%" "%SCRIPT%" --symbol 301236.SZ --min-samples 200 --hurdle-bp 2.0

echo.
echo ==============================================
echo Pooled ML model: ALL collected stocks
 echo ==============================================
"%PYEXE%" "%SCRIPT%" --symbol ALL --min-samples 300 --hurdle-bp 2.0

echo.
echo V4 rules:
echo - validation chooses probability threshold; test never chooses the best threshold
echo - +60s actionable labels must clear noise band and 2bp execution hurdle
echo - report gross and net edge, calibration and per-symbol pooled stability
echo - chronological/purged splits and non-overlapping 60s test remain mandatory
echo Research only. Nothing is deployed to live trading.
pause
