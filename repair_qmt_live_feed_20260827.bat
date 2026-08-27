@echo off
setlocal EnableExtensions
chcp 65001 >nul

echo ========================================
echo A-Stock QMT Live Feed Repair 2026-08-27
echo ========================================

set "INSTALLDIR=%LOCALAPPDATA%\AStockQMT"
set "SERVICEFILE=%INSTALLDIR%\services\qmt_cloud_bridge.py"
set "RUNTIME=%INSTALLDIR%\runtime"
set "LOGFILE=%RUNTIME%\bridge_console.log"
set "WATCHDOG=%RUNTIME%\AStockQMTCloudBridgeWatchdog.cmd"
set "STARTUPDIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "OLDSTART=%STARTUPDIR%\AStockQMTCloudBridge.cmd"
set "STARTVBS=%STARTUPDIR%\AStockQMTCloudBridge.vbs"
set "TMPFILE=%TEMP%\astock_qmt_cloud_bridge_selfheal.py"
set "KILLVBS=%TEMP%\astock_stop_cloud_bridge.vbs"
set "RAWURL=https://raw.githubusercontent.com/richard663-ui/-a-stock-app/main/services/qmt_cloud_bridge.py"

if not exist "%INSTALLDIR%\services" (
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

if not exist "%RUNTIME%" mkdir "%RUNTIME%" >nul 2>&1

echo [1/6] Downloading self-healing bridge...
del /q "%TMPFILE%" >nul 2>&1
curl.exe -L --fail --retry 3 --connect-timeout 8 -o "%TMPFILE%" "%RAWURL%"
if errorlevel 1 goto :download_fail

findstr /C:"QMT self-heal: ON" "%TMPFILE%" >nul 2>&1
if errorlevel 1 goto :marker_fail

echo [2/6] Syntax checking before replacement...
"%PYEXE%" -m py_compile "%TMPFILE%"
if errorlevel 1 (
  echo [ERROR] Syntax check failed. Existing bridge was not changed.
  pause
  exit /b 1
)

echo [3/6] Stopping only the QMT cloud bridge...
(
  echo On Error Resume Next
  echo Set svc = GetObject("winmgmts:\\.\root\cimv2"^)
  echo For Each p In svc.ExecQuery("Select * from Win32_Process"^)
  echo   cmd = LCase("" ^& p.CommandLine^)
  echo   If InStr(cmd, "qmt_cloud_bridge.py"^) ^> 0 Or InStr(cmd, "astockqmtcloudbridgewatchdog.cmd"^) ^> 0 Then
  echo     p.Terminate
  echo   End If
  echo Next
) > "%KILLVBS%"
cscript.exe //nologo "%KILLVBS%" >nul 2>&1
del /q "%KILLVBS%" >nul 2>&1
timeout /t 2 /nobreak >nul

echo [4/6] Installing repaired bridge...
copy /Y "%TMPFILE%" "%SERVICEFILE%" >nul
if errorlevel 1 goto :install_fail
del /q "%TMPFILE%" >nul 2>&1

echo [5/6] Rebuilding silent watchdog...
(
  echo @echo off
  echo set "PYTHONPATH=%INSTALLDIR%;%%PYTHONPATH%%"
  echo set "NO_PROXY=.supabase.co,supabase.co"
  echo set "no_proxy=.supabase.co,supabase.co"
  echo cd /d "%INSTALLDIR%"
  echo :astock_loop
  echo "%PYEXE%" -u "%SERVICEFILE%" ^>^> "%LOGFILE%" 2^>^&1
  echo echo [%%DATE%% %%TIME%%] bridge exited code %%ERRORLEVEL%% - restarting in 3 seconds ^>^> "%LOGFILE%"
  echo timeout /t 3 /nobreak ^>nul
  echo goto astock_loop
) > "%WATCHDOG%"

del /q "%OLDSTART%" >nul 2>&1
(
  echo Set sh = CreateObject("WScript.Shell"^)
  echo sh.Run "cmd.exe /d /c ""%WATCHDOG%""", 0, False
) > "%STARTVBS%"

echo [6/6] Starting repaired bridge silently...
wscript.exe "%STARTVBS%"
timeout /t 6 /nobreak >nul

findstr /C:"QMT self-heal: ON" "%LOGFILE%" >nul 2>&1
if errorlevel 1 (
  echo [WARN] Repair is installed, but the new bridge marker is not visible yet.
  echo Keep QMT open and logged in. The bridge will retry automatically every 3 seconds.
) else (
  echo [PASS] Self-healing QMT bridge is running.
)

echo.
echo IMPORTANT:
echo - The repair no longer reports an empty QMT feed as ONLINE.
echo - It automatically retries QMT after a service disconnect.
echo - Five-level order-book-only changes are now retained.
echo - Research Recorder was NOT stopped or modified.
echo - No reboot is required.
echo.
echo If stocks still show no live data after about 10 seconds,
echo close and reopen QMT once, log in, and leave it open.
echo The repaired bridge will reconnect automatically.
pause
exit /b 0

:download_fail
echo [ERROR] Download failed. Existing bridge was not changed.
pause
exit /b 1

:marker_fail
echo [ERROR] Downloaded bridge failed version marker check.
pause
exit /b 1

:install_fail
echo [ERROR] Could not install the repaired bridge.
pause
exit /b 1
