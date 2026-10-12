"""Rebuild the app-local ``app/dependencies`` folder from the lock file (plan D22).

Installs with ``pip install --target`` into a new folder, so the interpreter's
own environment is never modified. The result is compared with the committed
reference hashes in ``dependencies-lock-v01.sha256`` (taken from the v0.5.1
folder that recorded the RadiaLog runs). Package code must match exactly;
differences limited to pip's install metadata are reported but tolerated.

Usage:
    python -X utf8 tools/vendor_dependencies.py [--target DIR] [--python EXE]
    python -X utf8 tools/vendor_dependencies.py --write-reference EXISTING_DEPENDENCIES_DIR
"""
import argparse
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'app'
LOCK = APP / 'dependencies-lock-v01.txt'
REFERENCE = APP / 'dependencies-lock-v01.sha256'
METADATA = {'RECORD', 'INSTALLER', 'REQUESTED', 'direct_url.json'}


def digests(folder):
    out = {}
    for path in sorted(folder.rglob('*')):
        rel = path.relative_to(folder)
        if path.is_file() and '__pycache__' not in rel.parts and path.suffix != '.pyc':
            out[rel.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def write_listing(listing, file):
    file.write_text(''.join(f'{d}  {p}\n' for p, d in sorted(listing.items())), encoding='utf-8', newline='\n')


def read_listing(file):
    rows = (line.split('  ', 1) for line in file.read_text(encoding='utf-8').splitlines() if line.strip())
    return {path: digest for digest, path in rows}


def compare(built, reference):
    code, meta = [], []
    for path in sorted(set(built) | set(reference)):
        if built.get(path) != reference.get(path):
            (meta if Path(path).name in METADATA else code).append(path)
    return code, meta


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--target', type=Path, default=APP / 'dependencies')
    parser.add_argument('--python', default=sys.executable)
    parser.add_argument('--write-reference', type=Path, metavar='DIR',
                        help='record DIR as the reference hash list instead of installing')
    args = parser.parse_args()
    if args.write_reference:
        write_listing(digests(args.write_reference), REFERENCE)
        print(f'reference written: {REFERENCE} ({len(read_listing(REFERENCE))} files)')
        return 0
    target = args.target.resolve()
    if target.exists() and any(target.iterdir()):
        sys.exit(f'{target} exists and is not empty; remove it or choose another --target')
    staging = target.with_name(target.name + '.partial')
    if staging.exists():
        shutil.rmtree(staging)
    command = [args.python, '-m', 'pip', 'install', '--target', str(staging), '--no-deps', '--no-compile',
               '--disable-pip-version-check', '--no-input', '-r', str(LOCK)]
    print('running:', ' '.join(command))
    subprocess.run(command, check=True)
    built = digests(staging)
    code, meta = compare(built, read_listing(REFERENCE))
    for path in meta:
        print('metadata differs (tolerated):', path)
    if code:
        for path in code:
            print('PACKAGE FILE DIFFERS:', path)
        sys.exit(f'{len(code)} package files differ from {REFERENCE.name}; staging kept at {staging}')
    if target.exists():
        target.rmdir()
    staging.rename(target)
    print(f'dependencies ready: {target} ({len(built)} files; {len(meta)} metadata differences)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
