@echo off
setlocal EnableExtensions
chcp 65001 >nul

echo ======================================================
echo A-Stock L1/Tick 60s ML Baseline - One Click Update
echo No Level-2 permission required
echo ======================================================

set "INSTALLDIR=%LOCALAPPDATA%\AStockQMT"
set "SERVICEDIR=%INSTALLDIR%\services"
set "RUNTIME=%INSTALLDIR%\runtime"
set "STARTUPDIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "BASE=https://raw.githubusercontent.com/richard663-ui/-a-stock-app/main"
set "WATCHDOG=%RUNTIME%\AStockL2TrainingWatchdog.cmd"
set "AUTOWATCHDOG=%RUNTIME%\AStockL2MLAutoTrainWatchdog.cmd"
set "LOGFILE=%RUNTIME%\l2_training_recorder.log"
set "AUTOLOG=%RUNTIME%\l2_ml_autotrain_daemon.log"
set "STARTVBS=%STARTUPDIR%\AStockL2TrainingRecorder.vbs"
set "AUTOVBS=%STARTUPDIR%\AStockL2MLAutoTrain.vbs"
set "KILLVBS=%TEMP%\astock_l1_stop.vbs"

if not exist "%INSTALLDIR%" (
  echo [ERROR] AStock QMT runtime not found: %INSTALLDIR%
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

"%PYEXE%" -c "from xtquant import xtdata; import sklearn,joblib,pandas,numpy" >nul 2>&1
if errorlevel 1 (
  echo [INFO] Installing research ML dependencies...
  "%PYEXE%" -m pip install --disable-pip-version-check "scikit-learn>=1.5,<2.0" "joblib>=1.4"
  if errorlevel 1 goto :fail
)

if not exist "%SERVICEDIR%" mkdir "%SERVICEDIR%" >nul 2>&1
if not exist "%RUNTIME%" mkdir "%RUNTIME%" >nul 2>&1

echo [1/6] Downloading L1 baseline files...
for %%F in (qmt_l1_training_recorder_v1.py train_l1_60s_model_v1.py l1_ml_autotrain_daemon_v1.py) do (
  curl.exe -L --fail --retry 3 -o "%TEMP%\%%F" "%BASE%/services/%%F" || goto :fail_download
)
findstr /C:"l1-training-recorder-v1-20260904" "%TEMP%\qmt_l1_training_recorder_v1.py" >nul || goto :fail_download
findstr /C:"l1-60s-trainer-v1-purged-morning-gated-20260904" "%TEMP%\train_l1_60s_model_v1.py" >nul || goto :fail_download
findstr /C:"l1-ml-autotrain-v1-20260904" "%TEMP%\l1_ml_autotrain_daemon_v1.py" >nul || goto :fail_download

REM The new files reuse the already proven V3/V4/V5 trainer and V2-V6 recorder stack.
REM Refresh those dependencies too so a stale local wrapper cannot mismatch main.
for %%F in (qmt_l2_training_recorder_v2.py qmt_l2_training_recorder_v3.py qmt_l2_training_recorder_v4.py qmt_l2_training_recorder_v5.py qmt_l2_training_recorder_v6.py train_l2_60s_model_v3.py train_l2_60s_model_v4.py train_l2_60s_model_v5.py) do (
  curl.exe -L --fail --retry 3 -o "%TEMP%\%%F" "%BASE%/services/%%F" || goto :fail_download
)

echo [2/6] Syntax checking the complete training stack...
"%PYEXE%" -m py_compile "%TEMP%\qmt_l1_training_recorder_v1.py" "%TEMP%\train_l1_60s_model_v1.py" "%TEMP%\l1_ml_autotrain_daemon_v1.py" "%TEMP%\qmt_l2_training_recorder_v2.py" "%TEMP%\qmt_l2_training_recorder_v3.py" "%TEMP%\qmt_l2_training_recorder_v4.py" "%TEMP%\qmt_l2_training_recorder_v5.py" "%TEMP%\qmt_l2_training_recorder_v6.py" "%TEMP%\train_l2_60s_model_v3.py" "%TEMP%\train_l2_60s_model_v4.py" "%TEMP%\train_l2_60s_model_v5.py"
if errorlevel 1 goto :fail_syntax

REM Stop only the training recorder/ML daemon AFTER downloads and syntax pass.
REM QMT cloud bridge and forward recorder remain untouched.
(
  echo On Error Resume Next
  echo Set svc = GetObject("winmgmts:\\.\root\cimv2"^)
  echo For Each p In svc.ExecQuery("Select * from Win32_Process"^)
  echo   cmd = LCase("" ^& p.CommandLine^)
  echo   If InStr(cmd, "qmt_l2_training_recorder"^) ^> 0 Or InStr(cmd, "qmt_l1_training_recorder"^) ^> 0 Or InStr(cmd, "astockl2trainingwatchdog.cmd"^) ^> 0 Or InStr(cmd, "l2_ml_autotrain_daemon"^) ^> 0 Or InStr(cmd, "l1_ml_autotrain_daemon"^) ^> 0 Or InStr(cmd, "astockl2mlautotrainwatchdog.cmd"^) ^> 0 Then p.Terminate
  echo Next
) > "%KILLVBS%"
cscript.exe //nologo "%KILLVBS%" >nul 2>&1
del /q "%KILLVBS%" >nul 2>&1
timeout /t 1 /nobreak >nul

echo [3/6] Installing matched files...
for %%F in (qmt_l1_training_recorder_v1.py train_l1_60s_model_v1.py l1_ml_autotrain_daemon_v1.py qmt_l2_training_recorder_v2.py qmt_l2_training_recorder_v3.py qmt_l2_training_recorder_v4.py qmt_l2_training_recorder_v5.py qmt_l2_training_recorder_v6.py train_l2_60s_model_v3.py train_l2_60s_model_v4.py train_l2_60s_model_v5.py) do (
  copy /Y "%TEMP%\%%F" "%SERVICEDIR%\%%F" >nul || goto :fail_install
)

set "PYTHONPATH=%INSTALLDIR%;%PYTHONPATH%"

echo [4/6] Rebuilding hidden L1 recorder + trainer watchdogs...
(
  echo @echo off
  echo set "PYTHONPATH=%INSTALLDIR%;%%PYTHONPATH%%"
  echo cd /d "%INSTALLDIR%"
  echo :loop
  echo "%PYEXE%" -u "%SERVICEDIR%\qmt_l1_training_recorder_v1.py" ^>^> "%LOGFILE%" 2^>^&1
  echo echo [%%DATE%% %%TIME%%] L1 recorder exited %%ERRORLEVEL%% - restart in 5s ^>^> "%LOGFILE%"
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
  echo "%PYEXE%" -u "%SERVICEDIR%\l1_ml_autotrain_daemon_v1.py" ^>^> "%AUTOLOG%" 2^>^&1
  echo echo [%%DATE%% %%TIME%%] L1 ML daemon exited %%ERRORLEVEL%% - restart in 10s ^>^> "%AUTOLOG%"
  echo timeout /t 10 /nobreak ^>nul
  echo goto loop
) > "%AUTOWATCHDOG%"
(
  echo Set sh = CreateObject("WScript.Shell"^)
  echo sh.Run "cmd.exe /d /c ""%AUTOWATCHDOG%""", 0, False
) > "%AUTOVBS%"

echo [5/6] Starting L1 background collection + auto-training...
wscript.exe "%STARTVBS%"
wscript.exe "%AUTOVBS%"
timeout /t 6 /nobreak >nul

echo [6/6] Verifying markers...
findstr /C:"l1-training-recorder-v1-20260904" "%LOGFILE%" >nul 2>&1
if errorlevel 1 (echo [WARN] L1 recorder installed; marker not visible yet.) else (echo [PASS] L1/Tick 60s recorder active.)
findstr /C:"l1-ml-autotrain-v1-20260904" "%AUTOLOG%" >nul 2>&1
if errorlevel 1 (echo [WARN] L1 auto-trainer installed; marker not visible yet.) else (echo [PASS] L1 ML auto-trainer active.)

echo.
echo ======================================================
echo [OK] L1_BASELINE pipeline configured.
echo ======================================================
echo - NO Level-2 permission is required.
echo - Existing samples from today/yesterday are preserved and reused.
echo - 5s L1/Tick states + +60s smoothed-mid labels continue automatically.
echo - Logistic Regression + HistGradientBoosting train at 11:35 and 15:10.
echo - Validation selects thresholds; test is never used to tune them.
echo - 2bp execution hurdle and 09:30-10:30 OOS gate remain active.
echo - 70%% is tracked only as a multi-day non-overlap OOS milestone.
echo - Nothing is auto-deployed to live trading.
echo - QMT must remain open and logged in during market hours.
echo.
pause
exit /b 0

:fail_download
echo [ERROR] Download/marker validation failed. Existing runtime was NOT stopped.
pause
exit /b 1
:fail_syntax
echo [ERROR] Syntax check failed. Existing runtime was NOT stopped.
pause
exit /b 1
:fail_install
echo [ERROR] Installation failed after old training runtime stopped. Re-run this BAT.
pause
exit /b 1
:fail
echo [ERROR] L1 baseline setup failed before runtime replacement.
pause
exit /b 1
