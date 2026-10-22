@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul

echo ========================================
echo A-Stock Research Recorder Installer V2
echo ========================================

set "INSTALLDIR=%LOCALAPPDATA%\AStockQMT"
set "SERVICEDIR=%INSTALLDIR%\services"
set "MODULEDIR=%INSTALLDIR%\modules"
set "RUNTIME=%INSTALLDIR%\runtime"
set "STARTUPDIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "STARTFILE=%STARTUPDIR%\AStockQMTResearchRecorder.cmd"
set "LOGFILE=%RUNTIME%\research_recorder.log"
set "RECORDER=%SERVICEDIR%\qmt_research_recorder.py"
set "MODEL=%MODULEDIR%\research_forward_model.py"
set "TMPREC=%TEMP%\qmt_research_recorder_v2.py"
set "TMPMODEL=%TEMP%\research_forward_model_v2.py"
set "URLREC=https://raw.githubusercontent.com/richard663-ui/-a-stock-app/main/services/qmt_research_recorder.py"
set "URLMODEL=https://raw.githubusercontent.com/richard663-ui/-a-stock-app/main/modules/research_forward_model.py"
set "WATCHLIST=%USERPROFILE%\.a_stock_qmt\research_watchlist.txt"
set "DATAROOT=%USERPROFILE%\AStockData"

if not exist "%INSTALLDIR%" (
  echo [ERROR] Stable AStock runtime not found: %INSTALLDIR%
  echo Install V18 first, then run this installer again.
  pause
  exit /b 1
)
if not exist "%SERVICEDIR%" mkdir "%SERVICEDIR%" >nul 2>&1
if not exist "%MODULEDIR%" mkdir "%MODULEDIR%" >nul 2>&1
if not exist "%RUNTIME%" mkdir "%RUNTIME%" >nul 2>&1
if not exist "%USERPROFILE%\.a_stock_qmt" mkdir "%USERPROFILE%\.a_stock_qmt" >nul 2>&1
if not exist "%DATAROOT%" mkdir "%DATAROOT%" >nul 2>&1

set "PYEXE="
for /f "delims=" %%I in ('py -c "import sys; print(sys.executable)" 2^>nul') do set "PYEXE=%%I"
if not defined PYEXE for /f "delims=" %%I in ('python -c "import sys; print(sys.executable)" 2^>nul') do set "PYEXE=%%I"
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PYEXE (
  echo [ERROR] Python could not be located.
  pause
  exit /b 1
)

"%PYEXE%" -c "from xtquant import xtdata; import pandas,requests,certifi" >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Required QMT packages are missing.
  echo Run repair_and_start_bridge.bat once, then retry.
  pause
  exit /b 1
)

if not exist "%WATCHLIST%" (
  > "%WATCHLIST%" echo # AStock research watchlist
  >> "%WATCHLIST%" echo # One symbol per line, for example: 000400.SZ
  >> "%WATCHLIST%" echo # Current phone-selected stock is recorded automatically too.
)

echo [1/6] Downloading recorder and research model...
del /q "%TMPREC%" "%TMPMODEL%" >nul 2>&1
curl.exe -L --fail --retry 3 --connect-timeout 8 -o "%TMPREC%" "%URLREC%"
if errorlevel 1 goto :download_fail
curl.exe -L --fail --retry 3 --connect-timeout 8 -o "%TMPMODEL%" "%URLMODEL%"
if errorlevel 1 goto :download_fail
findstr /C:"AStock Research Recorder started" "%TMPREC%" >nul 2>&1
if errorlevel 1 goto :marker_fail
findstr /C:"research-shadow-v2-20260825" "%TMPMODEL%" >nul 2>&1
if errorlevel 1 goto :marker_fail

echo [2/6] Checking Python syntax...
"%PYEXE%" -m py_compile "%TMPREC%" "%TMPMODEL%"
if errorlevel 1 (
  echo [ERROR] Syntax check failed. Nothing was installed.
  pause
  exit /b 1
)

echo [3/6] Installing files...
copy /Y "%TMPREC%" "%RECORDER%" >nul
if errorlevel 1 goto :install_fail
copy /Y "%TMPMODEL%" "%MODEL%" >nul
if errorlevel 1 goto :install_fail
del /q "%TMPREC%" "%TMPMODEL%" >nul 2>&1

echo [4/6] Writing Windows Startup watchdog...
(
  echo @echo off
  echo set "PYTHONPATH=%INSTALLDIR%;%%PYTHONPATH%%"
  echo set "NO_PROXY=.supabase.co,supabase.co"
  echo set "no_proxy=.supabase.co,supabase.co"
  echo cd /d "%INSTALLDIR%"
  echo if not exist "%RUNTIME%" mkdir "%RUNTIME%" ^>nul 2^>^&1
  echo :research_loop
  echo "%PYEXE%" -u "%RECORDER%" ^>^> "%LOGFILE%" 2^>^&1
  echo echo [%%DATE%% %%TIME%%] recorder exited code %%ERRORLEVEL%% - restarting in 5 seconds ^>^> "%LOGFILE%"
  echo timeout /t 5 /nobreak ^>nul
  echo goto research_loop
) > "%STARTFILE%"
if not exist "%STARTFILE%" goto :install_fail

echo [5/6] Stopping an old recorder instance if present...
taskkill /FI "WINDOWTITLE eq AStockResearchRecorder*" /T /F >nul 2>&1
timeout /t 2 /nobreak >nul

echo [6/6] Starting V2 recorder...
start "AStockResearchRecorder" /min cmd /c ""%STARTFILE%""
timeout /t 5 /nobreak >nul
findstr /C:"research-shadow-v2-20260825" "%LOGFILE%" >nul 2>&1
if errorlevel 1 (
  echo [WARN] Installer finished, but the V2 marker is not visible in the log yet.
  echo Keep QMT logged in and run check_research_recorder.bat after 10 seconds.
) else (
  echo [PASS] V2 research recorder is running.
)

echo.
echo [OK] Research Recorder V2 installed.
echo Data folder: %DATAROOT%
echo It records raw QMT data, 5-second score traces, and independent 60s/120s forward samples.
echo Only scored evaluation rows are copied to Supabase for remote review.
echo No reboot is required.
pause
exit /b 0

:download_fail
echo [ERROR] Download failed. Existing recorder was not changed.
pause
exit /b 1

:marker_fail
echo [ERROR] Downloaded files failed marker validation. Existing recorder was not changed.
pause
exit /b 1

:install_fail
echo [ERROR] Could not install the recorder files.
pause
exit /b 1
