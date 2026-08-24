@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo A-Stock QMT V18 Final - Stable Install
echo ========================================

set "PYEXE="
for /f "delims=" %%I in ('py -c "import sys; print(sys.executable)" 2^>nul') do set "PYEXE=%%I"
if not defined PYEXE for /f "delims=" %%I in ('python -c "import sys; print(sys.executable)" 2^>nul') do set "PYEXE=%%I"
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PYEXE (
  echo [ERROR] Python 3.12 could not be located.
  pause
  exit /b 1
)
if not exist "%PYEXE%" (
  echo [ERROR] Python path does not exist: %PYEXE%
  pause
  exit /b 1
)

echo [1/7] Python: %PYEXE%
"%PYEXE%" -c "import pandas,numpy,requests,certifi; from xtquant import xtdata" >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Required local packages are missing.
  echo Required: pandas numpy requests certifi xtquant
  echo Run repair_and_start_bridge.bat once, then retry.
  pause
  exit /b 1
)

echo [2/7] Running deterministic V18 self-test...
set "PYTHONPATH=%~dp0;%PYTHONPATH%"
"%PYEXE%" "%~dp0services\v18_selftest.py"
if errorlevel 1 (
  echo [ERROR] V18 self-test failed. Nothing has been installed.
  pause
  exit /b 1
)

set "PERSIST=%USERPROFILE%\.a_stock_qmt\secrets.toml"
echo [3/7] Checking persistent configuration...
if not exist "%PERSIST%" (
  echo [ERROR] Missing: %PERSIST%
  echo Run repair_and_start_bridge.bat once. Do not paste the secret key into chat.
  pause
  exit /b 1
)

set "INSTALLDIR=%LOCALAPPDATA%\AStockQMT"
set "STARTUPDIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "STARTFILE=%STARTUPDIR%\AStockQMTCloudBridge.cmd"
set "LOGFILE=%INSTALLDIR%\runtime\bridge_console.log"

echo [4/7] Stopping any old bridge process...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$self=$PID; Get-CimInstance Win32_Process ^| Where-Object { $_.ProcessId -ne $self -and $_.CommandLine -match 'qmt_cloud_bridge\.py' } ^| ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1
timeout /t 1 /nobreak >nul

echo [5/7] Copying stable runtime to %INSTALLDIR% ...
if not exist "%INSTALLDIR%" mkdir "%INSTALLDIR%" >nul 2>&1
robocopy "%~dp0modules" "%INSTALLDIR%\modules" /MIR /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 (
  echo [ERROR] Failed to copy modules.
  pause
  exit /b 1
)
robocopy "%~dp0services" "%INSTALLDIR%\services" /MIR /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 (
  echo [ERROR] Failed to copy services.
  pause
  exit /b 1
)
if not exist "%INSTALLDIR%\runtime" mkdir "%INSTALLDIR%\runtime" >nul 2>&1

echo [6/7] Writing Windows Startup launcher with watchdog...
(
  echo @echo off
  echo set "PYTHONPATH=%INSTALLDIR%;%%PYTHONPATH%%"
  echo set "NO_PROXY=.supabase.co,supabase.co"
  echo set "no_proxy=.supabase.co,supabase.co"
  echo cd /d "%INSTALLDIR%"
  echo if not exist "%INSTALLDIR%\runtime" mkdir "%INSTALLDIR%\runtime" ^>nul 2^>^&1
  echo :astock_loop
  echo "%PYEXE%" -u "%INSTALLDIR%\services\qmt_cloud_bridge.py" ^>^> "%LOGFILE%" 2^>^&1
  echo echo [%%DATE%% %%TIME%%] bridge exited code %%ERRORLEVEL%% - restarting in 3 seconds ^>^> "%LOGFILE%"
  echo timeout /t 3 /nobreak ^>nul
  echo goto astock_loop
) > "%STARTFILE%"
if not exist "%STARTFILE%" (
  echo [ERROR] Startup launcher was not created.
  pause
  exit /b 1
)
schtasks /Delete /TN "AStockQMTCloudBridge" /F >nul 2>&1

echo [7/7] Starting and verifying V18 bridge...
> "%LOGFILE%" echo ===== V18 install %DATE% %TIME% =====
start "AStockQMTBridge" /min cmd /c ""%STARTFILE%""
timeout /t 5 /nobreak >nul
findstr /C:"QMT cloud bridge V18 Final started" "%LOGFILE%" >nul 2>&1
if errorlevel 1 (
  echo [WARN] Installer finished, but the V18 start line was not confirmed yet.
  echo Run check_v18_final.bat. Its output will show the exact failing layer.
) else (
  echo [PASS] V18 bridge process confirmed in log.
)

echo.
echo [OK] V18 Final installed.
echo Stable runtime: %INSTALLDIR%
echo Startup file:   %STARTFILE%
echo Bridge log:     %LOGFILE%
echo Prediction DB:  %INSTALLDIR%\runtime\one_minute_predictions.sqlite3
echo Watchdog:       ON - bridge restarts automatically after an unexpected exit.
echo.
echo QMT must be open and logged in for live Tick/Level-2 data.
echo After this install, the downloaded ZIP folder is no longer used by the bridge.
pause
endlocal
