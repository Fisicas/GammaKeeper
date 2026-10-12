"""Exact acquisition/analysis source identity, including installations without Git."""
import hashlib
import json
from pathlib import Path
import subprocess
from functools import lru_cache


@lru_cache(maxsize=1)
def identity():
    root = Path(__file__).resolve().parent
    files = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(root.glob('*.py'))}
    files.update({p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in sorted((root/'ui').glob('*.py'))})
    result = dict(source_sha256=hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
                  source_files=files, commit=None, dirty=None, deployment_sha256=None)
    deployment = root.parent / 'DEPLOYMENT.json'
    if deployment.exists():
        raw = deployment.read_bytes()
        record = json.loads(raw)
        expected=record.get('file_sha256',{})
        modified=any(expected.get('app/'+name,expected.get(name))!=digest for name,digest in files.items())
        result.update(commit=record.get('commit'), release=record.get('tag'),
                      deployment_sha256=hashlib.sha256(raw).hexdigest(), dirty=modified)
    elif (root.parent/'.git').exists():
        try:
            commit = subprocess.run(['git', '-C', str(root.parent), 'rev-parse', 'HEAD'],
                                    capture_output=True, text=True, timeout=3, creationflags=0x08000000)
            status = subprocess.run(['git', '-C', str(root.parent), 'status', '--porcelain', '--untracked-files=normal'],
                                    capture_output=True, text=True, timeout=3, creationflags=0x08000000)
            if commit.returncode == status.returncode == 0:
                result.update(commit=commit.stdout.strip(), dirty=bool(status.stdout.strip()))
        except (OSError, subprocess.SubprocessError):
            pass
    return result
