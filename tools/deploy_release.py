"""Install a tagged release side by side under ``Radiacode Data\\GammaKeeper Releases`` (plan section 3.9).

Not %LOCALAPPDATA%: a process started inside a packaged (MSIX) app, such as the
Claude desktop app, has new AppData folders redirected into that package's
private LocalCache, where launchers started from Explorer cannot see them.
Override the location with --releases or GAMMAKEEPER_RELEASES.

Steps: export the tag with ``git archive``; check every exported file against
its git blob (SHA-256); optionally compare with an existing release folder;
vendor dependencies from the lock file; run the unit tests in a throwaway copy
so test output never lands in the release; write DEPLOYMENT.json; move the
release into place. Releases are never replaced: a running recorder may be
using an older one. ``current.txt`` is written only with --activate, and only
when the tests ran and passed.

Usage:
    python -X utf8 tools/deploy_release.py TAG [--compare-folder DIR] [--activate]
    python -X utf8 tools/deploy_release.py TAG --skip-tests "reason recorded in DEPLOYMENT.json"
"""
import argparse
import datetime as dt
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_RELEASES = Path(os.environ.get('GAMMAKEEPER_RELEASES', REPO.parent / 'GammaKeeper Releases'))
SCIENCE_PYTHON = Path(os.environ.get('GAMMAKEEPER_SCIENCE_PYTHON') or os.environ.get('RADIALOG_SCIENCE_PYTHON')
                      or r'C:\Users\nelso\.venvs\codex-science-v1\Scripts\python.exe')


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def git(*args):
    return subprocess.run(['git', '-C', str(REPO), *args], check=True, capture_output=True).stdout


def tree(ref):
    rows = git('ls-tree', '-r', '-z', ref).decode('utf-8').split('\0')
    return {row.split('\t', 1)[1]: row.split()[2] for row in rows if row}


def export(ref, staging):
    archive = git('archive', '--format=tar', ref)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(staging, filter='data')
    blobs = tree(ref)
    for path, blob in blobs.items():
        if sha256((staging / path).read_bytes()) != sha256(git('cat-file', 'blob', blob)):
            raise SystemExit(f'Exported file differs from its git blob: {path}')
    return blobs


def compare_folder(staging, blobs, folder):
    differing = [p for p in blobs if not (folder / p).is_file()
                 or sha256((folder / p).read_bytes()) != sha256((staging / p).read_bytes())]
    if differing:
        raise SystemExit(f'{len(differing)} files differ from {folder}: {differing[:10]}')
    return dict(folder=str(folder), files_identical=len(blobs))


def vendor(staging, python):
    lock = 'dependencies-lock-v01.txt'
    if sha256((staging / lock).read_bytes()) != sha256((REPO / lock).read_bytes()):
        raise SystemExit(f'{lock} in the release differs from the repository; vendor it with its own tooling')
    subprocess.run([str(python), '-X', 'utf8', str(REPO / 'tools' / 'vendor_dependencies.py'),
                    '--target', str(staging / 'dependencies'), '--python', str(python)], check=True)
    listing = (REPO / 'dependencies-lock-v01.sha256').read_bytes()
    return dict(lock_sha256=sha256((staging / lock).read_bytes()), reference_sha256=sha256(listing))


def run_tests(staging, python):
    with tempfile.TemporaryDirectory(prefix='gammakeeper_release_tests_') as scratch:
        copy = Path(scratch) / 'release'
        shutil.copytree(staging, copy)
        env = {**os.environ, 'QT_QPA_PLATFORM': 'offscreen', 'GAMMAKEEPER_SCIENCE_PYTHON': str(python)}
        result = subprocess.run([str(python), '-X', 'utf8', '-m', 'unittest', 'discover', '-s', 'tests'],
                                cwd=copy, env=env, capture_output=True, text=True, encoding='utf-8', errors='replace')
    summary = [line for line in result.stderr.splitlines() if line.startswith(('Ran ', 'OK', 'FAILED'))]
    return dict(passed=result.returncode == 0, summary=summary, tail=result.stderr.splitlines()[-30:])


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('tag')
    parser.add_argument('--releases', type=Path, default=DEFAULT_RELEASES)
    parser.add_argument('--python', type=Path, default=SCIENCE_PYTHON)
    parser.add_argument('--compare-folder', type=Path)
    parser.add_argument('--skip-tests', metavar='REASON')
    parser.add_argument('--activate', action='store_true', help='point releases/current.txt at this tag')
    args = parser.parse_args()
    if args.activate and args.skip_tests:
        raise SystemExit('--activate requires the tests to run and pass')
    commit = git('rev-parse', f'{args.tag}^{{commit}}').decode().strip()
    dest = args.releases / args.tag
    staging = args.releases / (args.tag + '.partial')
    if dest.exists():
        raise SystemExit(f'{dest} already exists; releases are never replaced')
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    blobs = export(args.tag, staging)
    record = dict(tag=args.tag, commit=commit, created_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
                  source_repository=str(REPO), files=len(blobs),
                  file_sha256={p: sha256((staging / p).read_bytes()) for p in sorted(blobs)})
    if args.compare_folder:
        record['compare_folder'] = compare_folder(staging, blobs, args.compare_folder.resolve())
    record['dependencies'] = vendor(staging, args.python)
    if args.skip_tests:
        record['tests'] = dict(skipped=True, reason=args.skip_tests)
    else:
        record['tests'] = run_tests(staging, args.python)
        if not record['tests']['passed']:
            print('\n'.join(record['tests']['tail']))
            raise SystemExit(f'Tests failed; release left unactivated at {staging}')
    with open(staging / 'DEPLOYMENT.json', 'x', encoding='utf-8') as out:
        json.dump(record, out, indent=2)
    staging.rename(dest)
    print(f'installed {args.tag} ({commit[:10]}) at {dest}')
    if args.activate:
        current = args.releases / 'current.txt'
        temporary = current.with_suffix('.tmp')
        temporary.write_text(args.tag + '\n', encoding='ascii')
        os.replace(temporary, current)
        print(f'activated: {current} -> {args.tag}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
