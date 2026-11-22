@echo off
cd /d "%~dp0"
python -m venv .venv
if errorlevel 1 goto error
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto error
where ffmpeg >nul 2>&1
if errorlevel 1 (
  echo FFmpeg is required. Install it and add ffmpeg and ffprobe to PATH.
  echo On Windows you can use: winget install Gyan.FFmpeg
)
echo Setup finished. Run Start-ClipStudio.cmd
pause
exit /b 0
:error
echo Setup failed. Check the error above. Python 3.11 or newer is recommended.
pause
exit /b 1
