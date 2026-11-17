@echo off
powershell.exe -NoLogo -NoProfile -File "%~dp0Launch-Windows.ps1" %*
exit /b %errorlevel%
