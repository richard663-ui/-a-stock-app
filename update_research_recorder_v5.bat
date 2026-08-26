@echo off
setlocal EnableExtensions
chcp 65001 >nul

echo ========================================
echo A-Stock Research Recorder V5 Updater
echo Direction + Confidence + MACD Calibration
echo ========================================

set "INSTALLDIR=%LOCALAPPDATA%\AStockQMT"
set "SERVICEDIR=%INSTALLDIR%\services"
set "MODULEDIR=%INSTALLDIR%\modules"
set "RUNTIME=%INSTALLDIR%\runtime"
set "STARTUPDIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "STARTVBS=%STARTUPDIR%\AStockQMTResearchRecorder.vbs"
set "WATCHDOG=%RUNTIME%\AStockResearchWatchdog.cmd"
set "LOGFILE=%RUNTIME%\research_recorder.log"
set "REC5=%SERVICEDIR%\qmt_research_recorder_v5.py"
set "MOD5=%MODULEDIR%\research_forward_model_v5.py"
set "MACD5=%MODULEDIR%\macd_calibration_v5.py"
set "TMPREC=%TEMP%\astock_rec_v5.py"
set "TMPMOD=%TEMP%\astock_model_v5.py"
set "TMPMACD=%TEMP%\astock_macd_v5.py"
set "KILLVBS=%TEMP%\astock_stop_research_v5.vbs"
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

echo [1/7] Downloading V5...
del /q "%TMPREC%" "%TMPMOD%" "%TMPMACD%" >nul 2>&1
curl.exe -L --fail --retry 3 --connect-timeout 8 -o "%TMPREC%" "%BASE%/services/qmt_research_recorder_v5.py"
if errorlevel 1 goto :download_fail
curl.exe -L --fail --retry 3 --connect-timeout 8 -o "%TMPMOD%" "%BASE%/modules/research_forward_model_v5.py"
if errorlevel 1 goto :download_fail
curl.exe -L --fail --retry 3 --connect-timeout 8 -o "%TMPMACD%" "%BASE%/modules/macd_calibration_v5.py"
if errorlevel 1 goto :download_fail

findstr /C:"research-recorder-v5-20260827" "%TMPREC%" >nul 2>&1
if errorlevel 1 goto :marker_fail
findstr /C:"research-shadow-v5-direction-confidence-macd-structure" "%TMPMOD%" >nul 2>&1
if errorlevel 1 goto :marker_fail
findstr /C:"macd-structure-v9" "%TMPMACD%" >nul 2>&1
if errorlevel 1 goto :marker_fail

echo [2/7] Syntax checking...
"%PYEXE%" -m py_compile "%TMPREC%" "%TMPMOD%" "%TMPMACD%"
if errorlevel 1 (
  echo [ERROR] Syntax check failed. Existing recorder was not changed.
  pause
  exit /b 1
)

echo [3/7] Stopping research recorder only...
(
  echo On Error Resume Next
  echo Set svc = GetObject("winmgmts:\\.\root\cimv2"^)
  echo For Each p In svc.ExecQuery("Select * from Win32_Process"^)
  echo   cmd = LCase("" ^& p.CommandLine^)
  echo   If InStr(cmd, "qmt_research_recorder_v3.py"^) ^> 0 Or InStr(cmd, "qmt_research_recorder_v4.py"^) ^> 0 Or InStr(cmd, "qmt_research_recorder_v4b.py"^) ^> 0 Or InStr(cmd, "qmt_research_recorder_v5.py"^) ^> 0 Or InStr(cmd, "astockresearchwatchdog.cmd"^) ^> 0 Then
  echo     p.Terminate
  echo   End If
  echo Next
) > "%KILLVBS%"
cscript.exe //nologo "%KILLVBS%" >nul 2>&1
del /q "%KILLVBS%" >nul 2>&1
timeout /t 2 /nobreak >nul

echo [4/7] Installing files...
copy /Y "%TMPREC%" "%REC5%" >nul
if errorlevel 1 goto :install_fail
copy /Y "%TMPMOD%" "%MOD5%" >nul
if errorlevel 1 goto :install_fail
copy /Y "%TMPMACD%" "%MACD5%" >nul
if errorlevel 1 goto :install_fail
del /q "%TMPREC%" "%TMPMOD%" "%TMPMACD%" >nul 2>&1

echo [5/7] Rewriting hidden watchdog...
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
) > "%STARTVBS%"

echo [6/7] Starting V5 silently...
wscript.exe "%STARTVBS%"
timeout /t 6 /nobreak >nul

echo [7/7] Checking V5 marker...
findstr /C:"research-recorder-v5-20260827" "%LOGFILE%" >nul 2>&1
if errorlevel 1 (
  echo [WARN] V5 is installed, but startup marker is not visible yet.
  echo Keep QMT logged in. The watchdog will continue retrying.
) else (
  echo [PASS] V5 research recorder is running silently.
)

echo.
echo [OK] V5 installed. No reboot required.
echo - V4b direction engine is retained.
echo - MACD no longer directly decides the direction score.
echo - 1m/5m/15m/30m/60m MACD structures calibrate confidence.
echo - Confidence is NOT probability and NOT historical win rate yet.
echo - Cross age, histogram change/acceleration and DIF/DEA slopes are recorded.
echo - Existing raw QMT data, forward evaluation and volume profile remain.
echo - QMT cloud/mobile bridge was NOT stopped.
echo.
pause
exit /b 0

:download_fail
echo [ERROR] Download failed. Existing recorder was not changed.
pause
exit /b 1

:marker_fail
echo [ERROR] Downloaded V5 files failed version check.
pause
exit /b 1

:install_fail
echo [ERROR] Installation failed.
pause
exit /b 1
