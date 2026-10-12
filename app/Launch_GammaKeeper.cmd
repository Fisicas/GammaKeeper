@echo off
setlocal
cd /d "%~dp0"
rem Interpreter: GAMMAKEEPER_SCIENCE_PYTHON, else the per-clone "git config gammakeeper.python", else pythonw on PATH.
set "GAMMAKEEPER_PYTHON=%GAMMAKEEPER_SCIENCE_PYTHON%"
if not defined GAMMAKEEPER_PYTHON for /f "usebackq delims=" %%p in (`git -C "%~dp0.." config --get gammakeeper.python 2^>nul`) do set "GAMMAKEEPER_PYTHON=%%p"
if defined GAMMAKEEPER_PYTHON set "GAMMAKEEPER_PYTHON=%GAMMAKEEPER_PYTHON:python.exe=pythonw.exe%"
if not defined GAMMAKEEPER_PYTHON set "GAMMAKEEPER_PYTHON=pythonw.exe"
if not "%GAMMAKEEPER_PYTHON%"=="pythonw.exe" if not exist "%GAMMAKEEPER_PYTHON%" (
  echo The configured science Python is missing. See docs\manual.md.
  pause
  exit /b 1
)
start "GammaKeeper" "%GAMMAKEEPER_PYTHON%" -X utf8 "%~dp0gui.py"
