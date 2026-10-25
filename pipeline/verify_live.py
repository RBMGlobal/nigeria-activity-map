#!/usr/bin/env python3
"""Require live Pages bytes to match the accepted artifact, with bounded CDN retries."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time
import urllib.request


def fetch(url):
    request = urllib.request.Request(url + ('&' if '?' in url else '?') + 'verify=' + str(time.time_ns()),
                                     headers={'User-Agent': 'RBM-release-verification', 'Cache-Control': 'no-cache'})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def verify(site, base_url):
    expected = (site / 'release.json').read_bytes()
    first = fetch(base_url + 'release.json')
    if first != expected:
        raise ValueError('Live release manifest does not match the accepted release yet')
    release = json.loads(expected)
    checked = {}
    for name, item in release['files'].items():
        local = (site / name).read_bytes()
        if len(local) != item['bytes'] or hashlib.sha256(local).hexdigest() != item['sha256']:
            raise ValueError('Local artifact changed: ' + name)
        live = fetch(base_url + name)
        if live != local:
            raise ValueError('Live file does not match accepted bytes: ' + name)
        checked[name] = item
    if fetch(base_url + 'release.json') != first:
        raise ValueError('Release changed during verification')
    return {'verified_at': datetime.now(timezone.utc).isoformat(), 'url': base_url,
            'coverage': release['coverage'], 'files': checked, 'result': 'passed'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--site', type=Path, required=True)
    parser.add_argument('--url', required=True)
    parser.add_argument('--timeout', type=int, default=420)
    args = parser.parse_args()
    deadline = time.monotonic() + args.timeout
    while True:
        try:
            result = verify(args.site, args.url.rstrip('/') + '/')
            break
        except Exception as error:
            if time.monotonic() >= deadline:
                raise RuntimeError('Live publication verification failed: ' + str(error)) from error
            print('Waiting for consistent Pages content:', str(error), flush=True)
            time.sleep(15)
    (args.site.parent / 'verification.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as summary:
            summary.write('## Verified events publication\n\n')
            summary.write('GDELT input coverage through **' + result['coverage']['gdelt']['input_through'] + '**.\n\n')
            summary.write('UCDP maximum event date: **' + result['coverage']['ucdp']['max_event_date'] + '**.\n\n')
            summary.write('Live manifest and all listed assets match the accepted release byte for byte.\n')


if __name__ == '__main__':
    main()
