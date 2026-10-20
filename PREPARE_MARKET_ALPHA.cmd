@echo off
setlocal
set "PYTHONPATH=%LOCALAPPDATA%\AStockQMT;%LOCALAPPDATA%\AStockQMT\vendor;%~dp0;%PYTHONPATH%"
"%LOCALAPPDATA%\Programs\Python\Python312\python.exe" "%~dp0services\market_alpha_data.py" --download --max-seconds 600
if errorlevel 1 (
  echo Daily data coverage is incomplete. Caches are preserved. Re-run to resume.
  pause
  exit /b 2
)
"%LOCALAPPDATA%\Programs\Python\Python312\python.exe" "%~dp0services\market_alpha_research.py"
pause
