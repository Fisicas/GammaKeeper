@echo off
setlocal
cd /d "%~dp0"
set "RADIALOG_PYTHON=C:\Users\nelso\.venvs\codex-science-v1\Scripts\pythonw.exe"
if not exist "%RADIALOG_PYTHON%" (
  echo The configured science Python is missing. See README.md.
  pause
  exit /b 1
)
start "RadiaLog" "%RADIALOG_PYTHON%" -X utf8 "%~dp0gui.py"
