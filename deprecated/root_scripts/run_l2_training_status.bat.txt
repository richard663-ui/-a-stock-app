@echo off
setlocal
chcp 65001 >nul
set "INSTALLDIR=%LOCALAPPDATA%\AStockQMT"
set "SCRIPT=%INSTALLDIR%\services\l2_training_status_v2.py"
set "PYEXE="
for /f "delims=" %%I in ('py -c "import sys;print(sys.executable)" 2^>nul') do set "PYEXE=%%I"
if not defined PYEXE for /f "delims=" %%I in ('python -c "import sys;print(sys.executable)" 2^>nul') do set "PYEXE=%%I"
if not defined PYEXE echo Python not found & pause & exit /b 1
if not exist "%SCRIPT%" echo Training pipeline not installed. Run update_l2_training_pipeline.bat first. & pause & exit /b 1
"%PYEXE%" "%SCRIPT%"
echo.
pause
