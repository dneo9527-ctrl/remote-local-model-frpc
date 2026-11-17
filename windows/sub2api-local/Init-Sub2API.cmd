@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Init-Sub2API.ps1"
if errorlevel 1 pause
