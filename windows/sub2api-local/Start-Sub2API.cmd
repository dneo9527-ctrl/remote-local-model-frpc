@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-Sub2API.ps1" -NoBrowser
if errorlevel 1 pause
