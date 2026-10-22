@echo off
setlocal EnableExtensions
chcp 65001 >nul

echo ======================================================
echo A-Stock V7 Forward + V6 Multi-Challenger Runtime
echo Runtime marker: l1-v7-v6-stack-20260905
echo ======================================================
set "INSTALLDIR=%LOCALAPPDATA%\AStockQMT"
set "SERVICEDIR=%INSTALLDIR%\services"
set "MODULEDIR=%INSTALLDIR%\modules"
set "RUNTIME=%INSTALLDIR%\runtime"
set "STARTUPDIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "BASE=https://raw.githubusercontent.com/richard663-ui/-a-stock-app/main"
set "RESEARCHWATCH=%RUNTIME%\AStockResearchWatchdog.cmd"
set "RESEARCHLOG=%RUNTIME%\research_recorder.log"
set "RESEARCHVBS=%STARTUPDIR%\AStockQMTResearchRecorder.vbs"
set "AUTOWATCH=%RUNTIME%\AStockL2MLAutoTrainWatchdog.cmd"
set "AUTOLOG=%RUNTIME%\l2_ml_autotrain_daemon.log"
set "AUTOVBS=%STARTUPDIR%\AStockL2MLAutoTrain.vbs"
set "KILLVBS=%TEMP%\astock_morning_hotfix_stop.vbs"
set "V6DAEMON=l1-ml-autotrain-v6-multi-challenger-20260905"
set "V6TRAINER=l1-60s-trainer-v6-exec-aligned-stock-intercept-robust-20260905"

if not exist "%INSTALLDIR%" (echo [ERROR] AStock runtime not found.& if not defined ASTOCK_NO_PAUSE pause& exit /b 1)
set "PYEXE="
for /f "delims=" %%I in ('py -c "import sys;print(sys.executable)" 2^>nul') do set "PYEXE=%%I"
if not defined PYEXE for /f "delims=" %%I in ('python -c "import sys;print(sys.executable)" 2^>nul') do set "PYEXE=%%I"
if not defined PYEXE (echo [ERROR] Python not found.& if not defined ASTOCK_NO_PAUSE pause& exit /b 1)
if not exist "%SERVICEDIR%" mkdir "%SERVICEDIR%" >nul 2>&1
if not exist "%MODULEDIR%" mkdir "%MODULEDIR%" >nul 2>&1
if not exist "%RUNTIME%" mkdir "%RUNTIME%" >nul 2>&1

echo [1/6] Downloading complete V7 + V6 multi-challenger stack before touching runtime...
for %%F in (qmt_research_recorder_v6.py qmt_research_recorder_v7.py train_l1_60s_model_v1.py train_l1_60s_model_v2.py train_l1_60s_model_v4.py train_l1_60s_model_v4r.py train_l1_60s_model_v5_challenger.py train_l1_60s_model_v5r.py train_l1_60s_model_v6_exec_aligned.py l1_ml_autotrain_daemon_v1.py l1_ml_autotrain_daemon_v2.py) do (
  curl.exe -L --fail --retry 3 -o "%TEMP%\%%F" "%BASE%/services/%%F" || goto :fail_download
)
curl.exe -L --fail --retry 3 -o "%TEMP%\macd_calibration_v5.py" "%BASE%/modules/macd_calibration_v5.py" || goto :fail_download
findstr /C:"research-recorder-v7-nonblocking-macd-20260904" "%TEMP%\qmt_research_recorder_v7.py" >nul || goto :fail_download
findstr /C:"l1-60s-trainer-v2-dedup-columns-20260904" "%TEMP%\train_l1_60s_model_v2.py" >nul || goto :fail_download
findstr /C:"l1-60s-trainer-v4-asymmetric-regime-20260904" "%TEMP%\train_l1_60s_model_v4.py" >nul || goto :fail_download
findstr /C:"l1-60s-trainer-v4r-asymmetric-rotating-thin-20260904" "%TEMP%\train_l1_60s_model_v4r.py" >nul || goto :fail_download
findstr /C:"l1-60s-trainer-v5-robust-challenger-20260904" "%TEMP%\train_l1_60s_model_v5_challenger.py" >nul || goto :fail_download
findstr /C:"l1-60s-trainer-v5r-robust-challenger-20260904" "%TEMP%\train_l1_60s_model_v5r.py" >nul || goto :fail_download
findstr /C:"%V6TRAINER%" "%TEMP%\train_l1_60s_model_v6_exec_aligned.py" >nul || goto :fail_download
findstr /C:"%V6DAEMON%" "%TEMP%\l1_ml_autotrain_daemon_v2.py" >nul || goto :fail_download

echo [2/6] Syntax checking complete V7 + V6 stack...
"%PYEXE%" -m py_compile "%TEMP%\qmt_research_recorder_v6.py" "%TEMP%\qmt_research_recorder_v7.py" "%TEMP%\train_l1_60s_model_v1.py" "%TEMP%\train_l1_60s_model_v2.py" "%TEMP%\train_l1_60s_model_v4.py" "%TEMP%\train_l1_60s_model_v4r.py" "%TEMP%\train_l1_60s_model_v5_challenger.py" "%TEMP%\train_l1_60s_model_v5r.py" "%TEMP%\train_l1_60s_model_v6_exec_aligned.py" "%TEMP%\l1_ml_autotrain_daemon_v1.py" "%TEMP%\l1_ml_autotrain_daemon_v2.py" "%TEMP%\macd_calibration_v5.py"
if errorlevel 1 goto :fail_syntax

echo [3/6] Stopping research recorder and L1 auto-trainer only...
(
 echo On Error Resume Next
 echo Set svc = GetObject("winmgmts:\\.\root\cimv2"^)
 echo For Each p In svc.ExecQuery("Select * from Win32_Process"^)
 echo   cmd = LCase("" ^& p.CommandLine^)
 echo   If InStr(cmd,"qmt_research_recorder"^) ^> 0 Or InStr(cmd,"astockresearchwatchdog.cmd"^) ^> 0 Or InStr(cmd,"l1_ml_autotrain_daemon"^) ^> 0 Or InStr(cmd,"astockl2mlautotrainwatchdog.cmd"^) ^> 0 Then p.Terminate
 echo Next
) > "%KILLVBS%"
cscript.exe //nologo "%KILLVBS%" >nul 2>&1
del /q "%KILLVBS%" >nul 2>&1
timeout /t 1 /nobreak >nul

echo [4/6] Installing matched V7 + V6 files...
for %%F in (qmt_research_recorder_v6.py qmt_research_recorder_v7.py train_l1_60s_model_v1.py train_l1_60s_model_v2.py train_l1_60s_model_v4.py train_l1_60s_model_v4r.py train_l1_60s_model_v5_challenger.py train_l1_60s_model_v5r.py train_l1_60s_model_v6_exec_aligned.py l1_ml_autotrain_daemon_v1.py l1_ml_autotrain_daemon_v2.py) do copy /Y "%TEMP%\%%F" "%SERVICEDIR%\%%F" >nul || goto :fail_install
copy /Y "%TEMP%\macd_calibration_v5.py" "%MODULEDIR%\macd_calibration_v5.py" >nul || goto :fail_install

set "PYTHONPATH=%INSTALLDIR%;%PYTHONPATH%"
echo [5/6] Rebuilding watchdogs...
(
 echo @echo off
 echo set "PYTHONPATH=%INSTALLDIR%;%%PYTHONPATH%%"
 echo cd /d "%INSTALLDIR%"
 echo :loop
 echo "%PYEXE%" -u "%SERVICEDIR%\qmt_research_recorder_v7.py" ^>^> "%RESEARCHLOG%" 2^>^&1
 echo set "RC=%%ERRORLEVEL%%"
 echo if "%%RC%%"=="17" exit /b 0
 echo timeout /t 5 /nobreak ^>nul
 echo goto loop
) > "%RESEARCHWATCH%"
(
 echo Set sh = CreateObject("WScript.Shell"^)
 echo sh.Run "cmd.exe /d /c ""%RESEARCHWATCH%""", 0, False
) > "%RESEARCHVBS%"
(
 echo @echo off
 echo set "PYTHONPATH=%INSTALLDIR%;%%PYTHONPATH%%"
 echo cd /d "%INSTALLDIR%"
 echo :loop
 echo "%PYEXE%" -u "%SERVICEDIR%\l1_ml_autotrain_daemon_v2.py" ^>^> "%AUTOLOG%" 2^>^&1
 echo timeout /t 10 /nobreak ^>nul
 echo goto loop
) > "%AUTOWATCH%"
(
 echo Set sh = CreateObject("WScript.Shell"^)
 echo sh.Run "cmd.exe /d /c ""%AUTOWATCH%""", 0, False
) > "%AUTOVBS%"

wscript.exe "%RESEARCHVBS%"
wscript.exe "%AUTOVBS%"
timeout /t 8 /nobreak >nul

echo [6/6] Verifying V7 + V6 runtime...
findstr /C:"research-recorder-v7-nonblocking-macd-20260904" "%RESEARCHLOG%" >nul 2>&1
if errorlevel 1 (echo [WARN] V7 research marker not visible yet.) else (echo [PASS] V7 non-blocking forward recorder active.)
findstr /C:"%V6DAEMON%" "%AUTOLOG%" >nul 2>&1
if errorlevel 1 (echo [WARN] V6 multi-challenger marker not visible yet.) else (echo [PASS] L1 ML V6 multi-challenger daemon active.)
echo.
echo [OK] V7 + V6 multi-challenger runtime installed.
echo - V4R remains Champion and is trained/synced normally.
echo - V5R remains conservative control; V6 is the execution-aligned Challenger.
echo - V6 uses ask/bid-aligned proxy targets, fixed stock intercepts, and RobustScaler.
echo - V5 stability gates are unchanged; no historical threshold relaxation was made.
echo - Challenger failures cannot replace or break the Champion.
echo - MACD network fetches do not block 60s/120s expiry timing.
echo - Existing samples are preserved.
echo - QMT cloud bridge and L1 recorder were not stopped.
echo - Nothing is auto-promoted or auto-deployed.
if not defined ASTOCK_NO_PAUSE pause
exit /b 0

:fail_download
echo [ERROR] Download/marker check failed. Runtime untouched.
if not defined ASTOCK_NO_PAUSE pause
exit /b 1
:fail_syntax
echo [ERROR] Syntax check failed. Runtime untouched.
if not defined ASTOCK_NO_PAUSE pause
exit /b 1
:fail_install
echo [ERROR] Install failed after stop. Re-run this BAT.
if not defined ASTOCK_NO_PAUSE pause
exit /b 1
