@echo off
setlocal
cd /d "%~dp0"
set "GAMMAKEEPER_PYTHON=C:\Users\nelso\.venvs\codex-science-v1\Scripts\pythonw.exe"
if not exist "%GAMMAKEEPER_PYTHON%" (
  echo The configured science Python is missing. See README.md.
  pause
  exit /b 1
)
start "GammaKeeper" "%GAMMAKEEPER_PYTHON%" -X utf8 "%~dp0gui.py"
