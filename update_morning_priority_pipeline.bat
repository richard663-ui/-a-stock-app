@echo off
setlocal EnableExtensions
chcp 65001 >nul

echo ===============================================
echo A-Stock Morning Priority ML Patch
echo Auction capture + 09:30-10:30 hard OOS gate
 echo ===============================================
set "INSTALLDIR=%LOCALAPPDATA%\AStockQMT"
set "SERVICEDIR=%INSTALLDIR%\services"
set "RUNTIME=%INSTALLDIR%\runtime"
set "STARTUPDIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "BASE=https://raw.githubusercontent.com/richard663-ui/-a-stock-app/main"
set "REC=%SERVICEDIR%\qmt_l2_training_recorder_v6.py"
set "TRAIN=%SERVICEDIR%\train_l2_60s_model_v5.py"
set "AUTO=%SERVICEDIR%\l2_ml_autotrain_daemon_v3.py"
set "WATCHDOG=%RUNTIME%\AStockL2TrainingWatchdog.cmd"
set "AUTOWATCHDOG=%RUNTIME%\AStockL2MLAutoTrainWatchdog.cmd"
set "LOGFILE=%RUNTIME%\l2_training_recorder.log"
set "AUTOLOG=%RUNTIME%\l2_ml_autotrain_daemon.log"
set "STARTVBS=%STARTUPDIR%\AStockL2TrainingRecorder.vbs"
set "AUTOVBS=%STARTUPDIR%\AStockL2MLAutoTrain.vbs"
set "KILLVBS=%TEMP%\astock_morning_patch_stop.vbs"

if not exist "%INSTALLDIR%" (
  echo [ERROR] Existing AStock QMT runtime not found: %INSTALLDIR%
  echo Run update_l2_training_pipeline.bat first.
  pause
  exit /b 1
)

set "PYEXE="
for /f "delims=" %%I in ('py -c "import sys;print(sys.executable)" 2^>nul') do set "PYEXE=%%I"
if not defined PYEXE for /f "delims=" %%I in ('python -c "import sys;print(sys.executable)" 2^>nul') do set "PYEXE=%%I"
if not defined PYEXE echo [ERROR] Python not found. & pause & exit /b 1

"%PYEXE%" -c "from xtquant import xtdata" >nul 2>&1
if errorlevel 1 echo [ERROR] This Python cannot import xtquant. & pause & exit /b 1

if not exist "%SERVICEDIR%" mkdir "%SERVICEDIR%" >nul 2>&1
if not exist "%RUNTIME%" mkdir "%RUNTIME%" >nul 2>&1

echo [1/5] Downloading morning-priority recorder/trainer...
curl.exe -L --fail --retry 3 -o "%TEMP%\l2recv6.py" "%BASE%/services/qmt_l2_training_recorder_v6.py" || goto :fail
curl.exe -L --fail --retry 3 -o "%TEMP%\l2train5.py" "%BASE%/services/train_l2_60s_model_v5.py" || goto :fail
curl.exe -L --fail --retry 3 -o "%TEMP%\l2auto3.py" "%BASE%/services/l2_ml_autotrain_daemon_v3.py" || goto :fail
findstr /C:"l2-training-recorder-v6-morning-auction-20260903" "%TEMP%\l2recv6.py" >nul || goto :fail
findstr /C:"l2-60s-trainer-v5-morning-gated-20260903" "%TEMP%\l2train5.py" >nul || goto :fail
findstr /C:"l2-ml-autotrain-v3-morning-gated-20260903" "%TEMP%\l2auto3.py" >nul || goto :fail

echo [2/5] Syntax check...
"%PYEXE%" -m py_compile "%TEMP%\l2recv6.py" "%TEMP%\l2train5.py" "%TEMP%\l2auto3.py"
if errorlevel 1 goto :syntax

echo [3/5] Installing...
copy /Y "%TEMP%\l2recv6.py" "%REC%" >nul || goto :fail
copy /Y "%TEMP%\l2train5.py" "%TRAIN%" >nul || goto :fail
copy /Y "%TEMP%\l2auto3.py" "%AUTO%" >nul || goto :fail

(
  echo On Error Resume Next
  echo Set svc = GetObject("winmgmts:\\.\root\cimv2"^)
  echo For Each p In svc.ExecQuery("Select * from Win32_Process"^)
  echo   cmd = LCase("" ^& p.CommandLine^)
  echo   If InStr(cmd, "qmt_l2_training_recorder"^) ^> 0 Or InStr(cmd, "astockl2trainingwatchdog.cmd"^) ^> 0 Or InStr(cmd, "l2_ml_autotrain_daemon"^) ^> 0 Or InStr(cmd, "astockl2mlautotrainwatchdog.cmd"^) ^> 0 Then p.Terminate
  echo Next
) > "%KILLVBS%"
cscript.exe //nologo "%KILLVBS%" >nul 2>&1
del /q "%KILLVBS%" >nul 2>&1
timeout /t 2 /nobreak >nul

echo [4/5] Rebuilding hidden watchdogs...
(
  echo @echo off
  echo set "PYTHONPATH=%INSTALLDIR%;%%PYTHONPATH%%"
  echo cd /d "%INSTALLDIR%"
  echo :loop
  echo "%PYEXE%" -u "%REC%" ^>^> "%LOGFILE%" 2^>^&1
  echo echo [%%DATE%% %%TIME%%] morning recorder exited %%ERRORLEVEL%% - restart in 5s ^>^> "%LOGFILE%"
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
  echo "%PYEXE%" -u "%AUTO%" ^>^> "%AUTOLOG%" 2^>^&1
  echo echo [%%DATE%% %%TIME%%] morning ML daemon exited %%ERRORLEVEL%% - restart in 10s ^>^> "%AUTOLOG%"
  echo timeout /t 10 /nobreak ^>nul
  echo goto loop
) > "%AUTOWATCHDOG%"
(
  echo Set sh = CreateObject("WScript.Shell"^)
  echo sh.Run "cmd.exe /d /c ""%AUTOWATCHDOG%""", 0, False
) > "%AUTOVBS%"
wscript.exe "%STARTVBS%"
wscript.exe "%AUTOVBS%"

echo [5/5] Verifying...
timeout /t 6 /nobreak >nul
findstr /C:"l2-training-recorder-v6-morning-auction-20260903" "%LOGFILE%" >nul 2>&1
if errorlevel 1 (echo [WARN] Recorder installed; marker not visible yet.) else (echo [PASS] Morning-priority L2 recorder active.)
findstr /C:"l2-ml-autotrain-v3-morning-gated-20260903" "%AUTOLOG%" >nul 2>&1
if errorlevel 1 (echo [WARN] ML daemon installed; marker not visible yet.) else (echo [PASS] Morning-gated ML daemon active.)

echo.
echo [OK] Morning-priority patch configured.
echo 09:15-09:25 opening auction is recorded separately.
echo 09:30-10:30 is a mandatory OOS readiness gate.
echo Opening-auction rows are excluded from the continuous-auction model.
echo Phone switching is still NOT required.
echo Nothing is auto-deployed to live trading.
pause
exit /b 0

:syntax
echo [ERROR] Syntax check failed. Existing runtime was not replaced.
pause
exit /b 1
:fail
echo [ERROR] Morning-priority update failed.
pause
exit /b 1
