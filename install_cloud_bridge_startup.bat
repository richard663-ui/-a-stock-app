@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo A-Stock QMT V18 Final - Stable Install
echo ========================================

set "PYEXE="
for /f "delims=" %%I in ('py -c "import sys; print(sys.executable)" 2^>nul') do set "PYEXE=%%I"
if not defined PYEXE (
  for /f "delims=" %%I in ('python -c "import sys; print(sys.executable)" 2^>nul') do set "PYEXE=%%I"
)
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

echo [1/6] Python: %PYEXE%
"%PYEXE%" -c "import pandas,numpy,requests,certifi; from xtquant import xtdata" >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Required local packages are missing.
  echo Required: pandas numpy requests certifi xtquant
  echo Run repair_and_start_bridge.bat once, then retry this installer.
  pause
  exit /b 1
)

echo [2/6] Running deterministic V18 self-test...
set "PYTHONPATH=%~dp0;%PYTHONPATH%"
"%PYEXE%" "%~dp0services\v18_selftest.py"
if errorlevel 1 (
  echo [ERROR] V18 self-test failed. Nothing has been installed.
  pause
  exit /b 1
)

set "PERSIST=%USERPROFILE%\.a_stock_qmt\secrets.toml"
echo [3/6] Checking persistent configuration...
if not exist "%PERSIST%" (
  echo [ERROR] Missing: %PERSIST%
  echo Run repair_and_start_bridge.bat once. Do not paste the secret key into chat.
  pause
  exit /b 1
)

set "INSTALLDIR=%LOCALAPPDATA%\AStockQMT"
set "STARTUPDIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "STARTFILE=%STARTUPDIR%\AStockQMTCloudBridge.cmd"

echo [4/6] Copying stable runtime to %INSTALLDIR% ...
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

echo [5/6] Writing Windows Startup launcher...
(
  echo @echo off
  echo set "PYTHONPATH=%INSTALLDIR%;%%PYTHONPATH%%"
  echo set "NO_PROXY=.supabase.co,supabase.co"
  echo set "no_proxy=.supabase.co,supabase.co"
  echo cd /d "%INSTALLDIR%"
  echo if not exist "%INSTALLDIR%\runtime" mkdir "%INSTALLDIR%\runtime" ^>nul 2^>^&1
  echo "%PYEXE%" -u "%INSTALLDIR%\services\qmt_cloud_bridge.py" ^>^> "%INSTALLDIR%\runtime\bridge_console.log" 2^>^&1
) > "%STARTFILE%"
if not exist "%STARTFILE%" (
  echo [ERROR] Startup launcher was not created.
  pause
  exit /b 1
)
schtasks /Delete /TN "AStockQMTCloudBridge" /F >nul 2>&1

echo [6/6] Starting V18 bridge now...
start "AStockQMTBridge" /min cmd /c ""%STARTFILE%""
timeout /t 4 /nobreak >nul

echo.
echo [OK] V18 Final installed.
echo Stable runtime: %INSTALLDIR%
echo Startup file:   %STARTFILE%
echo Bridge log:     %INSTALLDIR%\runtime\bridge_console.log
echo Prediction DB:  %INSTALLDIR%\runtime\one_minute_predictions.sqlite3
echo.
echo QMT must be open and logged in for live Tick/Level-2 data.
echo After this install, the downloaded ZIP folder is no longer used by the bridge.
pause
endlocal
