@echo off
setlocal EnableExtensions
chcp 65001 >nul

echo ======================================================
echo A-Stock Morning Hotfix 2026-09-04
echo Fix L1 trainer RC=1 + forward late_scoring

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

if not exist "%INSTALLDIR%" (echo [ERROR] AStock runtime not found.& pause& exit /b 1)
set "PYEXE="
for /f "delims=" %%I in ('py -c "import sys;print(sys.executable)" 2^>nul') do set "PYEXE=%%I"
if not defined PYEXE for /f "delims=" %%I in ('python -c "import sys;print(sys.executable)" 2^>nul') do set "PYEXE=%%I"
if not defined PYEXE (echo [ERROR] Python not found.& pause& exit /b 1)
if not exist "%SERVICEDIR%" mkdir "%SERVICEDIR%" >nul 2>&1
if not exist "%MODULEDIR%" mkdir "%MODULEDIR%" >nul 2>&1
if not exist "%RUNTIME%" mkdir "%RUNTIME%" >nul 2>&1

echo [1/6] Downloading fixed files before touching runtime...
for %%F in (qmt_research_recorder_v6.py qmt_research_recorder_v7.py train_l1_60s_model_v1.py train_l1_60s_model_v2.py l1_ml_autotrain_daemon_v1.py l1_ml_autotrain_daemon_v2.py) do (
  curl.exe -L --fail --retry 3 -o "%TEMP%\%%F" "%BASE%/services/%%F" || goto :fail_download
)
curl.exe -L --fail --retry 3 -o "%TEMP%\macd_calibration_v5.py" "%BASE%/modules/macd_calibration_v5.py" || goto :fail_download
findstr /C:"research-recorder-v7-nonblocking-macd-20260904" "%TEMP%\qmt_research_recorder_v7.py" >nul || goto :fail_download
findstr /C:"l1-60s-trainer-v2-dedup-columns-20260904" "%TEMP%\train_l1_60s_model_v2.py" >nul || goto :fail_download
findstr /C:"l1-ml-autotrain-v2-dedup-20260904" "%TEMP%\l1_ml_autotrain_daemon_v2.py" >nul || goto :fail_download

echo [2/6] Syntax checking...
"%PYEXE%" -m py_compile "%TEMP%\qmt_research_recorder_v6.py" "%TEMP%\qmt_research_recorder_v7.py" "%TEMP%\train_l1_60s_model_v1.py" "%TEMP%\train_l1_60s_model_v2.py" "%TEMP%\l1_ml_autotrain_daemon_v1.py" "%TEMP%\l1_ml_autotrain_daemon_v2.py" "%TEMP%\macd_calibration_v5.py"
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

echo [4/6] Installing matched hotfix files...
for %%F in (qmt_research_recorder_v6.py qmt_research_recorder_v7.py train_l1_60s_model_v1.py train_l1_60s_model_v2.py l1_ml_autotrain_daemon_v1.py l1_ml_autotrain_daemon_v2.py) do copy /Y "%TEMP%\%%F" "%SERVICEDIR%\%%F" >nul || goto :fail_install
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
timeout /t 6 /nobreak >nul

echo [6/6] Verifying...
findstr /C:"research-recorder-v7-nonblocking-macd-20260904" "%RESEARCHLOG%" >nul 2>&1
if errorlevel 1 (echo [WARN] V7 research marker not visible yet.) else (echo [PASS] V7 non-blocking forward recorder active.)
findstr /C:"l1-ml-autotrain-v2-dedup-20260904" "%AUTOLOG%" >nul 2>&1
if errorlevel 1 (echo [WARN] L1 trainer V2 marker not visible yet.) else (echo [PASS] L1 ML trainer V2 active.)
echo.
echo [OK] Hotfix installed.
echo - MACD network fetches no longer block 60s/120s expiry timing.
echo - V5B predictive weights/gates are unchanged.
echo - L1 duplicate feature columns no longer crash training.
echo - Existing samples are preserved.
echo - If run before 12:30, daemon-version change forces a fresh lunch training attempt.
echo - QMT cloud bridge and L1 recorder were not stopped.
pause
exit /b 0

:fail_download
echo [ERROR] Download/marker check failed. Runtime untouched.& pause& exit /b 1
:fail_syntax
echo [ERROR] Syntax check failed. Runtime untouched.& pause& exit /b 1
:fail_install
echo [ERROR] Install failed after stop. Re-run this BAT.& pause& exit /b 1
