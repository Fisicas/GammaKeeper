"""Create or remove the GammaKeeper Desktop and Start Menu shortcuts for this Windows user.

The shortcuts run the stable launcher GammaKeeper.pyw in the releases folder with pythonw.exe,
so activating another release never requires new shortcuts. Only the two GammaKeeper.lnk
files are created or removed; releases and run data are never touched.

Run it through tools/install_gammakeeper.cmd from Explorer: a process started inside a packaged
(MSIX) app, such as the Claude desktop app, can have its writes to AppData (where the Start
Menu lives) redirected to that app's private storage.

Usage:
    python -X utf8 tools/shortcuts.py install|uninstall [--desktop DIR] [--programs DIR]
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_RELEASES = Path(os.environ.get('GAMMAKEEPER_RELEASES', REPO.parent / 'GammaKeeper Releases'))
NAME = 'GammaKeeper.lnk'


def powershell(script):
    result = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                             '-Command', script], capture_output=True, text=True)
    if result.returncode:
        raise SystemExit(f'PowerShell failed: {result.stderr.strip()}')
    return result.stdout.strip()


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def user_folders():
    out = powershell("[Environment]::GetFolderPath('Desktop'); [Environment]::GetFolderPath('Programs')")
    desktop, programs = out.splitlines()
    return Path(desktop), Path(programs)


def create(link, target, arguments, icon, workdir):
    powershell('$s = (New-Object -ComObject WScript.Shell).CreateShortcut(' + quote(link) + '); '
               f'$s.TargetPath = {quote(target)}; $s.Arguments = {quote(arguments)}; '
               f'$s.IconLocation = {quote(str(icon) + ",0")}; $s.WorkingDirectory = {quote(workdir)}; '
               "$s.Description = 'GammaKeeper: time-resolved gamma spectra logger'; $s.Save()")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('action', choices=['install', 'uninstall'])
    parser.add_argument('--releases', type=Path, default=DEFAULT_RELEASES)
    parser.add_argument('--python', type=Path, default=Path(os.environ.get('GAMMAKEEPER_SCIENCE_PYTHON')
                        or sys.executable))
    parser.add_argument('--desktop', type=Path, help='override the Desktop folder (testing)')
    parser.add_argument('--programs', type=Path, help='override the Start Menu Programs folder (testing)')
    args = parser.parse_args()
    if args.desktop and args.programs:
        folders = [args.desktop, args.programs]
    else:
        desktop, programs = user_folders()
        folders = [args.desktop or desktop, args.programs or programs]
    links = [folder / NAME for folder in folders]
    if args.action == 'uninstall':
        for link in links:
            if link.exists():
                link.unlink()
                print('removed', link)
        return 0
    releases = args.releases.resolve()
    launcher, icon = releases / 'GammaKeeper.pyw', releases / 'gammakeeper.ico'
    pythonw = args.python.with_name('pythonw.exe')
    for required in (launcher, icon, pythonw):
        if not required.is_file():
            raise SystemExit(f'Missing {required}; install and activate a release first (tools/install_gammakeeper.cmd)')
    for link in links:
        link.parent.mkdir(parents=True, exist_ok=True)
        create(link, pythonw, f'"{launcher}"', icon, releases)
        print('created', link)
    return 0


if __name__ == '__main__':
    sys.exit(main())
