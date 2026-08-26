@echo off
setlocal EnableExtensions
chcp 65001 >nul

echo ========================================
echo A-Stock Research Recorder V4b Updater
echo ========================================

set "INSTALLDIR=%LOCALAPPDATA%\AStockQMT"
set "SERVICEDIR=%INSTALLDIR%\services"
set "MODULEDIR=%INSTALLDIR%\modules"
set "RUNTIME=%INSTALLDIR%\runtime"
set "STARTUPDIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "STARTVBS=%STARTUPDIR%\AStockQMTResearchRecorder.vbs"
set "WATCHDOG=%RUNTIME%\AStockResearchWatchdog.cmd"
set "LOGFILE=%RUNTIME%\research_recorder.log"
set "REC3=%SERVICEDIR%\qmt_research_recorder_v3.py"
set "REC4=%SERVICEDIR%\qmt_research_recorder_v4.py"
set "REC4B=%SERVICEDIR%\qmt_research_recorder_v4b.py"
set "MOD4=%MODULEDIR%\research_forward_model_v4.py"
set "MOD4B=%MODULEDIR%\research_forward_model_v4b.py"
set "TMP3=%TEMP%\astock_rec_v3.py"
set "TMP4=%TEMP%\astock_rec_v4.py"
set "TMP4B=%TEMP%\astock_rec_v4b.py"
set "TMPM4=%TEMP%\astock_model_v4.py"
set "TMPM4B=%TEMP%\astock_model_v4b.py"
set "KILLVBS=%TEMP%\astock_stop_research.vbs"
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

echo [1/7] Downloading V4b files...
del /q "%TMP3%" "%TMP4%" "%TMP4B%" "%TMPM4%" "%TMPM4B%" >nul 2>&1
curl.exe -L --fail --retry 3 --connect-timeout 8 -o "%TMP3%" "%BASE%/services/qmt_research_recorder_v3.py"
if errorlevel 1 goto :download_fail
curl.exe -L --fail --retry 3 --connect-timeout 8 -o "%TMP4%" "%BASE%/services/qmt_research_recorder_v4.py"
if errorlevel 1 goto :download_fail
curl.exe -L --fail --retry 3 --connect-timeout 8 -o "%TMP4B%" "%BASE%/services/qmt_research_recorder_v4b.py"
if errorlevel 1 goto :download_fail
curl.exe -L --fail --retry 3 --connect-timeout 8 -o "%TMPM4%" "%BASE%/modules/research_forward_model_v4.py"
if errorlevel 1 goto :download_fail
curl.exe -L --fail --retry 3 --connect-timeout 8 -o "%TMPM4B%" "%BASE%/modules/research_forward_model_v4b.py"
if errorlevel 1 goto :download_fail

findstr /C:"research-recorder-v4b-20260826" "%TMP4B%" >nul 2>&1
if errorlevel 1 goto :marker_fail
findstr /C:"research-shadow-v4b-grouped-stable-exhaustion-persistence" "%TMPM4B%" >nul 2>&1
if errorlevel 1 goto :marker_fail

echo [2/7] Syntax checking before install...
"%PYEXE%" -m py_compile "%TMP3%" "%TMP4%" "%TMP4B%" "%TMPM4%" "%TMPM4B%"
if errorlevel 1 (
  echo [ERROR] Syntax check failed. Existing recorder was not changed.
  pause
  exit /b 1
)

echo [3/7] Stopping only the research recorder/watchdog...
(
  echo On Error Resume Next
  echo Set svc = GetObject("winmgmts:\\.\root\cimv2"^)
  echo For Each p In svc.ExecQuery("Select * from Win32_Process"^)
  echo   cmd = LCase("" ^& p.CommandLine^)
  echo   If InStr(cmd, "qmt_research_recorder_v3.py"^) ^> 0 Or InStr(cmd, "qmt_research_recorder_v4.py"^) ^> 0 Or InStr(cmd, "qmt_research_recorder_v4b.py"^) ^> 0 Or InStr(cmd, "astockresearchwatchdog.cmd"^) ^> 0 Then
  echo     p.Terminate
  echo   End If
  echo Next
) > "%KILLVBS%"
cscript.exe //nologo "%KILLVBS%" >nul 2>&1
del /q "%KILLVBS%" >nul 2>&1
timeout /t 2 /nobreak >nul

echo [4/7] Installing V4b files...
copy /Y "%TMP3%" "%REC3%" >nul
if errorlevel 1 goto :install_fail
copy /Y "%TMP4%" "%REC4%" >nul
if errorlevel 1 goto :install_fail
copy /Y "%TMP4B%" "%REC4B%" >nul
if errorlevel 1 goto :install_fail
copy /Y "%TMPM4%" "%MOD4%" >nul
if errorlevel 1 goto :install_fail
copy /Y "%TMPM4B%" "%MOD4B%" >nul
if errorlevel 1 goto :install_fail
del /q "%TMP3%" "%TMP4%" "%TMP4B%" "%TMPM4%" "%TMPM4B%" >nul 2>&1

echo [5/7] Rewriting hidden watchdog...
(
  echo @echo off
  echo set "PYTHONPATH=%INSTALLDIR%;%%PYTHONPATH%%"
  echo set "NO_PROXY=.supabase.co,supabase.co"
  echo set "no_proxy=.supabase.co,supabase.co"
  echo cd /d "%INSTALLDIR%"
  echo :research_loop
  echo "%PYEXE%" -u "%REC4B%" ^>^> "%LOGFILE%" 2^>^&1
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

echo [6/7] Starting V4b silently...
wscript.exe "%STARTVBS%"
timeout /t 6 /nobreak >nul

echo [7/7] Checking V4b marker...
findstr /C:"research-recorder-v4b-20260826" "%LOGFILE%" >nul 2>&1
if errorlevel 1 (
  echo [WARN] V4b is installed, but its startup marker is not visible yet.
  echo The background watchdog will keep retrying. Keep QMT logged in.
) else (
  echo [PASS] V4b research recorder is running silently.
)

echo.
echo [OK] V4b installed. No reboot required.
echo - Grouped factor caps reduce double-counting.
echo - Exhaustion/reversal penalties are active.
echo - MACD is a bounded multi-cycle regime factor.
echo - 60s/120s scores use trailing median plus 3-of-4 persistence.
echo - Old manual high-confidence certification is disabled for V4b research.
echo - Intraday volume-at-price profile is now collected locally.
echo - QMT cloud/mobile bridge was NOT stopped by this updater.
echo.
echo Keep QMT logged in and continue the morning test.
pause
exit /b 0

:download_fail
echo [ERROR] Download failed. Existing recorder was not changed.
pause
exit /b 1

:marker_fail
echo [ERROR] Downloaded V4b files failed version check.
pause
exit /b 1

:install_fail
echo [ERROR] Installation failed.
pause
exit /b 1
