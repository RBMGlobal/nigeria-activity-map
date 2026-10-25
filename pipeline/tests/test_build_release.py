"""Offline regression tests for release isolation, resumable state and provenance."""
from datetime import date, datetime, timedelta, timezone
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

PIPELINE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_release", PIPELINE / "build_release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)
SUNDAY = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
MONDAY = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
TOKEN = "synthetic-test-token-not-a-real-credential-123456789"


def valid_png():
    buffer = io.BytesIO()
    Image.new("RGB", (1000, 500), "#123456").save(buffer, format="PNG", compress_level=0)
    return buffer.getvalue()


PNG = valid_png()


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        scripts = self.repo / "pipeline/scripts"
        (scripts / "geodata").mkdir(parents=True)
        (self.repo / "pipeline/state").mkdir()
        for name in release.SCRIPTS:
            (scripts / name).write_text("# Synthetic subprocess placeholder\n")
        for name in release.GEODATA:
            (scripts / "geodata" / name).write_text("synthetic geodata")
        (scripts / ".reliefweb.json").write_text('{"appname":"test","window_days":90}')
        self.ucdp = [{"d": "2026-09-30", "la": 8, "lo": 8, "t": 1, "k": 1, "c": 0,
                      "s": "Test", "w": "Test"} for _ in range(101)]
        self.gdelt = [{"d": "2026-10-23", "la": 8, "lo": 8, "r": 19, "n": 1,
                       "id": str(i), "s": "Test", "w": "Test"} for i in range(101)]
        self.text = '<html><script>/*' + ('padding ' * 65000) + '*/\nL.map("map");\n'
        self.text += "const EVENTS = " + json.dumps(self.ucdp) + ";\n"
        self.text += "const GDELT_EVENTS = " + json.dumps(self.gdelt) + ";\n"
        self.text += 'const RELIEFWEB_META = {"from":"2026-07-28","to":"2026-10-25","fetched_at":"2026-10-25T10:00:00+00:00","latest_publication":"2026-10-24","count":12};\n</script></html>'
        (self.repo / "index.html").write_text(self.text)
        (self.repo / "lastweek.png").write_bytes(PNG)
        self.state = {"ingested": [(date(2026, 9, 23) + timedelta(days=i)).strftime("%Y%m%d")
                                   for i in range(32)], "missing": []}
        self.write_state(self.state)
        self.calls = []
        self.output = self.root / "build"

    def tearDown(self):
        self.temp.cleanup()

    def write_state(self, state):
        state_path = self.repo / release.STATE_FILES[0]
        state_path.write_bytes(release.json_bytes(state))
        (self.repo / release.STATE_FILES[1]).write_bytes(release.json_bytes({
            "schema_version": 1,
            "index_sha256": release.file_metadata(self.repo / "index.html")["sha256"],
            "gdelt_state_sha256": release.file_metadata(state_path)["sha256"],
        }))

    def heat(self):
        return {"from": "2026-10-23", "to": "2026-10-23", "count": 1, "raw": 101,
                "points": [[8.0, 8.0, 101]],
                "method": "Machine-coded reporting, not verified incidents or opinion polling.",
                "source": "https://rbmglobal.co.uk/nigeria-activity-map/"}

    def runner(self, work, name, arguments=(), secret=""):
        self.calls.append(name)
        token_path = work / "scripts/.ucdp_token"
        if name == "update_events_map.py":
            self.assertEqual(token_path.read_text(), TOKEN)
            self.assertEqual(stat.S_IMODE(token_path.stat().st_mode), 0o600)
            self.assertEqual(secret, TOKEN)
            return "Sources: annual 26.1; candidate: 26.0.9\n"
        self.assertFalse(token_path.exists(), "Credential survives past UCDP")
        if name == "update_gdelt_layer.py":
            self.assertEqual(arguments, ("3660",))
        if name == "render_events_lastweek.py":
            self.assertFalse((work / "Nigeria_Events_Map_LastWeek_latest.png").exists())
            (work / "Nigeria_Events_Map_LastWeek_latest.png").write_bytes(PNG)
        if name == "refresh_sentiment_heat.py":
            (work / "Published/sentiment-gdelt.json").write_bytes(release.json_bytes(self.heat()))
        return ""

    def build(self, **kwargs):
        return release.build(self.repo, self.output, token=TOKEN, now=SUNDAY,
                             runner=kwargs.pop("runner", self.runner), **kwargs)

    def accept_output(self):
        for path in (self.output / "repository").rglob("*"):
            if path.is_file():
                target = self.repo / path.relative_to(self.output / "repository")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)

    def test_pipeline_order_and_no_credentials_in_outputs(self):
        original = (self.repo / "index.html").read_bytes()
        manifest = self.build()
        self.assertEqual(self.calls, ["update_events_map.py", "update_gdelt_layer.py",
                         "update_reliefweb_layer.py", "render_events_lastweek.py",
                         "validate_map_release.py", "refresh_sentiment_heat.py"])
        self.assertEqual((self.repo / "index.html").read_bytes(), original)
        self.assertEqual(manifest["coverage"]["gdelt"]["input_through"], "2026-10-24")
        self.assertEqual(manifest["coverage"]["gdelt"]["max_event_date"], "2026-10-23")
        self.assertEqual(manifest["coverage"]["ucdp"]["source_versions"], "annual 26.1; candidate: 26.0.9")
        self.assertEqual(set(manifest["files"]), set(release.PUBLIC_FILES))
        files = [p for p in self.output.rglob("*") if p.is_file()]
        self.assertTrue(all(TOKEN.encode() not in p.read_bytes() for p in files))
        self.assertFalse(any(p.name.startswith(".ucdp") or p.name.startswith(".github_token") for p in files))
        self.assertEqual(set(p.name for p in (self.output / "site").iterdir()),
                         {*release.PUBLIC_FILES, "release.json", ".nojekyll"})
        commits = json.loads((self.output / "commit-manifest.json").read_text())["files"]
        self.assertEqual(set(commits), {*release.PUBLIC_FILES, "release.json", *release.STATE_FILES})
        for name, expected in commits.items():
            self.assertEqual(release.file_metadata(self.output / "repository" / name), expected)

    def test_missing_day_fails_even_with_latest_event_present(self):
        self.state["ingested"].remove("20260923")
        self.write_state(self.state)
        with self.assertRaisesRegex(release.BuildError, "20260923"):
            self.build()
        self.assertFalse(self.output.exists())
        self.assertNotIn("render_events_lastweek.py", self.calls)

    def test_failed_fetch_cannot_claim_complete_by_remaining_zero(self):
        self.state["ingested"].remove("20261024")
        self.write_state(self.state)
        with self.assertRaisesRegex(release.BuildError, "20261024"):
            self.build()
        self.assertFalse(self.output.exists())

    def test_missing_file_flag_fails_closed(self):
        self.state["missing"] = ["20260925"]
        self.write_state(self.state)
        with self.assertRaisesRegex(release.BuildError, "missing daily inputs"):
            self.build()

    def test_resume_retains_prior_events_and_commits_corresponding_state(self):
        self.state["ingested"].remove("20261024")
        self.write_state(self.state)
        before = (self.repo / release.STATE_FILES[0]).read_bytes()
        def resume(work, name, arguments=(), secret=""):
            if name == "update_gdelt_layer.py":
                state_path = work / "scripts/.gdelt_state.json"
                state = json.loads(state_path.read_text())
                self.assertNotIn("20261024", state["ingested"])
                state["ingested"].append("20261024")
                state_path.write_bytes(release.json_bytes(state))
            return self.runner(work, name, arguments, secret)
        self.build(runner=resume)
        self.assertEqual((self.repo / release.STATE_FILES[0]).read_bytes(), before)
        after = json.loads((self.output / "repository" / release.STATE_FILES[0]).read_text())
        self.assertIn("20261024", after["ingested"])
        self.assertEqual((self.output / "site/index.html").read_text(), self.text)

    def test_pair_rejects_modified_html_without_corresponding_binding(self):
        with (self.repo / "index.html").open("a") as stream:
            stream.write("\n<!-- edited -->")
        with self.assertRaisesRegex(release.BuildError, "binding mismatch"):
            self.build()
        self.assertEqual(self.calls, [])

    def test_each_mandatory_stage_failure_preserves_last_good(self):
        for failed in ("update_events_map.py", "update_gdelt_layer.py", "render_events_lastweek.py",
                       "validate_map_release.py", "refresh_sentiment_heat.py"):
            with self.subTest(stage=failed):
                before = {p.name: p.read_bytes() for p in (self.repo / "index.html", self.repo / "lastweek.png")}
                def fail(work, name, arguments=(), secret=""):
                    if name == failed:
                        (work / "Nigeria_Events_Map.html").write_text("broken")
                        raise release.BuildError("injected failure")
                    return self.runner(work, name, arguments, secret)
                with self.assertRaisesRegex(release.BuildError, "injected"):
                    self.build(runner=fail)
                self.assertFalse(self.output.exists())
                self.assertFalse(list(self.root.glob(".rbm-build-*")))
                for name, data in before.items():
                    self.assertEqual((self.repo / name).read_bytes(), data)

    def test_optional_reliefweb_failure_restores_snapshot(self):
        def fail_optional(work, name, arguments=(), secret=""):
            if name == "update_reliefweb_layer.py":
                (work / "Nigeria_Events_Map.html").write_text("broken optional partial output")
                raise release.BuildError("optional source unavailable")
            return self.runner(work, name, arguments, secret)
        manifest = self.build(runner=fail_optional)
        self.assertEqual((self.output / "site/index.html").read_text(), self.text)
        self.assertEqual(manifest["coverage"]["reliefweb"]["fetched_at"], "2026-10-25T10:00:00+00:00")
        self.assertEqual(manifest["coverage"]["reliefweb"]["refresh"], "retained")
        self.assertEqual(len(manifest["warnings"]), 1)

    def test_missing_new_png_is_not_replaced_by_seed_png(self):
        def no_render(work, name, arguments=(), secret=""):
            if name == "render_events_lastweek.py":
                return "successful no-op"
            return self.runner(work, name, arguments, secret)
        with self.assertRaisesRegex(release.BuildError, "Missing or unsafe"):
            self.build(runner=no_render)
        self.assertFalse(self.output.exists())

    def test_png_truncation_rejected(self):
        def truncated(work, name, arguments=(), secret=""):
            result = self.runner(work, name, arguments, secret)
            if name == "render_events_lastweek.py":
                (work / "Nigeria_Events_Map_LastWeek_latest.png").write_bytes(PNG[:150000])
            return result
        with self.assertRaises((OSError, SyntaxError)):
            self.build(runner=truncated)
        self.assertFalse(self.output.exists())

    def test_heat_window_must_match_real_events(self):
        def wrong_heat(work, name, arguments=(), secret=""):
            result = self.runner(work, name, arguments, secret)
            if name == "refresh_sentiment_heat.py":
                heat = self.heat()
                heat["to"] = "2026-10-24"
                (work / "Published/sentiment-gdelt.json").write_bytes(release.json_bytes(heat))
            return result
        with self.assertRaisesRegex(release.BuildError, "heat source window"):
            self.build(runner=wrong_heat)

    def test_nonfinite_coordinates_rejected(self):
        text = self.text.replace('"la": 8', '"la": NaN', 1)
        with self.assertRaisesRegex(release.BuildError, "coordinate"):
            release.validate_source(text, self.state, SUNDAY.date())

    def test_duplicate_input_date_rejected(self):
        self.state["ingested"].append(self.state["ingested"][-1])
        with self.assertRaisesRegex(release.BuildError, "Duplicate"):
            release.validate_source(self.text, self.state, SUNDAY.date())

    def test_retained_rebuild_never_calls_any_ingestion(self):
        manifest = release.build(self.repo, self.output, retained=True, now=SUNDAY, runner=self.runner)
        self.assertEqual(self.calls, ["render_events_lastweek.py", "validate_map_release.py", "refresh_sentiment_heat.py"])
        self.assertEqual(manifest["mode"], "retained-source-rebuild")
        self.assertEqual(manifest["coverage"]["reliefweb"]["refresh"], "retained")

    def test_refresh_requires_token_before_any_work(self):
        with self.assertRaisesRegex(release.BuildError, "UCDP_TOKEN"):
            release.build(self.repo, self.output, now=SUNDAY, runner=self.runner)
        self.assertEqual(self.calls, [])

    def test_existing_output_never_overwritten(self):
        self.output.mkdir()
        sentinel = self.output / "keep"
        sentinel.write_text("accepted last good")
        with self.assertRaisesRegex(release.BuildError, "already exists"):
            self.build()
        self.assertEqual(sentinel.read_text(), "accepted last good")

    def test_child_environment_excludes_all_tokens(self):
        with patch.dict(os.environ, {"GITHUB_TOKEN": "test", "UCDP_TOKEN": "test",
                                     "ACTIONS_RUNTIME_TOKEN": "test", "MPLBACKEND": "QtAgg"}):
            env = release.child_environment(self.root)
        self.assertFalse({"GITHUB_TOKEN", "UCDP_TOKEN", "ACTIONS_RUNTIME_TOKEN"} & env.keys())
        self.assertEqual(env["TZ"], "Europe/London")
        self.assertEqual(env["MPLBACKEND"], "Agg")

    def test_monday_archive_uses_capture_date_and_actual_data_windows(self):
        self.state["ingested"].append("20261025")
        self.write_state(self.state)
        release.build(self.repo, self.output, token=TOKEN, now=MONDAY, runner=self.runner)
        archive = self.output / "site/archive/maps/2026-10-26.json"
        meta = json.loads(archive.read_text())
        self.assertEqual(meta["ucdp"], {"from": "2026-09-24", "to": "2026-09-30"})
        self.assertEqual(meta["gdelt"], {"from": "2026-10-17", "to": "2026-10-23"})
        self.assertEqual(meta["gdelt_input_through"], "2026-10-25")
        self.accept_output()
        old = archive.read_bytes()
        self.output = self.root / "retry"
        release.build(self.repo, self.output, token=TOKEN, now=MONDAY, runner=self.runner)
        self.assertEqual((self.output / "site/archive/maps/2026-10-26.json").read_bytes(), old)
        commits = json.loads((self.output / "commit-manifest.json").read_text())["files"]
        self.assertNotIn("archive/maps/2026-10-26.json", commits)

    def test_accepted_release_preserves_all_bytes_and_timestamps(self):
        self.build()
        self.accept_output()
        expected = (self.repo / "release.json").read_bytes()
        redeploy = self.root / "redeploy"
        release.accepted_release(self.repo, redeploy)
        self.assertEqual((redeploy / "site/release.json").read_bytes(), expected)
        self.assertEqual(json.loads((redeploy / "commit-manifest.json").read_text())["files"], {})
        self.assertEqual(list((redeploy / "repository").iterdir()), [])

    def test_accepted_release_rejects_changed_public_bytes(self):
        self.build()
        self.accept_output()
        with (self.repo / "lastweek.png").open("ab") as stream:
            stream.write(b"tampered")
        with self.assertRaisesRegex(release.BuildError, "hash mismatch"):
            release.accepted_release(self.repo, self.root / "redeploy")

    def test_symlink_seed_rejected(self):
        target = self.root / "outside.html"
        shutil.move(self.repo / "index.html", target)
        (self.repo / "index.html").symlink_to(target)
        with self.assertRaisesRegex(release.BuildError, "unsafe input"):
            self.build()


if __name__ == "__main__":
    unittest.main()
