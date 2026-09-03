@echo off
setlocal EnableExtensions
chcp 65001 >nul

echo ========================================
echo A-Stock 60s Safety + Integrity Updater
echo V5B model + V6 expiry-first recorder
echo ========================================

set "INSTALLDIR=%LOCALAPPDATA%\AStockQMT"
set "SERVICEDIR=%INSTALLDIR%\services"
set "MODULEDIR=%INSTALLDIR%\modules"
set "RUNTIME=%INSTALLDIR%\runtime"
set "STARTUPDIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "RESEARCHVBS=%STARTUPDIR%\AStockQMTResearchRecorder.vbs"
set "WATCHDOG=%RUNTIME%\AStockResearchWatchdog.cmd"
set "LOGFILE=%RUNTIME%\research_recorder.log"
set "REC5=%SERVICEDIR%\qmt_research_recorder_v5.py"
set "REC6=%SERVICEDIR%\qmt_research_recorder_v6.py"
set "MOD5=%MODULEDIR%\research_forward_model_v5.py"
set "MACD5=%MODULEDIR%\macd_calibration_v5.py"
set "DIR18=%MODULEDIR%\direction_v18.py"
set "TMPREC5=%TEMP%\astock_rec_v5b.py"
set "TMPREC6=%TEMP%\astock_rec_v6.py"
set "TMPMOD=%TEMP%\astock_model_v5b.py"
set "TMPMACD=%TEMP%\astock_macd_v5b.py"
set "TMPDIR=%TEMP%\astock_direction_v10_safety.py"
set "KILLVBS=%TEMP%\astock_stop_v6.vbs"
set "BASE=https://raw.githubusercontent.com/richard663-ui/-a-stock-app/main"

if not exist "%INSTALLDIR%" (
  echo [ERROR] AStock runtime not found: %INSTALLDIR%
  pause
  exit /b 1
)

set "PYEXE="
for /f "delims=" %%I in ('py -c "import sys; print(sys.executable)" 2^>nul') do set "PYEXE=%%I"
if not defined PYEXE for /f "delims=" %%I in ('python -c "import sys; print(sys.executable)" 2^>nul') do set "PYEXE=%%I"
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PYEXE (
  echo [ERROR] Python not found.
  pause
  exit /b 1
)

if not exist "%SERVICEDIR%" mkdir "%SERVICEDIR%" >nul 2>&1
if not exist "%MODULEDIR%" mkdir "%MODULEDIR%" >nul 2>&1
if not exist "%RUNTIME%" mkdir "%RUNTIME%" >nul 2>&1

echo [1/7] Downloading V5B + V6 files...
del /q "%TMPREC5%" "%TMPREC6%" "%TMPMOD%" "%TMPMACD%" "%TMPDIR%" >nul 2>&1
curl.exe -L --fail --retry 3 --connect-timeout 8 -o "%TMPREC5%" "%BASE%/services/qmt_research_recorder_v5.py" || goto :download_fail
curl.exe -L --fail --retry 3 --connect-timeout 8 -o "%TMPREC6%" "%BASE%/services/qmt_research_recorder_v6.py" || goto :download_fail
curl.exe -L --fail --retry 3 --connect-timeout 8 -o "%TMPMOD%" "%BASE%/modules/research_forward_model_v5.py" || goto :download_fail
curl.exe -L --fail --retry 3 --connect-timeout 8 -o "%TMPMACD%" "%BASE%/modules/macd_calibration_v5.py" || goto :download_fail
curl.exe -L --fail --retry 3 --connect-timeout 8 -o "%TMPDIR%" "%BASE%/modules/direction_v18.py" || goto :download_fail

findstr /C:"research-recorder-v5b-20260902" "%TMPREC5%" >nul 2>&1 || goto :marker_fail
findstr /C:"research-recorder-v6-expiry-first-20260902" "%TMPREC6%" >nul 2>&1 || goto :marker_fail
findstr /C:"research-shadow-v5b-regime-safety-60s" "%TMPMOD%" >nul 2>&1 || goto :marker_fail
findstr /C:"SAFETY_MIN_ABNORMALITY" "%TMPDIR%" >nul 2>&1 || goto :marker_fail

echo [2/7] Syntax checking before replacement...
"%PYEXE%" -m py_compile "%TMPREC5%" "%TMPREC6%" "%TMPMOD%" "%TMPMACD%" "%TMPDIR%"
if errorlevel 1 goto :syntax_fail

echo [3/7] Stopping OLD RESEARCH recorder only...
REM The cloud bridge is a separate live-data service and is intentionally left
REM untouched. Older versions killed qmt_cloud_bridge.py here and then looked for
REM a .vbs launcher even though the stable installer creates a .cmd launcher.
(
  echo On Error Resume Next
  echo Set svc = GetObject("winmgmts:\\.\root\cimv2"^)
  echo For Each p In svc.ExecQuery("Select * from Win32_Process"^)
  echo   cmd = LCase("" ^& p.CommandLine^)
  echo   If InStr(cmd, "qmt_research_recorder_v3.py"^) ^> 0 Or InStr(cmd, "qmt_research_recorder_v4.py"^) ^> 0 Or InStr(cmd, "qmt_research_recorder_v4b.py"^) ^> 0 Or InStr(cmd, "qmt_research_recorder_v5.py"^) ^> 0 Or InStr(cmd, "qmt_research_recorder_v6.py"^) ^> 0 Or InStr(cmd, "astockresearchwatchdog.cmd"^) ^> 0 Then
  echo     p.Terminate
  echo   End If
  echo Next
) > "%KILLVBS%"
cscript.exe //nologo "%KILLVBS%" >nul 2>&1
del /q "%KILLVBS%" >nul 2>&1
timeout /t 2 /nobreak >nul

echo [4/7] Installing files...
copy /Y "%TMPREC5%" "%REC5%" >nul || goto :install_fail
copy /Y "%TMPREC6%" "%REC6%" >nul || goto :install_fail
copy /Y "%TMPMOD%" "%MOD5%" >nul || goto :install_fail
copy /Y "%TMPMACD%" "%MACD5%" >nul || goto :install_fail
copy /Y "%TMPDIR%" "%DIR18%" >nul || goto :install_fail
del /q "%TMPREC5%" "%TMPREC6%" "%TMPMOD%" "%TMPMACD%" "%TMPDIR%" >nul 2>&1

echo [5/7] Rebuilding expiry-first research watchdog...
(
  echo @echo off
  echo set "PYTHONPATH=%INSTALLDIR%;%%PYTHONPATH%%"
  echo set "NO_PROXY=.supabase.co,supabase.co"
  echo set "no_proxy=.supabase.co,supabase.co"
  echo cd /d "%INSTALLDIR%"
  echo :research_loop
  echo "%PYEXE%" -u "%REC6%" ^>^> "%LOGFILE%" 2^>^&1
  echo set "RC=%%ERRORLEVEL%%"
  echo if "%%RC%%"=="17" exit /b 0
  echo echo [%%DATE%% %%TIME%%] recorder exited code %%RC%% - restarting in 5 seconds ^>^> "%LOGFILE%"
  echo timeout /t 5 /nobreak ^>nul
  echo goto research_loop
) > "%WATCHDOG%"
(
  echo Set sh = CreateObject("WScript.Shell"^)
  echo sh.Run "cmd.exe /d /c ""%WATCHDOG%""", 0, False
) > "%RESEARCHVBS%"

echo [6/7] Starting research recorder; live bridge remains untouched...
wscript.exe "%RESEARCHVBS%"
timeout /t 7 /nobreak >nul

echo [7/7] Checking V6 marker...
findstr /C:"research-recorder-v6-expiry-first-20260902" "%LOGFILE%" >nul 2>&1
if errorlevel 1 (
  echo [WARN] Files installed; V6 recorder marker not visible yet.
  echo Keep QMT logged in. Watchdog will keep retrying.
) else (
  echo [PASS] V6 expiry-first research recorder is running.
)

echo.
echo [OK] V5B model + V6 recorder integrity installed. No reboot required.
echo - Predictive weights and V5B safety gate were NOT changed.
echo - Expiring 60s/120s samples are evaluated before heavy scoring.
echo - Eight-symbol scoring is staggered and runs in background workers.
echo - Slow score calculations are skipped rather than creating stale predictions.
echo - Cloud bridge is NOT stopped or restarted by this updater.
echo - Historical data is preserved.
echo.
pause
exit /b 0

:download_fail
echo [ERROR] Download failed. Existing runtime was not changed.
pause
exit /b 1
:marker_fail
echo [ERROR] Downloaded files failed marker check.
pause
exit /b 1
:syntax_fail
echo [ERROR] Syntax check failed. Existing runtime was not changed.
pause
exit /b 1
:install_fail
echo [ERROR] Installation failed.
pause
exit /b 1
