@echo off
rem Double-click to open the review page for flagged or doubtful matches.
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run update.cmd first: it sets everything up.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m moviemap review %*
echo.
pause
