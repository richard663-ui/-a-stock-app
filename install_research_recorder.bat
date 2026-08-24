@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul

echo ========================================
echo A-Stock Research Recorder Installer
echo ========================================

set "INSTALLDIR=%LOCALAPPDATA%\AStockQMT"
set "SERVICEDIR=%INSTALLDIR%\services"
set "RUNTIME=%INSTALLDIR%\runtime"
set "STARTUPDIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "STARTFILE=%STARTUPDIR%\AStockQMTResearchRecorder.cmd"
set "LOGFILE=%RUNTIME%\research_recorder.log"
set "RECORDER=%SERVICEDIR%\qmt_research_recorder.py"
set "TMPFILE=%TEMP%\qmt_research_recorder.py"
set "RAWURL=https://raw.githubusercontent.com/richard663-ui/-a-stock-app/main/services/qmt_research_recorder.py"
set "WATCHLIST=%USERPROFILE%\.a_stock_qmt\research_watchlist.txt"
set "DATAROOT=%USERPROFILE%\AStockData"

if not exist "%SERVICEDIR%" (
  echo [ERROR] Stable AStock runtime not found: %INSTALLDIR%
  echo Install V18 first, then run this installer again.
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

"%PYEXE%" -c "from xtquant import xtdata; import pandas,requests,certifi" >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Required QMT Python packages are missing.
  echo Run repair_and_start_bridge.bat once, then retry.
  pause
  exit /b 1
)

if not exist "%RUNTIME%" mkdir "%RUNTIME%" >nul 2>&1
if not exist "%USERPROFILE%\.a_stock_qmt" mkdir "%USERPROFILE%\.a_stock_qmt" >nul 2>&1
if not exist "%DATAROOT%" mkdir "%DATAROOT%" >nul 2>&1

if not exist "%WATCHLIST%" (
  > "%WATCHLIST%" echo # AStock research watchlist
  >> "%WATCHLIST%" echo # One symbol per line, for example: 000400.SZ
  >> "%WATCHLIST%" echo # Current phone-selected stock is recorded automatically too.
)

echo [1/5] Downloading recorder...
del /q "%TMPFILE%" >nul 2>&1
curl.exe -L --fail --retry 3 --connect-timeout 8 -o "%TMPFILE%" "%RAWURL%"
if errorlevel 1 (
  echo [ERROR] Download failed. Existing recorder was not changed.
  pause
  exit /b 1
)
findstr /C:"AStock Research Recorder started" "%TMPFILE%" >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Downloaded file failed marker check.
  del /q "%TMPFILE%" >nul 2>&1
  pause
  exit /b 1
)

echo [2/5] Checking syntax...
"%PYEXE%" -m py_compile "%TMPFILE%"
if errorlevel 1 (
  echo [ERROR] Python syntax check failed. Nothing was installed.
  del /q "%TMPFILE%" >nul 2>&1
  pause
  exit /b 1
)

echo [3/5] Installing recorder...
copy /Y "%TMPFILE%" "%RECORDER%" >nul
if errorlevel 1 (
  echo [ERROR] Could not install recorder.
  pause
  exit /b 1
)
del /q "%TMPFILE%" >nul 2>&1

echo [4/5] Writing Windows Startup watchdog...
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
if not exist "%STARTFILE%" (
  echo [ERROR] Startup launcher could not be written.
  pause
  exit /b 1
)

echo [5/5] Starting recorder now...
start "AStockResearchRecorder" /min cmd /c ""%STARTFILE%""
timeout /t 4 /nobreak >nul
findstr /C:"AStock Research Recorder started" "%LOGFILE%" >nul 2>&1
if errorlevel 1 (
  echo [WARN] Recorder process was started, but the start marker is not in the log yet.
  echo Keep QMT open and logged in, then run check_research_recorder.bat.
) else (
  echo [PASS] Research recorder is running.
)

echo.
echo [OK] Research data collection has been installed.
echo Data folder:      %DATAROOT%
echo Watchlist file:   %WATCHLIST%
echo Recorder log:     %LOGFILE%
echo Startup launcher: %STARTFILE%
echo.
echo IMPORTANT:
echo - This does NOT replace the phone/mobile bridge.
echo - Raw QMT five-level snapshots are stored locally only.
echo - GitHub and Supabase do NOT receive your research database.
echo - Keep QMT logged in during market hours.
pause
endlocal
