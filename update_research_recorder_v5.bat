@echo off
setlocal EnableExtensions
chcp 65001 >nul

echo ========================================
echo A-Stock 60s Adaptive Model Updater
 echo V5A: symbol normalization + selective WATCH
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
set "TMPREC=%TEMP%\astock_rec_v5a.py"
set "TMPMOD=%TEMP%\astock_model_v5a.py"
set "TMPMACD=%TEMP%\astock_macd_v5a.py"
set "TMPDIR=%TEMP%\astock_direction_v18_adaptive.py"
set "KILLVBS=%TEMP%\astock_stop_adaptive60.vbs"
set "BASE=https://raw.githubusercontent.com/richard663-ui/-a-stock-app/main"

if not exist "%INSTALLDIR%" (
  echo [ERROR] AStock stable runtime not found: %INSTALLDIR%
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

echo [1/7] Downloading adaptive 60s files...
del /q "%TMPREC%" "%TMPMOD%" "%TMPMACD%" "%TMPDIR%" >nul 2>&1
curl.exe -L --fail --retry 3 --connect-timeout 8 -o "%TMPREC%" "%BASE%/services/qmt_research_recorder_v5.py"
if errorlevel 1 goto :download_fail
curl.exe -L --fail --retry 3 --connect-timeout 8 -o "%TMPMOD%" "%BASE%/modules/research_forward_model_v5.py"
if errorlevel 1 goto :download_fail
curl.exe -L --fail --retry 3 --connect-timeout 8 -o "%TMPMACD%" "%BASE%/modules/macd_calibration_v5.py"
if errorlevel 1 goto :download_fail
curl.exe -L --fail --retry 3 --connect-timeout 8 -o "%TMPDIR%" "%BASE%/modules/direction_v18.py"
if errorlevel 1 goto :download_fail

findstr /C:"research-recorder-v5a-20260901" "%TMPREC%" >nul 2>&1
if errorlevel 1 goto :marker_fail
findstr /C:"research-shadow-v5a-adaptive-normalized-selective-60s" "%TMPMOD%" >nul 2>&1
if errorlevel 1 goto :marker_fail
findstr /C:"macd-structure-v9" "%TMPMACD%" >nul 2>&1
if errorlevel 1 goto :marker_fail
findstr /C:"selective_gate_60" "%TMPDIR%" >nul 2>&1
if errorlevel 1 goto :marker_fail

echo [2/7] Syntax checking before replacement...
"%PYEXE%" -m py_compile "%TMPREC%" "%TMPMOD%" "%TMPMACD%" "%TMPDIR%"
if errorlevel 1 (
  echo [ERROR] Syntax check failed. Existing runtime was not changed.
  pause
  exit /b 1
)

echo [3/7] Stopping research recorder and live bridge...
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

echo [4/7] Installing adaptive model files...
copy /Y "%TMPREC%" "%REC5%" >nul
if errorlevel 1 goto :install_fail
copy /Y "%TMPMOD%" "%MOD5%" >nul
if errorlevel 1 goto :install_fail
copy /Y "%TMPMACD%" "%MACD5%" >nul
if errorlevel 1 goto :install_fail
copy /Y "%TMPDIR%" "%DIR18%" >nul
if errorlevel 1 goto :install_fail
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

echo [7/7] Checking V5A marker...
findstr /C:"research-recorder-v5a-20260901" "%LOGFILE%" >nul 2>&1
if errorlevel 1 (
  echo [WARN] V5A files are installed, but the recorder marker is not visible yet.
  echo Keep QMT logged in. The watchdog will continue retrying.
) else (
  echo [PASS] V5A adaptive research recorder is running.
)

echo.
echo [OK] Adaptive 60s update installed. No reboot required.
echo - 60-second direction remains the primary target.
echo - No per-stock manual parameter table was added.
echo - Recent 30s/60s movement is normalized automatically per symbol.
echo - Weak or conflicted candidates are changed to WATCH instead of forced UP/DOWN.
echo - 301236.SZ can remain the priority validation stock while other stocks use the same framework.
echo - MACD remains confidence context only and does not directly decide direction.
echo - Existing raw QMT data and historical validation samples are preserved.
echo.
pause
exit /b 0

:download_fail
echo [ERROR] Download failed. Existing runtime was not changed.
pause
exit /b 1

:marker_fail
echo [ERROR] Downloaded files failed adaptive-version marker check.
pause
exit /b 1

:install_fail
echo [ERROR] Installation failed.
pause
exit /b 1
