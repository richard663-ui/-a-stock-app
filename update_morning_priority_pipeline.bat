@echo off
setlocal EnableExtensions
chcp 65001 >nul

echo ======================================================
echo A-Stock Morning Priority ML Runtime - Atomic Update
echo L2 cache fix + freshness fix + async recorder heartbeat
 echo ======================================================
set "INSTALLDIR=%LOCALAPPDATA%\AStockQMT"
set "SERVICEDIR=%INSTALLDIR%\services"
set "MODULEDIR=%INSTALLDIR%\modules"
set "RUNTIME=%INSTALLDIR%\runtime"
set "STARTUPDIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "BASE=https://raw.githubusercontent.com/richard663-ui/-a-stock-app/main"
set "WATCHDOG=%RUNTIME%\AStockL2TrainingWatchdog.cmd"
set "AUTOWATCHDOG=%RUNTIME%\AStockL2MLAutoTrainWatchdog.cmd"
set "LOGFILE=%RUNTIME%\l2_training_recorder.log"
set "AUTOLOG=%RUNTIME%\l2_ml_autotrain_daemon.log"
set "STARTVBS=%STARTUPDIR%\AStockL2TrainingRecorder.vbs"
set "AUTOVBS=%STARTUPDIR%\AStockL2MLAutoTrain.vbs"
set "KILLVBS=%TEMP%\astock_morning_atomic_stop.vbs"

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

echo [1/7] Downloading a CONSISTENT recorder/trainer dependency stack...
for %%F in (qmt_l2_training_recorder_v2.py qmt_l2_training_recorder_v3.py qmt_l2_training_recorder_v4.py qmt_l2_training_recorder_v5.py qmt_l2_training_recorder_v6.py train_l2_60s_model_v3.py train_l2_60s_model_v4.py train_l2_60s_model_v5.py l2_ml_autotrain_daemon.py l2_ml_autotrain_daemon_v3.py) do (
  curl.exe -L --fail --retry 3 -o "%TEMP%\%%F" "%BASE%/services/%%F" || goto :fail_download
)
curl.exe -L --fail --retry 3 -o "%TEMP%\qmt_level2.py" "%BASE%/modules/qmt_level2.py" || goto :fail_download

findstr /C:"l2-training-recorder-v6-morning-auction-20260903c" "%TEMP%\qmt_l2_training_recorder_v6.py" >nul || goto :fail_download
findstr /C:"l2-training-recorder-v5-market-freshness-20260903b" "%TEMP%\qmt_l2_training_recorder_v5.py" >nul || goto :fail_download
findstr /C:"l2-60s-trainer-v5-morning-gated-20260903" "%TEMP%\train_l2_60s_model_v5.py" >nul || goto :fail_download
findstr /C:"l2-ml-autotrain-v3-morning-gated-20260903" "%TEMP%\l2_ml_autotrain_daemon_v3.py" >nul || goto :fail_download
findstr /C:"get_l2_cache" "%TEMP%\qmt_level2.py" >nul || goto :fail_download

echo [2/7] Syntax check for the whole stack...
"%PYEXE%" -m py_compile "%TEMP%\qmt_l2_training_recorder_v2.py" "%TEMP%\qmt_l2_training_recorder_v3.py" "%TEMP%\qmt_l2_training_recorder_v4.py" "%TEMP%\qmt_l2_training_recorder_v5.py" "%TEMP%\qmt_l2_training_recorder_v6.py" "%TEMP%\train_l2_60s_model_v3.py" "%TEMP%\train_l2_60s_model_v4.py" "%TEMP%\train_l2_60s_model_v5.py" "%TEMP%\l2_ml_autotrain_daemon.py" "%TEMP%\l2_ml_autotrain_daemon_v3.py" "%TEMP%\qmt_level2.py"
if errorlevel 1 goto :syntax

REM Only stop the working runtime AFTER every replacement file has downloaded
REM and compiled. A network failure therefore cannot take the current recorder down.
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

echo [3/7] Installing atomically matched Python files...
for %%F in (qmt_l2_training_recorder_v2.py qmt_l2_training_recorder_v3.py qmt_l2_training_recorder_v4.py qmt_l2_training_recorder_v5.py qmt_l2_training_recorder_v6.py train_l2_60s_model_v3.py train_l2_60s_model_v4.py train_l2_60s_model_v5.py l2_ml_autotrain_daemon.py l2_ml_autotrain_daemon_v3.py) do (
  copy /Y "%TEMP%\%%F" "%SERVICEDIR%\%%F" >nul || goto :fail_install
)
copy /Y "%TEMP%\qmt_level2.py" "%MODULEDIR%\qmt_level2.py" >nul || goto :fail_install

set "PYTHONPATH=%INSTALLDIR%;%PYTHONPATH%"
echo [4/7] Direct 301236.SZ Level-2 diagnostic BEFORE the 8-stock recorder starts...
"%PYEXE%" -c "import datetime,time; from modules.qmt_level2 import QMTLevel2Manager; n=datetime.datetime.now(); m=n.hour*60+n.minute; opened=(n.weekday()<5 and ((570<=m<690) or (780<=m<900))); q=QMTLevel2Manager(); q.switch('301236.SZ'); time.sleep(3); s=q.snapshot(); c=s.get('counts',{}); caps=s.get('capabilities',{}); print('[L2] transaction=',c.get('l2transaction',0),' order=',c.get('l2order',0),' quote=',c.get('l2quote',0)); print('[L2] tx_sub=',caps.get('l2transaction',{}).get('subscription_id'),' tx_source=',caps.get('l2transaction',{}).get('source'),' tx_error=',caps.get('l2transaction',{}).get('error')); print('[L2] order_sub=',caps.get('l2order',{}).get('subscription_id'),' order_source=',caps.get('l2order',{}).get('source'),' order_error=',caps.get('l2order',{}).get('error')); ok=(c.get('l2transaction',0)>0 or c.get('l2order',0)>0); q.stop(); raise SystemExit(0 if ok else (2 if opened else 3))"
set "DIAGRC=%ERRORLEVEL%"
if "%DIAGRC%"=="0" echo [PASS] TRUE QMT Level-2 transaction/order data reached Python.
if "%DIAGRC%"=="2" (
  echo [WARN] Market is open but no transaction/order Level-2 reached Python.
  echo The printed subscription/error fields now point to broker permission or client capability.
)
if "%DIAGRC%"=="3" echo [INFO] Market is closed; live L2 diagnostic is inconclusive. Runtime will still be installed.

echo [5/7] Rebuilding hidden watchdogs...
(
  echo @echo off
  echo set "PYTHONPATH=%INSTALLDIR%;%%PYTHONPATH%%"
  echo cd /d "%INSTALLDIR%"
  echo :loop
  echo "%PYEXE%" -u "%SERVICEDIR%\qmt_l2_training_recorder_v6.py" ^>^> "%LOGFILE%" 2^>^&1
  echo echo [%%DATE%% %%TIME%%] L2 recorder exited %%ERRORLEVEL%% - restart in 5s ^>^> "%LOGFILE%"
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
  echo "%PYEXE%" -u "%SERVICEDIR%\l2_ml_autotrain_daemon_v3.py" ^>^> "%AUTOLOG%" 2^>^&1
  echo echo [%%DATE%% %%TIME%%] ML daemon exited %%ERRORLEVEL%% - restart in 10s ^>^> "%AUTOLOG%"
  echo timeout /t 10 /nobreak ^>nul
  echo goto loop
) > "%AUTOWATCHDOG%"
(
  echo Set sh = CreateObject("WScript.Shell"^)
  echo sh.Run "cmd.exe /d /c ""%AUTOWATCHDOG%""", 0, False
) > "%AUTOVBS%"

echo [6/7] Starting background recorder + ML daemon...
wscript.exe "%STARTVBS%"
wscript.exe "%AUTOVBS%"
timeout /t 7 /nobreak >nul

echo [7/7] Verifying installed runtime...
findstr /C:"l2-training-recorder-v6-morning-auction-20260903c" "%LOGFILE%" >nul 2>&1
if errorlevel 1 (echo [WARN] Recorder installed; marker not visible yet.) else (echo [PASS] Morning-priority L2 recorder active.)
findstr /C:"l2-ml-autotrain-v3-morning-gated-20260903" "%AUTOLOG%" >nul 2>&1
if errorlevel 1 (echo [WARN] ML daemon installed; marker not visible yet.) else (echo [PASS] Morning-gated ML daemon active.)

echo.
echo [OK] Atomic L2/ML runtime configured.
echo - New-vs-old cache dedupe controls freshness; old cache tails cannot fake fresh L2.
echo - Recorder heartbeat is asynchronous; cloud delays cannot block local capture.
echo - Full dependency stack is updated together to avoid wrapper/base version mismatch.
echo - 09:15-09:25 auction remains separate; 09:30-10:30 remains mandatory OOS gate.
echo - Nothing is auto-deployed to live trading.
pause
exit /b 0

:syntax
echo [ERROR] Syntax check failed. Existing runtime was NOT stopped or replaced.
pause
exit /b 1
:fail_download
echo [ERROR] Download/marker validation failed. Existing runtime was NOT stopped.
pause
exit /b 1
:fail_install
echo [ERROR] Installation failed after stopping the old runtime.
echo Re-run this BAT to restore the verified stack.
pause
exit /b 1
