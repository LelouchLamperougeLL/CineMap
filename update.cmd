@echo off
rem Double-click to bring the map up to date with files.csv.
rem First run: sets up Python's libraries (a few minutes). Afterwards only new films are fetched.
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Setting up Python for this folder, one time only ...
  py -3 -m venv .venv >nul 2>nul || python -m venv .venv
  if not exist ".venv\Scripts\python.exe" (
    echo.
    echo Python was not found. Install Python 3.12 from https://www.python.org/downloads/
    echo ^(tick "Add python.exe to PATH"^), then double-click update.cmd again.
    pause
    exit /b 1
  )
)

".venv\Scripts\python.exe" -c "import numpy, sklearn, umap" >nul 2>nul
if errorlevel 1 (
  echo Installing the libraries the map needs, one time only ...
  ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --timeout 30 --retries 10 -r requirements.txt
  if errorlevel 1 (
    echo.
    echo Installing failed. Check the internet connection and double-click update.cmd again.
    pause
    exit /b 1
  )
)

".venv\Scripts\python.exe" -m moviemap update %*
set CODE=%ERRORLEVEL%
echo.
pause
exit /b %CODE%
