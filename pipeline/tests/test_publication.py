import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if not (ROOT / 'commit_release.py').exists():
    ROOT = Path(__file__).resolve().parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + '.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


commit = load('commit_release')
live = load('verify_live')


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.build = Path(self.tmp.name)
        (self.build / 'repository').mkdir()
        (self.build / 'site').mkdir()

    def prepared(self):
        entries = {}
        for name in commit.FIXED:
            path = self.build / 'repository' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('accepted ' + name)
            entries[name] = {'bytes': path.stat().st_size,
                             'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        (self.build / 'commit-manifest.json').write_text(json.dumps({'schema_version': 1, 'files': entries}))
        return entries

    def test_commit_requires_all_release_and_state_files(self):
        entries = self.prepared()
        entries.pop('pipeline/state/gdelt.json')
        (self.build / 'repository/pipeline/state/gdelt.json').unlink()
        (self.build / 'commit-manifest.json').write_text(json.dumps({'schema_version': 1, 'files': entries}))
        with self.assertRaisesRegex(ValueError, 'together'):
            commit.validate_manifest(self.build)

    def test_tampered_release_is_rejected(self):
        self.prepared()
        (self.build / 'repository/index.html').write_text('partial replacement')
        with self.assertRaisesRegex(ValueError, 'changed'):
            commit.validate_manifest(self.build)

    def test_unlisted_private_file_is_rejected(self):
        self.prepared()
        (self.build / 'repository/.ucdp_token').write_text('test-only-not-a-credential')
        with self.assertRaisesRegex(ValueError, 'exactly'):
            commit.validate_manifest(self.build)

    def test_complete_manifest_and_empty_redeploy(self):
        self.prepared()
        self.assertEqual(set(commit.validate_manifest(self.build)), commit.FIXED)
        for path in (self.build / 'repository').rglob('*'):
            if path.is_file():
                path.unlink()
        (self.build / 'commit-manifest.json').write_text('{"schema_version":1,"files":{}}')
        self.assertEqual(commit.validate_manifest(self.build), {})

    def site(self):
        body = b'complete published map'
        release = {'schema_version': 1, 'coverage': {'gdelt': {'input_through': '2026-10-24'},
                   'ucdp': {'max_event_date': '2026-09-30'}}, 'files': {'index.html': {
                   'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest()}}}
        manifest = json.dumps(release).encode()
        (self.build / 'site/index.html').write_bytes(body)
        (self.build / 'site/release.json').write_bytes(manifest)
        return body, manifest

    def test_live_content_must_match_both_sides_of_manifest_read(self):
        body, manifest = self.site()
        with patch.object(live, 'fetch', side_effect=[manifest, body, manifest]):
            self.assertEqual(live.verify(self.build / 'site', 'https://example.test/')['result'], 'passed')
        with patch.object(live, 'fetch', side_effect=[manifest, body, b'new release']):
            with self.assertRaisesRegex(ValueError, 'changed during'):
                live.verify(self.build / 'site', 'https://example.test/')
        with patch.object(live, 'fetch', side_effect=[manifest, b'truncated']):
            with self.assertRaisesRegex(ValueError, 'does not match'):
                live.verify(self.build / 'site', 'https://example.test/')


if __name__ == '__main__':
    unittest.main()
