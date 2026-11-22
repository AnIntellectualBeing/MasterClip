@echo off
rem Opens Clip Studio in its own window. No console stays open.
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" "%~dp0desktop.py"
) else (
  start "" pythonw "%~dp0desktop.py"
)
