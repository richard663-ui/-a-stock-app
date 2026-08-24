@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul

echo ========================================
echo A-Stock QMT Bridge Reliability Hotfix
echo ========================================

set "INSTALLDIR=%LOCALAPPDATA%\AStockQMT"
set "SERVICEFILE=%INSTALLDIR%\services\qmt_cloud_bridge.py"
set "STARTUPDIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "STARTFILE=%STARTUPDIR%\AStockQMTCloudBridge.cmd"
set "LOGFILE=%INSTALLDIR%\runtime\bridge_console.log"
set "TMPFILE=%TEMP%\astock_qmt_cloud_bridge_hotfix.py"
set "RAWURL=https://raw.githubusercontent.com/richard663-ui/-a-stock-app/main/services/qmt_cloud_bridge.py"

if not exist "%INSTALLDIR%\services" (
  echo [ERROR] Stable runtime not found: %INSTALLDIR%
  echo Run the normal V18 installer first.
  pause
  exit /b 1
)

set "PYEXE="
for /f "delims=" %%I in ('py -c "import sys; print(sys.executable)" 2^>nul') do set "PYEXE=%%I"
if not defined PYEXE for /f "delims=" %%I in ('python -c "import sys; print(sys.executable)" 2^>nul') do set "PYEXE=%%I"
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PYEXE (
  echo [ERROR] Python could not be located.
  pause
  exit /b 1
)

echo [1/4] Downloading the verified bridge file...
del /q "%TMPFILE%" >nul 2>&1
curl.exe -L --fail --retry 3 --connect-timeout 8 -o "%TMPFILE%" "%RAWURL%"
if errorlevel 1 (
  echo [ERROR] Download failed. Existing bridge was not changed.
  pause
  exit /b 1
)
findstr /C:"Cloud I/O isolation: ON" "%TMPFILE%" >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Downloaded file did not pass the hotfix marker check.
  del /q "%TMPFILE%" >nul 2>&1
  pause
  exit /b 1
)

echo [2/4] Checking Python syntax...
"%PYEXE%" -m py_compile "%TMPFILE%"
if errorlevel 1 (
  echo [ERROR] Syntax check failed. Existing bridge was not changed.
  del /q "%TMPFILE%" >nul 2>&1
  pause
  exit /b 1
)

echo [3/4] Installing the non-blocking bridge...
copy /Y "%TMPFILE%" "%SERVICEFILE%" >nul
if errorlevel 1 (
  echo [ERROR] Could not update %SERVICEFILE%
  pause
  exit /b 1
)
if not exist "%INSTALLDIR%\runtime" mkdir "%INSTALLDIR%\runtime" >nul 2>&1

echo [4/4] Enabling automatic restart watchdog...
(
  echo @echo off
  echo set "PYTHONPATH=%INSTALLDIR%;%%PYTHONPATH%%"
  echo set "NO_PROXY=.supabase.co,supabase.co"
  echo set "no_proxy=.supabase.co,supabase.co"
  echo cd /d "%INSTALLDIR%"
  echo if not exist "%INSTALLDIR%\runtime" mkdir "%INSTALLDIR%\runtime" ^>nul 2^>^&1
  echo :astock_loop
  echo "%PYEXE%" -u "%SERVICEFILE%" ^>^> "%LOGFILE%" 2^>^&1
  echo echo [%%DATE%% %%TIME%%] bridge exited code %%ERRORLEVEL%% - restarting in 3 seconds ^>^> "%LOGFILE%"
  echo timeout /t 3 /nobreak ^>nul
  echo goto astock_loop
) > "%STARTFILE%"
if not exist "%STARTFILE%" (
  echo [ERROR] Startup watchdog could not be written.
  pause
  exit /b 1
)

del /q "%TMPFILE%" >nul 2>&1
echo.
echo [OK] Reliability hotfix installed.
echo - QMT sampling is separated from cloud HTTP delays.
echo - Cloud Tick payload is capped to the newest 600 rows.
echo - Tick and Level-2 uploads use separate background workers.
echo - The startup launcher now restarts the bridge after an unexpected exit.
echo.
echo IMPORTANT: The currently running old bridge is intentionally not force-killed.
echo After the market closes, restart Windows once to activate this version safely.
pause
endlocal
