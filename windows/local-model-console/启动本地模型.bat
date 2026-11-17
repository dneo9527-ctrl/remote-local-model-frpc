@echo off
chcp 65001 >nul
setlocal
set "APP=%~dp0LocalAI-Control.exe"
if exist "%APP%" goto native
set "APP=%~dp0tools\control-panel\LocalAI-Control.exe"
if exist "%APP%" goto native
set "APP=%USERPROFILE%\Documents\LocalProjects\projects\qwen3-remote\tools\control-panel\LocalAI-Control.exe"
if exist "%APP%" goto native
set "GUI=%~dp0LocalAI-GUI.ps1"
if exist "%GUI%" goto launch
set "GUI=%~dp0scripts\LocalAI-GUI.ps1"
if exist "%GUI%" goto launch
set "GUI=%USERPROFILE%\Documents\LocalProjects\projects\qwen3-remote\scripts\LocalAI-GUI.ps1"
if exist "%GUI%" goto launch
echo 未找到 LocalAI-GUI.ps1。请将两个文件放在一起，或把脚本放进项目 scripts 目录。
pause
exit /b 1
:launch
start "" "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -STA -WindowStyle Hidden -ExecutionPolicy Bypass -File "%GUI%"
exit /b
:native
start "" "%APP%"
exit /b
