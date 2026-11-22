@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Stop-ClipStudio.ps1" -Port 8766
pause
