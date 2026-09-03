@echo off
setlocal EnableExtensions
chcp 65001 >nul

echo ===============================================
echo A-Stock Morning Priority ML Patch + L2 Cache Fix
echo Auction capture + 09:30-10:30 hard OOS gate
 echo ===============================================
set "INSTALLDIR=%LOCALAPPDATA%\AStockQMT"
set "SERVICEDIR=%INSTALLDIR%\services"
set "MODULEDIR=%INSTALLDIR%\modules"
set "RUNTIME=%INSTALLDIR%\runtime"
set "STARTUPDIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "BASE=https://raw.githubusercontent.com/richard663-ui/-a-stock-app/main"
set "REC=%SERVICEDIR%\qmt_l2_training_recorder_v6.py"
set "TRAIN=%SERVICEDIR%\train_l2_60s_model_v5.py"
set "AUTO=%SERVICEDIR%\l2_ml_autotrain_daemon_v3.py"
set "L2MOD=%MODULEDIR%\qmt_level2.py"
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
if not exist "%MODULEDIR%" mkdir "%MODULEDIR%" >nul 2>&1
if not exist "%RUNTIME%" mkdir "%RUNTIME%" >nul 2>&1

echo [1/6] Downloading morning-priority recorder/trainer and L2 cache fix...
curl.exe -L --fail --retry 3 -o "%TEMP%\l2recv6.py" "%BASE%/services/qmt_l2_training_recorder_v6.py" || goto :fail
curl.exe -L --fail --retry 3 -o "%TEMP%\l2train5.py" "%BASE%/services/train_l2_60s_model_v5.py" || goto :fail
curl.exe -L --fail --retry 3 -o "%TEMP%\l2auto3.py" "%BASE%/services/l2_ml_autotrain_daemon_v3.py" || goto :fail
curl.exe -L --fail --retry 3 -o "%TEMP%\qmtl2fix.py" "%BASE%/modules/qmt_level2.py" || goto :fail
findstr /C:"l2-training-recorder-v6-morning-auction-20260903" "%TEMP%\l2recv6.py" >nul || goto :fail
findstr /C:"l2-60s-trainer-v5-morning-gated-20260903" "%TEMP%\l2train5.py" >nul || goto :fail
findstr /C:"l2-ml-autotrain-v3-morning-gated-20260903" "%TEMP%\l2auto3.py" >nul || goto :fail
findstr /C:"get_l2_cache" "%TEMP%\qmtl2fix.py" >nul || goto :fail

echo [2/6] Syntax check...
"%PYEXE%" -m py_compile "%TEMP%\l2recv6.py" "%TEMP%\l2train5.py" "%TEMP%\l2auto3.py" "%TEMP%\qmtl2fix.py"
if errorlevel 1 goto :syntax

echo [3/6] Installing...
copy /Y "%TEMP%\l2recv6.py" "%REC%" >nul || goto :fail
copy /Y "%TEMP%\l2train5.py" "%TRAIN%" >nul || goto :fail
copy /Y "%TEMP%\l2auto3.py" "%AUTO%" >nul || goto :fail
copy /Y "%TEMP%\qmtl2fix.py" "%L2MOD%" >nul || goto :fail

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

echo [4/6] Rebuilding hidden watchdogs...
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

echo [5/6] Verifying background services...
timeout /t 6 /nobreak >nul
findstr /C:"l2-training-recorder-v6-morning-auction-20260903" "%LOGFILE%" >nul 2>&1
if errorlevel 1 (echo [WARN] Recorder installed; marker not visible yet.) else (echo [PASS] Morning-priority L2 recorder active.)
findstr /C:"l2-ml-autotrain-v3-morning-gated-20260903" "%AUTOLOG%" >nul 2>&1
if errorlevel 1 (echo [WARN] ML daemon installed; marker not visible yet.) else (echo [PASS] Morning-gated ML daemon active.)

echo [6/6] Direct QMT Level-2 diagnostic on 301236.SZ...
set "PYTHONPATH=%INSTALLDIR%;%PYTHONPATH%"
"%PYEXE%" -c "import time; from modules.qmt_level2 import QMTLevel2Manager; m=QMTLevel2Manager(); st=m.switch('301236.SZ'); time.sleep(2); s=m.snapshot(); c=s.get('counts',{}); caps=s.get('capabilities',{}); print('[L2] transaction=',c.get('l2transaction',0),' order=',c.get('l2order',0),' quote=',c.get('l2quote',0)); print('[L2] tx_sub=',caps.get('l2transaction',{}).get('subscription_id'),' tx_error=',caps.get('l2transaction',{}).get('error')); print('[L2] order_sub=',caps.get('l2order',{}).get('subscription_id'),' order_error=',caps.get('l2order',{}).get('error')); ok=(c.get('l2transaction',0)>0 or c.get('l2order',0)>0); m.stop(); raise SystemExit(0 if ok else 2)"
if errorlevel 2 (
  echo [WARN] QMT is connected but no transaction/order L2 reached Python yet.
  echo This now points to broker Level-2 permission/client capability rather than the ML code.
) else (
  echo [PASS] TRUE QMT Level-2 transaction/order data reached Python.
)

echo.
echo [OK] Morning-priority + L2 cache-poll fix configured.
echo The manager now SUBSCRIBES Level-2 and also drains get_l2_transaction/get_l2_order/get_l2_quote caches.
echo ndarray/DataFrame L2 payloads are accepted instead of silently discarded.
echo 09:15-09:25 opening auction is recorded separately.
echo 09:30-10:30 is a mandatory OOS readiness gate.
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
