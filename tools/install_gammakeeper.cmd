@echo off
setlocal
rem Install or update GammaKeeper for this Windows user, outside the repository.
rem Double-click from Explorer. Optional argument: a tag (v0.5.2) or branch (default origin/main).
rem It exports a clean copy into Radiacode Data\GammaKeeper Releases, rebuilds dependencies, runs the
rem test suite, activates the release and creates Desktop and Start Menu shortcuts. The repository's
rem working files are only read; earlier releases stay installed side by side.
rem Interpreter: GAMMAKEEPER_SCIENCE_PYTHON, else the per-clone "git config gammakeeper.python", else python on PATH.
set "PY=%GAMMAKEEPER_SCIENCE_PYTHON%"
if not defined PY for /f "usebackq delims=" %%p in (`git -C "%~dp0.." config --get gammakeeper.python`) do set "PY=%%p"
if not defined PY set "PY=python"
"%PY%" -c "import PySide6, numpy, matplotlib" 2>nul
if errorlevel 1 (
  echo Python with PySide6, NumPy and Matplotlib not found ^(%PY%^).
  echo Set it once per clone:  git config gammakeeper.python "C:\path\to\python.exe"
  pause
  exit /b 1
)
set "REF=%~1"
if "%REF%"=="" set "REF=origin/main"
cd /d "%~dp0.."
echo Fetching the latest commits from GitHub...
git fetch --quiet origin || echo Could not fetch; installing the local copy of %REF%.
echo.
echo Installing %REF%: clean export, dependencies, full test suite. This takes about two minutes...
"%PY%" -X utf8 tools\deploy_release.py %REF% --activate
if errorlevel 1 (
  echo.
  echo Installation failed; the previously active release is unchanged.
  pause
  exit /b 1
)
"%PY%" -X utf8 tools\shortcuts.py install
if errorlevel 1 (
  echo Shortcut creation failed; start GammaKeeper Releases\GammaKeeper.pyw directly.
  pause
  exit /b 1
)
echo.
echo GammaKeeper is installed. Start it from the Desktop or Start Menu shortcut.
echo New runs are saved under Radiacode Data\GammaKeeper Runs unless you choose another folder.
pause
