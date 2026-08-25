@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul

echo ========================================
echo A-Stock Research Recorder V2 Update
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

if not exist "%INSTALLDIR%" (
  echo [ERROR] Stable runtime not found: %INSTALLDIR%
  pause
  exit /b 1
)
if not exist "%SERVICEDIR%" mkdir "%SERVICEDIR%" >nul 2>&1
if not exist "%MODULEDIR%" mkdir "%MODULEDIR%" >nul 2>&1
if not exist "%RUNTIME%" mkdir "%RUNTIME%" >nul 2>&1

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
  pause
  exit /b 1
)

echo [1/6] Downloading V2 recorder files...
del /q "%TMPREC%" "%TMPMODEL%" >nul 2>&1
curl.exe -L --fail --retry 3 --connect-timeout 8 -o "%TMPREC%" "%URLREC%"
if errorlevel 1 goto :download_fail
curl.exe -L --fail --retry 3 --connect-timeout 8 -o "%TMPMODEL%" "%URLMODEL%"
if errorlevel 1 goto :download_fail
findstr /C:"AStock Research Recorder started" "%TMPREC%" >nul 2>&1
if errorlevel 1 goto :marker_fail
findstr /C:"research-shadow-v2-20260825" "%TMPMODEL%" >nul 2>&1
if errorlevel 1 goto :marker_fail

echo [2/6] Running syntax checks...
"%PYEXE%" -m py_compile "%TMPREC%" "%TMPMODEL%"
if errorlevel 1 (
  echo [ERROR] Syntax check failed. Existing recorder was not changed.
  pause
  exit /b 1
)

echo [3/6] Stopping only the old research recorder...
taskkill /FI "WINDOWTITLE eq AStockResearchRecorder*" /T /F >nul 2>&1
timeout /t 2 /nobreak >nul

echo [4/6] Installing V2 files...
copy /Y "%TMPREC%" "%RECORDER%" >nul
if errorlevel 1 goto :install_fail
copy /Y "%TMPMODEL%" "%MODEL%" >nul
if errorlevel 1 goto :install_fail
del /q "%TMPREC%" "%TMPMODEL%" >nul 2>&1

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

echo [5/6] Starting V2 recorder...
start "AStockResearchRecorder" /min cmd /c ""%STARTFILE%""
timeout /t 5 /nobreak >nul

echo [6/6] Verifying start marker...
findstr /C:"research-shadow-v2-20260825" "%LOGFILE%" >nul 2>&1
if errorlevel 1 (
  echo [WARN] Update installed, but the V2 log marker is not visible yet.
  echo Keep QMT logged in and run check_research_recorder.bat after 10 seconds.
) else (
  echo [PASS] V2 research recorder is running.
)

echo.
echo [OK] Forward-evaluation recorder V2 installed.
echo It now records:
echo - raw QMT five-level snapshots locally
 echo - 5-second score traces locally
 echo - independent 60-second evaluation buckets
 echo - independent 120-second evaluation buckets
 echo - scored evaluation rows to Supabase for remote review
 echo.
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
echo [ERROR] Could not install V2 files.
pause
exit /b 1
