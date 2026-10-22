@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul

set "INSTALLDIR=%LOCALAPPDATA%\AStockQMT"
set "STATUS=%USERPROFILE%\AStockData\recorder_status.json"
set "LOGFILE=%INSTALLDIR%\runtime\research_recorder.log"
set "RECORDER=%INSTALLDIR%\services\qmt_research_recorder.py"
set "MODEL=%INSTALLDIR%\modules\research_forward_model.py"
set "TODAY=%DATE:~0,4%-%DATE:~5,2%-%DATE:~8,2%"
set "DB=%USERPROFILE%\AStockData\raw\%TODAY%\ticks.sqlite3"

set "PYEXE="
for /f "delims=" %%I in ('py -c "import sys; print(sys.executable)" 2^>nul') do set "PYEXE=%%I"
if not defined PYEXE for /f "delims=" %%I in ('python -c "import sys; print(sys.executable)" 2^>nul') do set "PYEXE=%%I"
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"

echo ========================================
echo A-Stock Research Recorder Check V2
echo ========================================

if not exist "%RECORDER%" (
  echo [FAIL] Recorder is not installed.
  pause
  exit /b 1
)
if not exist "%MODEL%" (
  echo [FAIL] V2 research model is missing.
  echo Run update_research_recorder_v2.bat.
  pause
  exit /b 1
)
echo [PASS] Recorder V2 files exist.

if exist "%STATUS%" (
  echo.
  echo Current status:
  type "%STATUS%"
) else (
  echo [WARN] Status file does not exist yet.
)

if defined PYEXE if exist "%DB%" (
  echo.
  echo Today's local forward-evaluation summary:
  "%PYEXE%" -c "import sqlite3; p=r'%DB%'; c=sqlite3.connect(p); print('60s rows:',c.execute('select count(*) from forward_eval where horizon_seconds=60').fetchone()[0]); print('120s rows:',c.execute('select count(*) from forward_eval where horizon_seconds=120').fetchone()[0]); print('5s traces:',c.execute('select count(*) from score_trace').fetchone()[0]); c.close()"
)

echo.
if exist "%LOGFILE%" if defined PYEXE (
  echo Last recorder log lines:
  echo ----------------------------------------
  "%PYEXE%" -c "from pathlib import Path; p=Path(r'%LOGFILE%'); print(''.join(p.read_text(encoding='utf-8',errors='replace').splitlines(True)[-20:]), end='')"
  echo ----------------------------------------
)

echo.
echo Data folder: %USERPROFILE%\AStockData\raw
pause
endlocal
