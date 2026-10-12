"""List private details in the repository before it is made public (plan D46). Read-only.

Scans the files at a revision (default HEAD) and every revision in the history for:
local user paths and the user name, e-mail addresses, detector serial numbers, and other
configured patterns. Images are listed for manual review (text inside them cannot be scanned).
Nothing is changed; the report goes to stdout or --out (keep it outside the repository).

Usage:
    python -X utf8 tools/privacy_audit.py [--user nelso] [--out REPORT.md]
"""
import argparse
import getpass
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.ico', '.svg'}


def git(*args):
    return subprocess.run(['git', '-C', str(REPO), *args], capture_output=True, check=True).stdout.decode('utf-8', 'replace')


def patterns(user):
    return {
        'user home path': rf'[A-Za-z]:[\\/]+Users[\\/]+{re.escape(user)}\b|/[a-z]/Users/{re.escape(user)}\b',
        'user name': rf'\b{re.escape(user)}\b',
        'e-mail address': r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}',
        'detector serial': r'\bRC-\d{3}-\d{6}\b',
    }


def scan_tree(rev, compiled):
    hits = defaultdict(Counter)
    images = []
    for path in git('ls-tree', '-r', '--name-only', rev).splitlines():
        if Path(path).suffix.lower() in IMAGE_SUFFIXES:
            images.append(path)
            continue
        try:
            text = git('show', f'{rev}:{path}')
        except subprocess.CalledProcessError:
            continue
        for name, regex in compiled.items():
            count = len(regex.findall(text))
            if count:
                hits[name][path] += count
    return hits, images


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--user', default=getpass.getuser())
    parser.add_argument('--rev', default='HEAD')
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    compiled = {name: re.compile(p) for name, p in patterns(args.user).items()}
    lines = [f'# Privacy audit ({args.rev}, user "{args.user}")', '']
    hits, images = scan_tree(args.rev, compiled)
    for name in compiled:
        files = hits.get(name, {})
        lines.append(f'## {name}: {sum(files.values())} matches in {len(files)} files at {args.rev}')
        lines += [f'- `{path}`: {count}' for path, count in sorted(files.items(), key=lambda kv: -kv[1])]
        lines.append('')
    revs = git('rev-list', '--all').split()
    history = Counter()
    for rev in revs:
        for name, files in scan_tree(rev, compiled)[0].items():
            if files:
                history[name] += 1
    lines.append(f'## History: {len(revs)} revisions scanned')
    lines += [f'- {name}: present in {history[name]} of {len(revs)} revisions' for name in compiled]
    authors = sorted(set(git('log', '--all', '--format=%an <%ae>|%cn <%ce>').replace('|', '\n').split('\n')) - {''})
    lines += ['', '## Commit identities in history'] + [f'- {a}' for a in authors]
    lines += ['', f'## Images to review manually ({len(images)})'] + [f'- `{p}`' for p in images]
    lines += ['', 'Removing a detail from the current files does not remove it from history. Publish either a '
              'rewritten history or, more simply, a fresh repository created from a scrubbed snapshot.']
    report = '\n'.join(lines) + '\n'
    if args.out:
        args.out.write_text(report, encoding='utf-8')
        print('report written to', args.out)
    else:
        sys.stdout.write(report)
    return 0


if __name__ == '__main__':
    sys.exit(main())
