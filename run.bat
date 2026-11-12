@echo off
rem Co-op Coach for Windows: double-click this file.
rem First run: makes a private Python environment in this folder and installs what the app needs (a few minutes).
rem After that it starts in seconds and opens the app in your browser. Close this window to stop the app.
setlocal
cd /d "%~dp0"
title Co-op Coach
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

set "PY="
for %%V in (3.13 3.12 3.11 3.14) do (
  if not defined PY (
    py -%%V -c "import sys" >nul 2>nul && set "PY=py -%%V"
  )
)
if not defined PY (
  python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo.
  echo   Co-op Coach needs Python 3.13.
  echo   1. Go to https://www.python.org/downloads/windows/
  echo   2. Under "Stable Releases", find the newest Python 3.13 and click "Download Windows installer 64-bit".
  echo   3. On the first screen, TICK "Add python.exe to PATH", then click "Install Now".
  echo   4. Double-click run.bat again.
  echo.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo.
  echo   Setting up Co-op Coach for the first time. This takes 3 to 10 minutes. Please wait...
  echo.
  %PY% -m venv .venv
  if errorlevel 1 goto :fail
)
echo   Checking what Co-op Coach needs...
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet --disable-pip-version-check
".venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet --disable-pip-version-check
if errorlevel 1 goto :fail
if not exist ".env" copy ".env.example" ".env" >nul

echo.
if /i "%~1"=="demo" (
  echo   Starting the demo. Your browser opens by itself in a few seconds.
  ".venv\Scripts\python.exe" -m coopcoach demo --open
) else (
  echo   Starting Co-op Coach. Your browser opens by itself in a few seconds.
  echo   Keep this window open while you use it. Close it to stop the app.
  ".venv\Scripts\python.exe" -m coopcoach serve --open
)
goto :eof

:fail
echo.
echo   Something went wrong while setting up. Take a photo of this window
echo   and send it to whoever gave you Co-op Coach.
echo.
pause
exit /b 1
