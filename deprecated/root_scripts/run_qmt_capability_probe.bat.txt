@echo off
chcp 65001 >nul
cd /d "%~dp0"
set /p SYMBOL=Stock symbol [default 000400.SZ]: 
if "%SYMBOL%"=="" set "SYMBOL=000400.SZ"
echo.
echo Testing QMT permissions for %SYMBOL% ...
py .\services\qmt_capability_probe.py %SYMBOL%
echo.
echo If l2order and l2transaction show OK during trading hours,
echo the system can move from five-level estimates to real order-flow signals.
pause
