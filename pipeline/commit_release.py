#!/usr/bin/env python3
"""Commit only the complete, validated release allowlist; never force-push."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess


FIXED = {'index.html', 'lastweek.png', 'sentiment-gdelt.json', 'release.json',
         'pipeline/state/gdelt.json', 'pipeline/state/seed.json'}


def run(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()


def validate_manifest(build):
    build = build.resolve()
    manifest = json.loads((build / 'commit-manifest.json').read_text())
    if manifest.get('schema_version') != 1 or not isinstance(manifest.get('files'), dict):
        raise ValueError('Invalid commit manifest')
    files = manifest['files']
    actual = {p.relative_to(build / 'repository').as_posix()
              for p in (build / 'repository').rglob('*') if p.is_file()}
    if actual != set(files):
        raise ValueError('Commit manifest must cover exactly the prepared repository files')
    if files and not FIXED.issubset(files):
        raise ValueError('Release and ingestion state must be committed together')
    for name, item in files.items():
        if name not in FIXED and not re.fullmatch(r'archive/maps/\d{4}-\d{2}-\d{2}\.(png|json)', name):
            raise ValueError('Unexpected release path: ' + name)
        path = build / 'repository' / name
        if any((build / 'repository').joinpath(*Path(name).parts[:i]).is_symlink()
               for i in range(len(Path(name).parts) + 1)):
            raise ValueError('Symlinks cannot be committed')
        data = path.read_bytes()
        if len(data) != item['bytes'] or hashlib.sha256(data).hexdigest() != item['sha256']:
            raise ValueError('Prepared release changed: ' + name)
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', type=Path, required=True)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    build, repo = args.build.resolve(), args.repo.resolve()
    files = validate_manifest(build)
    if not files:
        print('Redeploying the accepted release; no source or state changes.')
        return
    if run(repo, 'diff', '--cached', '--name-only'):
        raise RuntimeError('Refusing to include unrelated staged changes')
    head = run(repo, 'rev-parse', 'HEAD')
    remote = run(repo, 'ls-remote', 'origin', 'refs/heads/main').split()[0]
    if head != remote:
        raise RuntimeError('Main changed during this build; rerun against the current source')
    for name in files:
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(build / 'repository' / name, target)
    run(repo, 'add', '--', *sorted(files))
    staged = set(run(repo, 'diff', '--cached', '--name-only').splitlines())
    if not staged:
        print('Accepted files unchanged; deployment and live verification will still run.')
        return
    if not staged.issubset(files):
        raise RuntimeError('Unexpected files staged for publication')
    release = json.loads((build / 'site/release.json').read_text())
    through = release['coverage']['gdelt']['input_through']
    run(repo, '-c', 'user.name=github-actions[bot]',
        '-c', 'user.email=41898282+github-actions[bot]@users.noreply.github.com',
        'commit', '-m', 'Publish validated events release through ' + through)
    run(repo, 'push', 'origin', 'HEAD:refs/heads/main')
    print('Accepted release and ingestion state committed atomically:', run(repo, 'rev-parse', 'HEAD'))


if __name__ == '__main__':
    main()
