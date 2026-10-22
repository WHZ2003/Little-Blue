@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-cloud.ps1"
if errorlevel 1 pause
