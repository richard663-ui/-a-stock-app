@echo off
setlocal
cd /d "%~dp0"

echo ==========================================
echo AStock 60-second validation report
echo ==========================================
echo.

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 services\research_validation_report.py --horizon 60
) else (
    python services\research_validation_report.py --horizon 60
)

if not %errorlevel%==0 (
    echo.
    echo Validation report failed.
    echo Make sure Python 3 is available and AStockData contains recorder samples.
    pause
    exit /b 1
)

echo.
echo Report completed.
pause
