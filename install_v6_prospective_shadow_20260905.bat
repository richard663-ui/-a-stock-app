@echo off
setlocal EnableExtensions
chcp 65001 >nul

echo ======================================================
echo A-Stock V6 Prospective 60s Shadow
 echo Observed future bid settlement - Research Only
 echo Installer: v6-shadow-installer-20260905
 echo ======================================================

set "INSTALLDIR=%LOCALAPPDATA%\AStockQMT"
set "SERVICEDIR=%INSTALLDIR%\services"
set "RUNTIME=%INSTALLDIR%\runtime"
set "STARTUPDIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "BASE=https://raw.githubusercontent.com/richard663-ui/-a-stock-app/main"
set "RUNNER=l1_v6_shadow_runner_v1.py"
set "MARKER=l1-v6-shadow-runner-v1-observed-bid-20260905"
set "WATCHDOG=%RUNTIME%\AStockV6ShadowWatchdog.cmd"
set "LOGFILE=%RUNTIME%\v6_shadow_runner.log"
set "STARTVBS=%STARTUPDIR%\AStockV6ProspectiveShadow.vbs"
set "KILLVBS=%TEMP%\astock_v6_shadow_stop.vbs"

if not exist "%INSTALLDIR%" (
  echo [ERROR] AStock runtime not found: %INSTALLDIR%
  pause
  exit /b 1
)

set "PYEXE="
for /f "delims=" %%I in ('py -c "import sys;print(sys.executable)" 2^>nul') do set "PYEXE=%%I"
if not defined PYEXE for /f "delims=" %%I in ('python -c "import sys;print(sys.executable)" 2^>nul') do set "PYEXE=%%I"
if not defined PYEXE (
  echo [ERROR] Python not found.
  pause
  exit /b 1
)

if not exist "%SERVICEDIR%" mkdir "%SERVICEDIR%" >nul 2>&1
if not exist "%RUNTIME%" mkdir "%RUNTIME%" >nul 2>&1

echo [1/5] Downloading prospective shadow runner...
curl.exe -L --fail --retry 3 -o "%TEMP%\%RUNNER%" "%BASE%/services/%RUNNER%" || goto :fail_download
findstr /C:"%MARKER%" "%TEMP%\%RUNNER%" >nul || goto :fail_download

REM Refresh V6 trainer in case the local copy predates this installer.
curl.exe -L --fail --retry 3 -o "%TEMP%\train_l1_60s_model_v6_exec_aligned.py" "%BASE%/services/train_l1_60s_model_v6_exec_aligned.py" || goto :fail_download
findstr /C:"l1-60s-trainer-v6-exec-aligned-stock-intercept-robust-20260905" "%TEMP%\train_l1_60s_model_v6_exec_aligned.py" >nul || goto :fail_download

echo [2/5] Syntax checking before touching the shadow runtime...
set "PYTHONPATH=%INSTALLDIR%;%PYTHONPATH%"
"%PYEXE%" -m py_compile "%TEMP%\%RUNNER%" "%TEMP%\train_l1_60s_model_v6_exec_aligned.py"
if errorlevel 1 goto :fail_syntax

echo [3/5] Replacing only the shadow process...
(
  echo On Error Resume Next
  echo Set svc = GetObject("winmgmts:\\.\root\cimv2"^)
  echo For Each p In svc.ExecQuery("Select * from Win32_Process"^)
  echo   cmd = LCase("" ^& p.CommandLine^)
  echo   If InStr(cmd,"l1_v6_shadow_runner"^) ^> 0 Or InStr(cmd,"astockv6shadowwatchdog.cmd"^) ^> 0 Then p.Terminate
  echo Next
) > "%KILLVBS%"
cscript.exe //nologo "%KILLVBS%" >nul 2>&1
del /q "%KILLVBS%" >nul 2>&1
timeout /t 1 /nobreak >nul

copy /Y "%TEMP%\%RUNNER%" "%SERVICEDIR%\%RUNNER%" >nul || goto :fail_install
copy /Y "%TEMP%\train_l1_60s_model_v6_exec_aligned.py" "%SERVICEDIR%\train_l1_60s_model_v6_exec_aligned.py" >nul || goto :fail_install

 echo [4/5] Installing persistent hidden watchdog...
(
  echo @echo off
  echo set "PYTHONPATH=%INSTALLDIR%;%%PYTHONPATH%%"
  echo cd /d "%INSTALLDIR%"
  echo :loop
  echo "%PYEXE%" -u "%SERVICEDIR%\%RUNNER%" ^>^> "%LOGFILE%" 2^>^&1
  echo echo [%%DATE%% %%TIME%%] V6 shadow exited %%ERRORLEVEL%% - restart in 10s ^>^> "%LOGFILE%"
  echo timeout /t 10 /nobreak ^>nul
  echo goto loop
) > "%WATCHDOG%"
(
  echo Set sh = CreateObject("WScript.Shell"^)
  echo sh.Run "cmd.exe /d /c ""%WATCHDOG%""", 0, False
) > "%STARTVBS%"

wscript.exe "%STARTVBS%"
timeout /t 5 /nobreak >nul

echo [5/5] Verifying shadow runner marker...
findstr /C:"%MARKER%" "%LOGFILE%" >nul 2>&1
if errorlevel 1 (
  echo [WARN] Shadow runner installed; marker not visible yet.
  echo Check: %LOGFILE%
) else (
  echo [PASS] V6 prospective shadow runner active.
)

echo.
echo ======================================================
echo [OK] Prospective 60s shadow layer configured.
echo ======================================================
echo - Existing L1 recorder was NOT stopped.
echo - Existing V4/V5/V6 trainer daemon was NOT stopped.
echo - No production phone/live model was changed.
echo - Off-market only: if V6 model is missing, runner bootstraps it from prior data.
echo - Trading day: one score per stock per minute, model frozen for the whole day.
echo - +60s settlement uses OBSERVED future bid1 rows, not future-mid proxy.
echo - Raw UP/DOWN probabilities are retained even when action is WATCH.
echo - Results and heartbeat sync to Supabase automatically.
echo - Startup persistence is installed; you do not need to launch it every day.
echo.
pause
exit /b 0

:fail_download
echo [ERROR] Download/marker validation failed. Existing systems were untouched.
pause
exit /b 1
:fail_syntax
echo [ERROR] Syntax check failed. Existing systems were untouched.
pause
exit /b 1
:fail_install
echo [ERROR] Shadow install failed. Recorder/trainer remain untouched; re-run this BAT.
pause
exit /b 1
