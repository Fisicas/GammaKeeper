r"""Create the GammaKeeper repository and import RadiaLog v0.1-v0.5.1 (plan Step 0, steps 2-4).

One commit per version folder, whose tree mirrors that folder's selected
source exactly. Every committed blob is checked byte-for-byte (SHA-256)
against the folder, and against the release manifest where one exists.
Any mismatch stops the import. Version folders are only read.
"""
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

WORKSPACE = Path(r'C:\Users\nelso\Desktop\Radiacode Data')
REPO = WORKSPACE / 'GammaKeeper'
VERSIONS = [('v01', '0.1.0'), ('v02', '0.2.0'), ('v03', '0.3.0'), ('v031', '0.3.1'), ('v04', '0.4.0'),
            ('v041', '0.4.1'), ('v042', '0.4.2'), ('v05', '0.5.0'), ('v051', '0.5.1')]
NAME, EMAIL = 'Michael R. Nelson', '18431493+Fisicas@users.noreply.github.com'
EXCLUDED_DIRS = {'runs', 'dependencies', 'logs', 'cache', '__pycache__', '.venv'}
INCLUDED_DIRS = {'tests', 'THIRD_PARTY_LICENSES', 'validation'}
TRAILER = 'Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>'


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def git(*args, env=None, input=None):
    result = subprocess.run(['git', '-C', str(REPO), *args], capture_output=True, input=input,
                            env={**os.environ, **(env or {})})
    if result.returncode:
        sys.exit(f'git {" ".join(args)} failed: {result.stderr.decode(errors="replace")}')
    return result.stdout


def excluded_file(rel):
    name = rel.name
    return ('.sqlite' in name or name.endswith(('.pyc', '.partial'))
            or (len(rel.parts) == 1 and name.startswith('export_') and name.endswith('.log')))


def select(folder):
    chosen, skipped = {}, {}
    for top in sorted(folder.iterdir()):
        if top.is_dir():
            if top.name in EXCLUDED_DIRS:
                skipped[top.name + '/'] = 'excluded directory'
                continue
            if top.name not in INCLUDED_DIRS:
                sys.exit(f'Unknown directory {top}; extend the rules before importing')
            for item in sorted(top.rglob('*')):
                rel = item.relative_to(folder)
                if item.is_dir():
                    if top.name == 'validation' and len(rel.parts) == 2:
                        skipped[rel.as_posix() + '/'] = 'validation subfolder (run data / temp)'
                    continue
                if '__pycache__' in rel.parts or excluded_file(rel):
                    continue
                if top.name == 'validation' and len(rel.parts) > 2:
                    continue
                chosen[rel.as_posix()] = item
        else:
            rel = top.relative_to(folder)
            if excluded_file(rel):
                skipped[rel.as_posix()] = 'excluded file'
            else:
                chosen[rel.as_posix()] = top
    return chosen, skipped


def readme_heading(folder):
    for line in (folder / 'README.md').read_text(encoding='utf-8').splitlines():
        if line.startswith('#'):
            return line.lstrip('#').strip()
    return ''


def manifest_check(folder, chosen):
    manifests = sorted((folder / 'validation').glob('release_manifest_v*.json'))
    if not manifests:
        return dict(status='no release manifest')
    data = json.loads(manifests[-1].read_text(encoding='utf-8'))
    listed = data.get('source_sha256')
    if not isinstance(listed, dict):
        return dict(status='manifest has no per-file hashes', manifest=manifests[-1].name)
    listed = {n.replace('\\', '/'): d for n, d in listed.items()}
    bad = [n for n, d in listed.items() if n not in chosen or sha256(chosen[n].read_bytes()) != d]
    if bad:
        sys.exit(f'{folder.name}: release manifest mismatch {bad}')
    return dict(status='matched', manifest=manifests[-1].name, entries=len(listed))


def clear_worktree():
    for item in REPO.iterdir():
        if item.name == '.git':
            continue
        shutil.rmtree(item) if item.is_dir() else item.unlink()


def verify_commit(chosen):
    tree = git('ls-tree', '-r', '-z', 'HEAD').decode('utf-8').split('\0')
    blobs = {}
    for row in filter(None, tree):
        meta, path = row.split('\t', 1)
        blobs[path] = meta.split()[2]
    if set(blobs) != set(chosen):
        sys.exit(f'Tree/file-set mismatch: {sorted(set(blobs) ^ set(chosen))}')
    for path, blob in blobs.items():
        if sha256(git('cat-file', 'blob', blob)) != sha256(chosen[path].read_bytes()):
            sys.exit(f'Blob mismatch for {path}')
    return len(blobs)


def main():
    if REPO.exists():
        sys.exit(f'{REPO} already exists; refusing to import over it')
    REPO.mkdir()
    git('init', '-q', '-b', 'main')
    for key, value in [('user.name', NAME), ('user.email', EMAIL), ('core.autocrlf', 'false'),
                       ('core.longpaths', 'true')]:
        git('config', '--local', key, value)
    (REPO / '.git' / 'info').mkdir(exist_ok=True)
    (REPO / '.git' / 'info' / 'attributes').write_text('* -text\n', encoding='ascii')
    record = dict(created_utc=dt.datetime.now(dt.timezone.utc).isoformat(), repository=str(REPO),
                  rules=dict(excluded_directories=sorted(EXCLUDED_DIRS),
                             excluded_files=['*.sqlite*', '*.pyc', '*.partial', 'root export_*.log'],
                             validation='top-level files only; subfolders are run data or temp output'),
                  versions=[])
    for suffix, version in VERSIONS:
        folder = WORKSPACE / f'radiacode_logger_{suffix}'
        chosen, skipped = select(folder)
        check = manifest_check(folder, chosen)
        clear_worktree()
        for rel, src in chosen.items():
            dst = REPO / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        latest = max(p.stat().st_mtime for p in chosen.values())
        date = dt.datetime.fromtimestamp(latest).astimezone().replace(microsecond=0).isoformat()
        git('add', '-A')
        message = (f'Import RadiaLog {version} from {folder.name}\n\n'
                   f'Source folder: {folder.name} (left unchanged in the workspace as the archive)\n'
                   f'README heading: {readme_heading(folder)}\n'
                   f'Files: {len(chosen)}; latest source mtime: {date}\n'
                   f'Release manifest: {check["status"]}\n'
                   'Not imported: runs/, dependencies/, logs/, cache/, __pycache__/, .venv/, validation subfolders, '
                   '*.sqlite*, root export_*.log\n\n' + TRAILER + '\n')
        env = dict(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
        git('commit', '-q', '--allow-empty-message', '-F', '-', env=env, input=message.encode('utf-8'))
        count = verify_commit(chosen)
        tag = f'v{version}'
        git('tag', '-a', tag, '-m', f'RadiaLog {version} (imported from {folder.name})', env=env)
        commit = git('rev-parse', 'HEAD').decode().strip()
        record['versions'].append(dict(
            tag=tag, folder=folder.name, commit=commit, date=date, files_verified=count, release_manifest=check,
            not_imported=skipped,
            files={rel: dict(sha256=sha256(src.read_bytes()), bytes=src.stat().st_size) for rel, src in chosen.items()}))
        print(f'{tag}: {count} files verified, {date}, {commit[:10]}')
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('import_manifest.json')
    with open(out, 'x', encoding='utf-8') as fh:
        json.dump(record, fh, indent=2)
    print('manifest written to', out)


if __name__ == '__main__':
    main()
