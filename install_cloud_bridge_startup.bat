@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo A-Stock QMT Cloud Bridge - Stable Install
echo ========================================

for /f "delims=" %%I in ('py -c "import sys; print(sys.executable)" 2^>nul') do set "PYEXE=%%I"
if not defined PYEXE (
  echo [ERROR] Python launcher 'py' was not found.
  pause
  exit /b 1
)
set "PYWEXE=%PYEXE:python.exe=pythonw.exe%"
if not exist "%PYEXE%" (
  echo [ERROR] Python was not found: %PYEXE%
  pause
  exit /b 1
)
if not exist "%PYWEXE%" set "PYWEXE=%PYEXE%"

"%PYEXE%" -c "from xtquant import xtdata" >nul 2>&1
if errorlevel 1 (
  echo [ERROR] xtquant is not available in this Python environment.
  echo Run repair_and_start_bridge.bat first and make sure QMT can connect.
  pause
  exit /b 1
)

set "PERSIST=%USERPROFILE%\.a_stock_qmt\secrets.toml"
if not exist "%PERSIST%" (
  echo [ERROR] Persistent bridge configuration not found:
  echo %PERSIST%
  echo Run repair_and_start_bridge.bat once before installing startup.
  pause
  exit /b 1
)

set "INSTALLDIR=%LOCALAPPDATA%\AStockQMT"
set "STARTUPDIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "STARTFILE=%STARTUPDIR%\AStockQMTCloudBridge.cmd"

echo [1/4] Installing stable bridge files to:
echo       %INSTALLDIR%
if not exist "%INSTALLDIR%" mkdir "%INSTALLDIR%" >nul 2>&1

robocopy "%~dp0modules" "%INSTALLDIR%\modules" /E /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 (
  echo [ERROR] Failed to copy modules.
  pause
  exit /b 1
)
robocopy "%~dp0services" "%INSTALLDIR%\services" /E /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 (
  echo [ERROR] Failed to copy services.
  pause
  exit /b 1
)

echo [2/4] Writing startup launcher...
(
  echo @echo off
  echo set "PYTHONPATH=%INSTALLDIR%;%%PYTHONPATH%%"
  echo set "NO_PROXY=.supabase.co,supabase.co"
  echo set "no_proxy=.supabase.co,supabase.co"
  echo if not exist "%INSTALLDIR%\runtime" mkdir "%INSTALLDIR%\runtime" ^>nul 2^>^&1
  echo start "AStockQMTBridge" /min "%PYEXE%" "%INSTALLDIR%\services\qmt_cloud_bridge.py" ^>^> "%INSTALLDIR%\runtime\bridge_console.log" 2^>^&1
) > "%STARTFILE%"

if not exist "%STARTFILE%" (
  echo [ERROR] Could not create startup launcher.
  pause
  exit /b 1
)

echo [3/4] Removing old Scheduled Task if present...
schtasks /Delete /TN "AStockQMTCloudBridge" /F >nul 2>&1

echo [4/4] Starting bridge now...
call "%STARTFILE%"
timeout /t 3 /nobreak >nul

echo.
echo [OK] Startup installed without administrator rights.
echo Stable install: %INSTALLDIR%
echo Startup file:   %STARTFILE%
echo Log file:       %INSTALLDIR%\runtime\bridge_console.log
echo.
echo IMPORTANT: Guosheng QMT must be open and logged in for live quotes.
echo You can move or delete the downloaded ZIP folder after this install.
pause
endlocal
