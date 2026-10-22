@echo off
rem Install a pinned, reviewed commit of the background runtime.
rem Usage: INSTALL_RUNTIME.cmd -Commit <40-character commit id> [-Source <local git clone>]
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy\install_runtime.ps1" %*
