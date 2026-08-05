@echo off
chcp 65001 >nul
cd /d "%~dp0"

for /f "delims=" %%I in ('py -c "import sys; print(sys.executable)"') do set PYEXE=%%I
set PYWEXE=%PYEXE:python.exe=pythonw.exe%

if not exist "%PYWEXE%" (
  echo [ERROR] pythonw.exe not found: %PYWEXE%
  pause
  exit /b 1
)

if not exist ".streamlit\secrets.toml" (
  echo [ERROR] Missing .streamlit\secrets.toml
  echo Copy .streamlit\secrets.toml.example to secrets.toml and fill in Supabase values first.
  pause
  exit /b 1
)

schtasks /Create /F /SC ONLOGON /TN "AStockQMTCloudBridge" /TR "\"%PYWEXE%\" \"%~dp0services\qmt_cloud_bridge.py\"" /RL LIMITED
if errorlevel 1 (
  echo [ERROR] Could not create the Windows startup task.
  pause
  exit /b 1
)

echo Startup task installed successfully.
echo It will start automatically after Windows login and retry until QMT is available.
start "" "%PYWEXE%" "%~dp0services\qmt_cloud_bridge.py"
pause
