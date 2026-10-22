@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul

echo ======================================================
echo A-Stock External Level2API - One-Time Preparation
echo QMT stays L1; external gRPC supplies real Level-2
echo ======================================================

set "INSTALLDIR=%LOCALAPPDATA%\AStockQMT"
set "SERVICEDIR=%INSTALLDIR%\services"
set "MODULEDIR=%INSTALLDIR%\modules"
set "VENDOR=%INSTALLDIR%\vendor\level2api"
set "PROTO=%VENDOR%\proto"
set "CLI=%VENDOR%\cli"
set "CONF=%CLI%\conf"
set "RUNTIME=%INSTALLDIR%\runtime"
set "PERSIST=%USERPROFILE%\.a_stock_qmt"
set "PROVIDERCFG=%PERSIST%\external_l2.toml"
set "WATCHDOG=%RUNTIME%\AStockL2TrainingWatchdog.cmd"
set "LOGFILE=%RUNTIME%\l2_training_recorder.log"
set "STARTUPDIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "STARTVBS=%STARTUPDIR%\AStockL2TrainingRecorder.vbs"
set "BASE=https://raw.githubusercontent.com/richard663-ui/-a-stock-app/main"
set "VBASE=https://raw.githubusercontent.com/Level2API/L2-push-python/master"

if not exist "%INSTALLDIR%" (
  echo [ERROR] AStock runtime not found: %INSTALLDIR%
  echo Run the normal QMT installer first.
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
echo [1/8] Python: %PYEXE%

for %%D in ("%SERVICEDIR%" "%MODULEDIR%" "%VENDOR%" "%PROTO%" "%CLI%" "%CONF%" "%RUNTIME%" "%PERSIST%") do if not exist "%%~D" mkdir "%%~D" >nul 2>&1

echo [2/8] Installing gRPC build/runtime dependencies...
"%PYEXE%" -m pip install --disable-pip-version-check "grpcio>=1.60" "grpcio-tools>=1.60" "protobuf>=4.25"
if errorlevel 1 goto :fail

echo [3/8] Downloading AStock adapter + recorder...
curl.exe -L --fail --retry 3 -o "%MODULEDIR%\external_level2.py" "%BASE%/modules/external_level2.py" || goto :fail
curl.exe -L --fail --retry 3 -o "%MODULEDIR%\external_level2_v2.py" "%BASE%/modules/external_level2_v2.py" || goto :fail
curl.exe -L --fail --retry 3 -o "%SERVICEDIR%\qmt_l2_training_recorder_v7.py" "%BASE%/services/qmt_l2_training_recorder_v7.py" || goto :fail
curl.exe -L --fail --retry 3 -o "%SERVICEDIR%\test_external_level2.py" "%BASE%/services/test_external_level2.py" || goto :fail
findstr /C:"ExternalLevel2Manager" "%MODULEDIR%\external_level2.py" >nul || goto :fail
findstr /C:"subscription_loop" "%MODULEDIR%\external_level2_v2.py" >nul || goto :fail
findstr /C:"l2-training-recorder-v7-external-provider-20260903b" "%SERVICEDIR%\qmt_l2_training_recorder_v7.py" >nul || goto :fail

echo [4/8] Downloading the provider's PUBLIC proto definitions and Windows proxy binary...
curl.exe -L --fail --retry 3 -o "%PROTO%\entity.proto" "%VBASE%/proto/entity.proto" || goto :fail
curl.exe -L --fail --retry 3 -o "%PROTO%\proxy.proto" "%VBASE%/proto/proxy.proto" || goto :fail
if not exist "%CLI%\txtool.exe" (
  curl.exe -L --fail --retry 3 -o "%CLI%\txtool.exe" "%VBASE%/cli/txtool.exe" || goto :fail
)
if not exist "%CONF%\proxy.toml" curl.exe -L --fail --retry 3 -o "%CONF%\proxy.toml" "%VBASE%/cli/conf/proxy.toml" || goto :fail
if not exist "%CONF%\log.toml" curl.exe -L --fail --retry 3 -o "%CONF%\log.toml" "%VBASE%/cli/conf/log.toml" || goto :fail

for %%A in ("%CLI%\txtool.exe") do if %%~zA LSS 8000000 (
  echo [ERROR] txtool.exe download is unexpectedly small. It will NOT be executed.
  goto :fail
)

echo [5/8] Compiling current Python gRPC stubs locally from the provider proto...
"%PYEXE%" -m grpc_tools.protoc -I"%PROTO%" --python_out="%VENDOR%" --grpc_python_out="%VENDOR%" "%PROTO%\entity.proto" "%PROTO%\proxy.proto"
if errorlevel 1 goto :fail
"%PYEXE%" -m py_compile "%VENDOR%\entity_pb2.py" "%VENDOR%\proxy_pb2.py" "%VENDOR%\proxy_pb2_grpc.py" "%MODULEDIR%\external_level2.py" "%MODULEDIR%\external_level2_v2.py" "%SERVICEDIR%\qmt_l2_training_recorder_v7.py" "%SERVICEDIR%\test_external_level2.py"
if errorlevel 1 goto :fail

if not exist "%PROVIDERCFG%" (
  echo [provider]>"%PROVIDERCFG%"
  echo mode = 'level2api'>>"%PROVIDERCFG%"
  echo address = 'localhost:5000'>>"%PROVIDERCFG%"
  echo client_dir = '%VENDOR%'>>"%PROVIDERCFG%"
  echo auto_subscribe = true>>"%PROVIDERCFG%"
  echo topic_mask = 15>>"%PROVIDERCFG%"
  echo price_divisor = 0.0>>"%PROVIDERCFG%"
)

echo [6/8] Writing external-provider recorder watchdog...
(
  echo @echo off
  echo set "PYTHONPATH=%INSTALLDIR%;%%PYTHONPATH%%"
  echo cd /d "%INSTALLDIR%"
  echo :loop
  echo "%PYEXE%" -u "%SERVICEDIR%\qmt_l2_training_recorder_v7.py" ^>^> "%LOGFILE%" 2^>^&1
  echo echo [%%DATE%% %%TIME%%] V7 recorder exited %%ERRORLEVEL%% - restart in 5s ^>^> "%LOGFILE%"
  echo timeout /t 5 /nobreak ^>nul
  echo goto loop
) > "%WATCHDOG%"
(
  echo Set sh = CreateObject("WScript.Shell"^)
  echo sh.Run "cmd.exe /d /c ""%WATCHDOG%""", 0, False
) > "%STARTVBS%"


echo [7/8] Creating local helper scripts...
(
  echo @echo off
  echo notepad.exe "%CONF%\proxy.toml"
) > "%INSTALLDIR%\EDIT_LEVEL2API_CONFIG.bat"
(
  echo @echo off
  echo setlocal EnableExtensions
  echo chcp 65001 ^>nul
  echo set "PYTHONPATH=%INSTALLDIR%;%%PYTHONPATH%%"
  echo findstr /C:"User = 'X.XXXX'" "%CONF%\proxy.toml" ^>nul 2^>^&1 ^&^& ^(echo [ERROR] Fill User in proxy.toml first. ^& pause ^& exit /b 1^)
  echo findstr /C:"Passwd = 'XXXXXXXXXX'" "%CONF%\proxy.toml" ^>nul 2^>^&1 ^&^& ^(echo [ERROR] Fill Passwd in proxy.toml first. ^& pause ^& exit /b 1^)
  echo echo Starting provider txtool.exe from its own cli directory...
  echo pushd "%CLI%"
  echo start "Level2APIProxy" /min "%CLI%\txtool.exe" proxy
  echo popd
  echo timeout /t 4 /nobreak ^>nul
  echo echo Testing external adapter with 301236.SZ...
  echo "%PYEXE%" "%SERVICEDIR%\test_external_level2.py" --symbol 301236.SZ --seconds 10
  echo echo.
  echo echo The recorder retries subscriptions automatically. No recorder restart is needed after the proxy comes online.
  echo echo If market is closed, zero event counts are inconclusive. During market hours transaction/quote must rise.
  echo pause
) > "%INSTALLDIR%\START_AND_TEST_LEVEL2API.bat"

REM Restart only the ML recorder. QMT cloud bridge and forward recorder are untouched.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$self=$PID; Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $self -and $_.CommandLine -match 'qmt_l2_training_recorder' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1
wscript.exe "%STARTVBS%"
timeout /t 3 /nobreak >nul

echo [8/8] Ready for provider credentials.
echo.
echo ======================================================
echo [OK] External Level2API integration is PREPARED.
echo ======================================================
echo You only need to edit this local file:
echo   %CONF%\proxy.toml
echo.
echo Fill ONLY the values supplied by the provider:
echo   User
echo   Passwd
echo   RpcServer
echo   TcpServer
echo Keep Address = localhost:5000 unless the provider tells you otherwise.
echo.
echo After filling it, run:
echo   %INSTALLDIR%\START_AND_TEST_LEVEL2API.bat
echo.
echo The fixed 8-stock basket is subscribed automatically with topic mask 15:
echo transaction + order + order queue + 10-level quote.
echo QMT remains open for L1 ticks and +60s price labels.
echo Subscription failures retry in background, so proxy startup order no longer matters.
echo.
echo SECURITY: txtool.exe was downloaded from the provider's public GitHub repo.
echo This installer NEVER auto-runs the third-party binary and NEVER disables antivirus.
echo.
start "" notepad.exe "%CONF%\proxy.toml"
pause
exit /b 0

:fail
echo.
echo [ERROR] Preparation failed. Existing QMT cloud/forward processes were not intentionally changed.
pause
exit /b 1
