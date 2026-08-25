@echo off
setlocal EnableExtensions
chcp 65001 >nul

echo ========================================
echo A-Stock Research Recorder V3 Updater
echo ========================================

set "INSTALLDIR=%LOCALAPPDATA%\AStockQMT"
set "SERVICEDIR=%INSTALLDIR%\services"
set "MODULEDIR=%INSTALLDIR%\modules"
set "RUNTIME=%INSTALLDIR%\runtime"
set "STARTUPDIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "OLDSTART=%STARTUPDIR%\AStockQMTResearchRecorder.cmd"
set "STARTVBS=%STARTUPDIR%\AStockQMTResearchRecorder.vbs"
set "WATCHDOG=%RUNTIME%\AStockResearchWatchdog.cmd"
set "LOGFILE=%RUNTIME%\research_recorder.log"
set "RECORDER=%SERVICEDIR%\qmt_research_recorder_v3.py"
set "MODEL=%MODULEDIR%\research_forward_model.py"
set "TMPREC=%TEMP%\qmt_research_recorder_v3.py"
set "TMPMODEL=%TEMP%\research_forward_model.py"
set "URLREC=https://raw.githubusercontent.com/richard663-ui/-a-stock-app/main/services/qmt_research_recorder_v3.py"
set "URLMODEL=https://raw.githubusercontent.com/richard663-ui/-a-stock-app/main/modules/research_forward_model.py"

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

echo [1/7] Downloading V3...
del /q "%TMPREC%" "%TMPMODEL%" >nul 2>&1
curl.exe -L --fail --retry 3 --connect-timeout 8 -o "%TMPREC%" "%URLREC%"
if errorlevel 1 goto :download_fail
curl.exe -L --fail --retry 3 --connect-timeout 8 -o "%TMPMODEL%" "%URLMODEL%"
if errorlevel 1 goto :download_fail

findstr /C:"research-recorder-v3-20260825" "%TMPREC%" >nul 2>&1
if errorlevel 1 goto :marker_fail

echo [2/7] Syntax checking...
"%PYEXE%" -m py_compile "%TMPREC%" "%TMPMODEL%"
if errorlevel 1 (
  echo [ERROR] Syntax check failed. Existing recorder was not changed.
  pause
  exit /b 1
)

echo [3/7] Stopping old visible recorder...
taskkill /FI "WINDOWTITLE eq AStockResearchRecorder*" /T /F >nul 2>&1
timeout /t 2 /nobreak >nul

echo [4/7] Installing files...
copy /Y "%TMPREC%" "%RECORDER%" >nul
if errorlevel 1 goto :install_fail
copy /Y "%TMPMODEL%" "%MODEL%" >nul
if errorlevel 1 goto :install_fail
del /q "%TMPREC%" "%TMPMODEL%" >nul 2>&1

echo [5/7] Creating hidden watchdog...
(
  echo @echo off
  echo set "PYTHONPATH=%INSTALLDIR%;%%PYTHONPATH%%"
  echo set "NO_PROXY=.supabase.co,supabase.co"
  echo set "no_proxy=.supabase.co,supabase.co"
  echo cd /d "%INSTALLDIR%"
  echo :research_loop
  echo "%PYEXE%" -u "%RECORDER%" ^>^> "%LOGFILE%" 2^>^&1
  echo set "RC=%%ERRORLEVEL%%"
  echo if "%%RC%%"=="17" exit /b 0
  echo echo [%%DATE%% %%TIME%%] recorder exited code %%RC%% - restarting in 5 seconds ^>^> "%LOGFILE%"
  echo timeout /t 5 /nobreak ^>nul
  echo goto research_loop
) > "%WATCHDOG%"

del /q "%OLDSTART%" >nul 2>&1
(
  echo Set sh = CreateObject("WScript.Shell"^)
  echo sh.Run "cmd.exe /d /c ""%WATCHDOG%""", 0, False
) > "%STARTVBS%"

echo [6/7] Starting hidden recorder...
wscript.exe "%STARTVBS%"
timeout /t 5 /nobreak >nul

echo [7/7] Checking V3 marker...
findstr /C:"research-recorder-v3-20260825" "%LOGFILE%" >nul 2>&1
if errorlevel 1 (
  echo [WARN] V3 is installed, but the startup marker is not visible yet.
  echo QMT may not be ready. The recorder will keep retrying in the background.
) else (
  echo [PASS] V3 research recorder is running silently.
)

echo.
echo [OK] Recorder V3 installed.
echo - No CMD/PowerShell window needs to stay open.
echo - It starts silently with Windows.
echo - It retries if QMT is not ready yet.
echo - 60s/120s evaluation samples are strictly non-overlapping.
echo - Late/restart-contaminated samples are excluded instead of counted.
echo - Five-level order-book changes are preserved in raw local data.
echo - Cloud upload failures retry from the local database.
echo.
echo Keep QMT logged in during market hours. No reboot required.
pause
exit /b 0

:download_fail
echo [ERROR] Download failed. Existing recorder was not changed.
pause
exit /b 1

:marker_fail
echo [ERROR] Downloaded recorder failed version check.
pause
exit /b 1

:install_fail
echo [ERROR] Installation failed.
pause
exit /b 1
