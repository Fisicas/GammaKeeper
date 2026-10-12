@echo off
setlocal
rem Remove the GammaKeeper Desktop and Start Menu shortcuts. Installed releases
rem (Radiacode Data\GammaKeeper Releases) and run data are left untouched.
rem Interpreter: GAMMAKEEPER_SCIENCE_PYTHON, else the per-clone "git config gammakeeper.python", else python on PATH.
set "PY=%GAMMAKEEPER_SCIENCE_PYTHON%"
if not defined PY for /f "usebackq delims=" %%p in (`git -C "%~dp0.." config --get gammakeeper.python`) do set "PY=%%p"
if not defined PY set "PY=python"
cd /d "%~dp0.."
"%PY%" -X utf8 tools\shortcuts.py uninstall
echo Releases and run data were not changed. Delete old release folders yourself if you no longer need them.
pause
