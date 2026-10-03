@echo off
rem Check the router setup for the map on Windows: log in and try every call the service makes.
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"

set "PY="
py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY (
  python --version >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo Python 3 не найден. Установите его с https://www.python.org/downloads/ ^(галочка "Add python.exe to PATH"^)
  echo или командой: winget install Python.Python.3.12
  pause
  exit /b 1
)

%PY% check.py
echo.
pause
