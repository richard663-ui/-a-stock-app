@echo off
setlocal EnableExtensions
chcp 65001 >nul

echo ===============================================
echo A-Stock Level-2 Trainable 60s Pipeline
 echo ===============================================
set "INSTALLDIR=%LOCALAPPDATA%\AStockQMT"
set "SERVICEDIR=%INSTALLDIR%\services"
set "MODULEDIR=%INSTALLDIR%\modules"
set "RUNTIME=%INSTALLDIR%\runtime"
set "STARTUPDIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "WATCHDOG=%RUNTIME%\AStockL2TrainingWatchdog.cmd"
set "STARTVBS=%STARTUPDIR%\AStockL2TrainingRecorder.vbs"
set "LOGFILE=%RUNTIME%\l2_training_recorder.log"
set "BASE=https://raw.githubusercontent.com/richard663-ui/-a-stock-app/main"
set "REC=%SERVICEDIR%\qmt_l2_training_recorder_v2.py"
set "TRAIN=%SERVICEDIR%\train_l2_60s_model_v2.py"
set "STATUS=%SERVICEDIR%\l2_training_status_v2.py"

if not exist "%INSTALLDIR%" echo [ERROR] AStock runtime missing & pause & exit /b 1
set "PYEXE="
for /f "delims=" %%I in ('py -c "import sys;print(sys.executable)" 2^>nul') do set "PYEXE=%%I"
if not defined PYEXE for /f "delims=" %%I in ('python -c "import sys;print(sys.executable)" 2^>nul') do set "PYEXE=%%I"
if not defined PYEXE echo [ERROR] Python not found & pause & exit /b 1
"%PYEXE%" -c "from xtquant import xtdata" >nul 2>&1
if errorlevel 1 echo [ERROR] This Python cannot import xtquant. Use the QMT bridge Python. & pause & exit /b 1
if not exist "%SERVICEDIR%" mkdir "%SERVICEDIR%"
if not exist "%RUNTIME%" mkdir "%RUNTIME%"

echo [1/6] Downloading...
curl.exe -L --fail --retry 3 -o "%TEMP%\l2rec.py" "%BASE%/services/qmt_l2_training_recorder_v2.py" || goto :fail
curl.exe -L --fail --retry 3 -o "%TEMP%\l2train.py" "%BASE%/services/train_l2_60s_model_v2.py" || goto :fail
curl.exe -L --fail --retry 3 -o "%TEMP%\l2status.py" "%BASE%/services/l2_training_status_v2.py" || goto :fail
curl.exe -L --fail --retry 3 -o "%TEMP%\mlreq.txt" "%BASE%/requirements_research_ml.txt" || goto :fail
curl.exe -L --fail --retry 3 -o "%TEMP%\dir18.py" "%BASE%/modules/direction_v18.py" || goto :fail
curl.exe -L --fail --retry 3 -o "%TEMP%\l2mod.py" "%BASE%/modules/qmt_level2.py" || goto :fail
curl.exe -L --fail --retry 3 -o "%TEMP%\qmtlive.py" "%BASE%/modules/qmt_live.py" || goto :fail
findstr /C:"l2-training-recorder-v2-20260901" "%TEMP%\l2rec.py" >nul || goto :fail

echo [2/6] Syntax check...
"%PYEXE%" -m py_compile "%TEMP%\l2rec.py" "%TEMP%\l2train.py" "%TEMP%\l2status.py" "%TEMP%\dir18.py" "%TEMP%\l2mod.py" "%TEMP%\qmtlive.py"
if errorlevel 1 goto :syntax

echo [3/6] ML dependencies...
"%PYEXE%" -c "import sklearn,joblib" >nul 2>&1
if errorlevel 1 "%PYEXE%" -m pip install --disable-pip-version-check -r "%TEMP%\mlreq.txt"
if errorlevel 1 goto :mlfail

echo [4/6] Installing...
copy /Y "%TEMP%\l2rec.py" "%REC%" >nul || goto :fail
copy /Y "%TEMP%\l2train.py" "%TRAIN%" >nul || goto :fail
copy /Y "%TEMP%\l2status.py" "%STATUS%" >nul || goto :fail
copy /Y "%TEMP%\dir18.py" "%MODULEDIR%\direction_v18.py" >nul || goto :fail
copy /Y "%TEMP%\l2mod.py" "%MODULEDIR%\qmt_level2.py" >nul || goto :fail
copy /Y "%TEMP%\qmtlive.py" "%MODULEDIR%\qmt_live.py" >nul || goto :fail

echo [5/6] Starting persistent recorder...
wmic process where "CommandLine like '%%qmt_l2_training_recorder%%'" call terminate >nul 2>&1
wmic process where "CommandLine like '%%AStockL2TrainingWatchdog%%'" call terminate >nul 2>&1
timeout /t 2 /nobreak >nul
(
 echo @echo off
 echo set "PYTHONPATH=%INSTALLDIR%;%%PYTHONPATH%%"
 echo cd /d "%INSTALLDIR%"
 echo :loop
 echo "%PYEXE%" -u "%REC%" ^>^> "%LOGFILE%" 2^>^&1
 echo timeout /t 5 /nobreak ^>nul
 echo goto loop
) > "%WATCHDOG%"
(
 echo Set sh = CreateObject("WScript.Shell"^)
 echo sh.Run "cmd.exe /d /c ""%WATCHDOG%""", 0, False
) > "%STARTVBS%"
wscript.exe "%STARTVBS%"

echo [6/6] Verifying...
timeout /t 6 /nobreak >nul
findstr /C:"l2-training-recorder-v2-20260901" "%LOGFILE%" >nul 2>&1
if errorlevel 1 (echo [WARN] Installed; marker not visible yet. Keep QMT logged in.) else (echo [PASS] L2 training recorder active.)
echo.
echo [OK] 301236.SZ priority, up to 8 watchlist stocks.
echo Primary label: smoothed mid-price around +60s.
echo Old lastPrice label kept only as comparison. No ML model auto-deployed.
pause
exit /b 0

:syntax
echo [ERROR] Syntax failed. Existing runtime not replaced.
pause
exit /b 1
:mlfail
echo [ERROR] scikit-learn install failed.
pause
exit /b 1
:fail
echo [ERROR] Update failed.
pause
exit /b 1
