@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

set "PYEXE="
for /f "delims=" %%I in ('py -c "import sys; print(sys.executable)" 2^>nul') do set "PYEXE=%%I"
if not defined PYEXE for /f "delims=" %%I in ('python -c "import sys; print(sys.executable)" 2^>nul') do set "PYEXE=%%I"
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PYEXE (
  echo [FAIL] Python not found.
  pause
  exit /b 1
)

set "PYTHONPATH=%~dp0;%PYTHONPATH%"
echo ========================================
echo A-Stock QMT V18 Final Health Check
echo ========================================
"%PYEXE%" "%~dp0services\v18_selftest.py"
if errorlevel 1 (
  echo.
  echo RESULT: SELFTEST FAILED
  pause
  exit /b 2
)
echo.
"%PYEXE%" "%~dp0services\v18_doctor.py"
echo.
pause
endlocal
