@echo off
setlocal
rem Remove the GammaKeeper Desktop and Start Menu shortcuts. Installed releases
rem (Radiacode Data\GammaKeeper Releases) and run data are left untouched.
set "PY=C:\Users\nelso\.venvs\codex-science-v1\Scripts\python.exe"
if defined GAMMAKEEPER_SCIENCE_PYTHON set "PY=%GAMMAKEEPER_SCIENCE_PYTHON%"
cd /d "%~dp0.."
"%PY%" -X utf8 tools\shortcuts.py uninstall
echo Releases and run data were not changed. Delete old release folders yourself if you no longer need them.
pause
