@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul

set "INSTALLDIR=%LOCALAPPDATA%\AStockQMT"
set "STATUS=%USERPROFILE%\AStockData\recorder_status.json"
set "LOGFILE=%INSTALLDIR%\runtime\research_recorder.log"
set "RECORDER=%INSTALLDIR%\services\qmt_research_recorder.py"

echo ========================================
echo A-Stock Research Recorder Check
echo ========================================

if not exist "%RECORDER%" (
  echo [FAIL] Recorder is not installed.
  echo Run install_research_recorder.bat first.
  pause
  exit /b 1
)

echo [PASS] Recorder file exists.

if exist "%STATUS%" (
  echo [PASS] Status file exists:
  echo        %STATUS%
  type "%STATUS%"
) else (
  echo [WARN] Status file does not exist yet.
  echo Keep QMT open and logged in for 10-20 seconds.
)

echo.
if exist "%LOGFILE%" (
  echo Last recorder log lines:
  echo ----------------------------------------
  powershell -NoProfile -Command "Get-Content -LiteralPath '%LOGFILE%' -Tail 20" 2>nul
  echo ----------------------------------------
) else (
  echo [WARN] Recorder log does not exist yet.
)

echo.
echo Data folder: %USERPROFILE%\AStockData\raw
pause
endlocal
