@echo off
setlocal EnableExtensions
chcp 65001 >nul

echo ========================================
echo A-Stock 60s Safety Gate Updater
echo V5B: strict L2 + regime protection
echo ========================================

set "INSTALLDIR=%LOCALAPPDATA%\AStockQMT"
set "SERVICEDIR=%INSTALLDIR%\services"
set "MODULEDIR=%INSTALLDIR%\modules"
set "RUNTIME=%INSTALLDIR%\runtime"
set "STARTUPDIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "RESEARCHVBS=%STARTUPDIR%\AStockQMTResearchRecorder.vbs"
set "BRIDGEVBS=%STARTUPDIR%\AStockQMTCloudBridge.vbs"
set "WATCHDOG=%RUNTIME%\AStockResearchWatchdog.cmd"
set "LOGFILE=%RUNTIME%\research_recorder.log"
set "REC5=%SERVICEDIR%\qmt_research_recorder_v5.py"
set "MOD5=%MODULEDIR%\research_forward_model_v5.py"
set "MACD5=%MODULEDIR%\macd_calibration_v5.py"
set "DIR18=%MODULEDIR%\direction_v18.py"
set "TMPREC=%TEMP%\astock_rec_v5b.py"
set "TMPMOD=%TEMP%\astock_model_v5b.py"
set "TMPMACD=%TEMP%\astock_macd_v5b.py"
set "TMPDIR=%TEMP%\astock_direction_v10_safety.py"
set "KILLVBS=%TEMP%\astock_stop_v5b.vbs"
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

echo [1/7] Downloading V5B safety files...
del /q "%TMPREC%" "%TMPMOD%" "%TMPMACD%" "%TMPDIR%" >nul 2>&1
curl.exe -L --fail --retry 3 --connect-timeout 8 -o "%TMPREC%" "%BASE%/services/qmt_research_recorder_v5.py" || goto :download_fail
curl.exe -L --fail --retry 3 --connect-timeout 8 -o "%TMPMOD%" "%BASE%/modules/research_forward_model_v5.py" || goto :download_fail
curl.exe -L --fail --retry 3 --connect-timeout 8 -o "%TMPMACD%" "%BASE%/modules/macd_calibration_v5.py" || goto :download_fail
curl.exe -L --fail --retry 3 --connect-timeout 8 -o "%TMPDIR%" "%BASE%/modules/direction_v18.py" || goto :download_fail

findstr /C:"research-recorder-v5b-20260902" "%TMPREC%" >nul 2>&1 || goto :marker_fail
findstr /C:"research-shadow-v5b-regime-safety-60s" "%TMPMOD%" >nul 2>&1 || goto :marker_fail
findstr /C:"SAFETY_MIN_ABNORMALITY" "%TMPDIR%" >nul 2>&1 || goto :marker_fail

echo [2/7] Syntax checking before replacement...
"%PYEXE%" -m py_compile "%TMPREC%" "%TMPMOD%" "%TMPMACD%" "%TMPDIR%"
if errorlevel 1 goto :syntax_fail

echo [3/7] Stopping old recorder and live bridge...
(
  echo On Error Resume Next
  echo Set svc = GetObject("winmgmts:\\.\root\cimv2"^)
  echo For Each p In svc.ExecQuery("Select * from Win32_Process"^)
  echo   cmd = LCase("" ^& p.CommandLine^)
  echo   If InStr(cmd, "qmt_research_recorder_v3.py"^) ^> 0 Or InStr(cmd, "qmt_research_recorder_v4.py"^) ^> 0 Or InStr(cmd, "qmt_research_recorder_v4b.py"^) ^> 0 Or InStr(cmd, "qmt_research_recorder_v5.py"^) ^> 0 Or InStr(cmd, "astockresearchwatchdog.cmd"^) ^> 0 Or InStr(cmd, "qmt_cloud_bridge.py"^) ^> 0 Or InStr(cmd, "astockqmtcloudbridgewatchdog.cmd"^) ^> 0 Then
  echo     p.Terminate
  echo   End If
  echo Next
) > "%KILLVBS%"
cscript.exe //nologo "%KILLVBS%" >nul 2>&1
del /q "%KILLVBS%" >nul 2>&1
timeout /t 2 /nobreak >nul

echo [4/7] Installing V5B files...
copy /Y "%TMPREC%" "%REC5%" >nul || goto :install_fail
copy /Y "%TMPMOD%" "%MOD5%" >nul || goto :install_fail
copy /Y "%TMPMACD%" "%MACD5%" >nul || goto :install_fail
copy /Y "%TMPDIR%" "%DIR18%" >nul || goto :install_fail
del /q "%TMPREC%" "%TMPMOD%" "%TMPMACD%" "%TMPDIR%" >nul 2>&1

echo [5/7] Rebuilding research watchdog...
(
  echo @echo off
  echo set "PYTHONPATH=%INSTALLDIR%;%%PYTHONPATH%%"
  echo set "NO_PROXY=.supabase.co,supabase.co"
  echo set "no_proxy=.supabase.co,supabase.co"
  echo cd /d "%INSTALLDIR%"
  echo :research_loop
  echo "%PYEXE%" -u "%REC5%" ^>^> "%LOGFILE%" 2^>^&1
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

echo [6/7] Restarting live bridge and research recorder...
if exist "%BRIDGEVBS%" wscript.exe "%BRIDGEVBS%"
wscript.exe "%RESEARCHVBS%"
timeout /t 7 /nobreak >nul

echo [7/7] Checking V5B marker...
findstr /C:"research-recorder-v5b-20260902" "%LOGFILE%" >nul 2>&1
if errorlevel 1 (
  echo [WARN] V5B files installed; recorder marker not visible yet.
  echo Keep QMT logged in. Watchdog will keep retrying.
) else (
  echo [PASS] V5B safety-gated research recorder is running.
)

echo.
echo [OK] V5B installed. No reboot required.
echo - No signal inversion.
echo - Research: requires 4/4 factor groups + confidence 65+ + abnormality 0.50-1.50.
echo - Mobile: only core-L2 high-confidence candidates with no opposed component can emit UP/DOWN.
echo - All rejected candidates remain visible internally and become WATCH externally.
echo - Historical V5A samples are preserved under their old model version.
echo.
pause
exit /b 0

:download_fail
echo [ERROR] Download failed. Existing runtime was not changed.
pause
exit /b 1
:marker_fail
echo [ERROR] Downloaded files failed V5B marker check.
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
