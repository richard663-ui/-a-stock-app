@echo off
setlocal EnableExtensions
chcp 65001 >nul

echo ===============================================
echo A-Stock Level-2 Trainable 60s Pipeline V4
echo Efficient recorder + automatic ML training
echo ===============================================
set "INSTALLDIR=%LOCALAPPDATA%\AStockQMT"
set "SERVICEDIR=%INSTALLDIR%\services"
set "MODULEDIR=%INSTALLDIR%\modules"
set "RUNTIME=%INSTALLDIR%\runtime"
set "STARTUPDIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "WATCHDOG=%RUNTIME%\AStockL2TrainingWatchdog.cmd"
set "STARTVBS=%STARTUPDIR%\AStockL2TrainingRecorder.vbs"
set "LOGFILE=%RUNTIME%\l2_training_recorder.log"
set "AUTOWATCHDOG=%RUNTIME%\AStockL2MLAutoTrainWatchdog.cmd"
set "AUTOVBS=%STARTUPDIR%\AStockL2MLAutoTrain.vbs"
set "AUTOLOG=%RUNTIME%\l2_ml_autotrain_daemon.log"
set "KILLVBS=%TEMP%\astock_stop_l2_training.vbs"
set "BASE=https://raw.githubusercontent.com/richard663-ui/-a-stock-app/main"
set "REC=%SERVICEDIR%\qmt_l2_training_recorder_v4.py"
set "REC3=%SERVICEDIR%\qmt_l2_training_recorder_v3.py"
set "RECBASE=%SERVICEDIR%\qmt_l2_training_recorder_v2.py"
set "TRAIN=%SERVICEDIR%\train_l2_60s_model_v3.py"
set "AUTOTRAIN=%SERVICEDIR%\l2_ml_autotrain_daemon.py"
set "STATUS=%SERVICEDIR%\l2_training_status_v2.py"
set "PERSISTDIR=%USERPROFILE%\.a_stock_qmt"
set "TRAININGLIST=%PERSISTDIR%\training_watchlist.txt"
set "RESEARCHLIST=%PERSISTDIR%\research_watchlist.txt"

if not exist "%INSTALLDIR%" (
  echo [ERROR] AStock runtime missing: %INSTALLDIR%
  pause
  exit /b 1
)

set "PYEXE="
for /f "delims=" %%I in ('py -c "import sys;print(sys.executable)" 2^>nul') do set "PYEXE=%%I"
if not defined PYEXE for /f "delims=" %%I in ('python -c "import sys;print(sys.executable)" 2^>nul') do set "PYEXE=%%I"
if not defined PYEXE (
  echo [ERROR] Python not found.
  pause
  exit /b 1
)

"%PYEXE%" -c "from xtquant import xtdata" >nul 2>&1
if errorlevel 1 (
  echo [ERROR] This Python cannot import xtquant.
  echo Use the same Python environment that already runs your QMT bridge.
  pause
  exit /b 1
)

if not exist "%SERVICEDIR%" mkdir "%SERVICEDIR%" >nul 2>&1
if not exist "%MODULEDIR%" mkdir "%MODULEDIR%" >nul 2>&1
if not exist "%RUNTIME%" mkdir "%RUNTIME%" >nul 2>&1
if not exist "%PERSISTDIR%" mkdir "%PERSISTDIR%" >nul 2>&1

echo [0/7] Writing autonomous background watchlists...
(
  echo # AStock autonomous research watchlist
  echo # Phone switching is NOT required for backtest collection.
  echo 301236.SZ
  echo 300308.SZ
  echo 000400.SZ
  echo 600522.SH
  echo 601179.SH
  echo 600105.SH
  echo 002916.SZ
  echo 000811.SZ
) > "%RESEARCHLIST%"
(
  echo # AStock autonomous Level-2 ML training watchlist
  echo # Stable basket is intentional: continuous samples are better than random rotation.
  echo 301236.SZ
  echo 300308.SZ
  echo 000400.SZ
  echo 600522.SH
  echo 601179.SH
  echo 600105.SH
  echo 002916.SZ
  echo 000811.SZ
) > "%TRAININGLIST%"

echo [1/7] Downloading efficient recorder and ML trainer...
curl.exe -L --fail --retry 3 -o "%TEMP%\l2recv4.py" "%BASE%/services/qmt_l2_training_recorder_v4.py" || goto :fail
curl.exe -L --fail --retry 3 -o "%TEMP%\l2recv3.py" "%BASE%/services/qmt_l2_training_recorder_v3.py" || goto :fail
curl.exe -L --fail --retry 3 -o "%TEMP%\l2recv2.py" "%BASE%/services/qmt_l2_training_recorder_v2.py" || goto :fail
curl.exe -L --fail --retry 3 -o "%TEMP%\l2train3.py" "%BASE%/services/train_l2_60s_model_v3.py" || goto :fail
curl.exe -L --fail --retry 3 -o "%TEMP%\l2auto.py" "%BASE%/services/l2_ml_autotrain_daemon.py" || goto :fail
curl.exe -L --fail --retry 3 -o "%TEMP%\l2status.py" "%BASE%/services/l2_training_status_v2.py" || goto :fail
curl.exe -L --fail --retry 3 -o "%TEMP%\mlreq.txt" "%BASE%/requirements_research_ml.txt" || goto :fail
curl.exe -L --fail --retry 3 -o "%TEMP%\dir18.py" "%BASE%/modules/direction_v18.py" || goto :fail
curl.exe -L --fail --retry 3 -o "%TEMP%\l2mod.py" "%BASE%/modules/qmt_level2.py" || goto :fail
curl.exe -L --fail --retry 3 -o "%TEMP%\qmtlive.py" "%BASE%/modules/qmt_live.py" || goto :fail
findstr /C:"l2-training-recorder-v4-efficient-20260902" "%TEMP%\l2recv4.py" >nul || goto :fail
findstr /C:"l2-training-recorder-v3-freshness-20260901" "%TEMP%\l2recv3.py" >nul || goto :fail
findstr /C:"l2-60s-trainer-v3-purged-selective-20260902" "%TEMP%\l2train3.py" >nul || goto :fail
findstr /C:"AStock L2 ML auto-trainer started" "%TEMP%\l2auto.py" >nul || goto :fail

echo [2/7] Syntax check...
"%PYEXE%" -m py_compile "%TEMP%\l2recv4.py" "%TEMP%\l2recv3.py" "%TEMP%\l2recv2.py" "%TEMP%\l2train3.py" "%TEMP%\l2auto.py" "%TEMP%\l2status.py" "%TEMP%\dir18.py" "%TEMP%\l2mod.py" "%TEMP%\qmtlive.py"
if errorlevel 1 goto :syntax

echo [3/7] ML dependencies...
"%PYEXE%" -c "import sklearn,joblib" >nul 2>&1
if errorlevel 1 (
  "%PYEXE%" -m pip install --disable-pip-version-check -r "%TEMP%\mlreq.txt"
  if errorlevel 1 goto :mlfail
)

echo [4/7] Installing...
copy /Y "%TEMP%\l2recv4.py" "%REC%" >nul || goto :fail
copy /Y "%TEMP%\l2recv3.py" "%REC3%" >nul || goto :fail
copy /Y "%TEMP%\l2recv2.py" "%RECBASE%" >nul || goto :fail
copy /Y "%TEMP%\l2train3.py" "%TRAIN%" >nul || goto :fail
copy /Y "%TEMP%\l2auto.py" "%AUTOTRAIN%" >nul || goto :fail
copy /Y "%TEMP%\l2status.py" "%STATUS%" >nul || goto :fail
copy /Y "%TEMP%\dir18.py" "%MODULEDIR%\direction_v18.py" >nul || goto :fail
copy /Y "%TEMP%\l2mod.py" "%MODULEDIR%\qmt_level2.py" >nul || goto :fail
copy /Y "%TEMP%\qmtlive.py" "%MODULEDIR%\qmt_live.py" >nul || goto :fail

echo [5/7] Restarting L2 recorder and ML auto-trainer...
(
  echo On Error Resume Next
  echo Set svc = GetObject("winmgmts:\\.\root\cimv2"^)
  echo For Each p In svc.ExecQuery("Select * from Win32_Process"^)
  echo   cmd = LCase("" ^& p.CommandLine^)
  echo   If InStr(cmd, "qmt_l2_training_recorder"^) ^> 0 Or InStr(cmd, "astockl2trainingwatchdog.cmd"^) ^> 0 Or InStr(cmd, "l2_ml_autotrain_daemon.py"^) ^> 0 Or InStr(cmd, "astockl2mlautotrainwatchdog.cmd"^) ^> 0 Then
  echo     p.Terminate
  echo   End If
  echo Next
) > "%KILLVBS%"
cscript.exe //nologo "%KILLVBS%" >nul 2>&1
del /q "%KILLVBS%" >nul 2>&1
timeout /t 2 /nobreak >nul
(
  echo @echo off
  echo set "PYTHONPATH=%INSTALLDIR%;%%PYTHONPATH%%"
  echo cd /d "%INSTALLDIR%"
  echo :loop
  echo "%PYEXE%" -u "%REC%" ^>^> "%LOGFILE%" 2^>^&1
  echo echo [%%DATE%% %%TIME%%] L2 training recorder exited code %%ERRORLEVEL%% - restart in 5s ^>^> "%LOGFILE%"
  echo timeout /t 5 /nobreak ^>nul
  echo goto loop
) > "%WATCHDOG%"
(
  echo Set sh = CreateObject("WScript.Shell"^)
  echo sh.Run "cmd.exe /d /c ""%WATCHDOG%""", 0, False
) > "%STARTVBS%"
(
  echo @echo off
  echo set "PYTHONPATH=%INSTALLDIR%;%%PYTHONPATH%%"
  echo cd /d "%INSTALLDIR%"
  echo :loop
  echo "%PYEXE%" -u "%AUTOTRAIN%" ^>^> "%AUTOLOG%" 2^>^&1
  echo echo [%%DATE%% %%TIME%%] ML auto-trainer exited code %%ERRORLEVEL%% - restart in 10s ^>^> "%AUTOLOG%"
  echo timeout /t 10 /nobreak ^>nul
  echo goto loop
) > "%AUTOWATCHDOG%"
(
  echo Set sh = CreateObject("WScript.Shell"^)
  echo sh.Run "cmd.exe /d /c ""%AUTOWATCHDOG%""", 0, False
) > "%AUTOVBS%"
wscript.exe "%STARTVBS%"
wscript.exe "%AUTOVBS%"

echo [6/7] Verifying L2 recorder...
timeout /t 6 /nobreak >nul
findstr /C:"l2-training-recorder-v4-efficient-20260902" "%LOGFILE%" >nul 2>&1
if errorlevel 1 (
  echo [WARN] L2 recorder installed; marker not visible yet. Keep QMT logged in.
) else (
  echo [PASS] Efficient L2 training recorder active.
)

echo [7/7] Verifying automatic ML trainer...
findstr /C:"AStock L2 ML auto-trainer started" "%AUTOLOG%" >nul 2>&1
if errorlevel 1 (
  echo [WARN] ML auto-trainer installed; startup marker not visible yet.
) else (
  echo [PASS] ML auto-trainer active.
)

echo.
echo [OK] Background L2 + ML autopilot configured.
echo Fixed priorities: 301236.SZ and 300308.SZ.
echo Auxiliary basket: 000400.SZ 600522.SH 601179.SH 600105.SH 002916.SZ 000811.SZ.
echo Phone switching is NOT required for research or ML data collection.
echo 5-second training samples and +60s smoothed-mid labels are unchanged.
echo Raw L2 events are batch-written to reduce SQLite contention.
echo Automatic research training runs at 11:35 and 15:10 when enough data exists.
echo Trainer reports dense and non-overlapping test metrics plus probability/coverage curves.
echo NO ML model is auto-deployed to the phone or trading path.
echo QMT still needs to be open and logged in for real market data.
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
