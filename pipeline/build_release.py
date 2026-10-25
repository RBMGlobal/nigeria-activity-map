#!/usr/bin/env python3
"""Build a complete release in isolation. This program cannot commit or publish.

The checked-in map is also the incremental collector's event store. Its ingestion
state is bound to its exact bytes; both are returned together for one Git commit.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")
SCRIPTS = (
    "update_events_map.py", "update_gdelt_layer.py", "update_reliefweb_layer.py",
    "state_data.py", "render_events_lastweek.py", "validate_map_release.py",
    "refresh_sentiment_heat.py",
)
GEODATA = (
    "ne_50m_west_africa.geojson", "ne_110m_admin_0_countries.geojson",
    "nigeria_sat_z8.png", "nigeria_sat_z8.json",
)
PUBLIC_FILES = ("index.html", "lastweek.png", "sentiment-gdelt.json")
STATE_FILES = ("pipeline/state/gdelt.json", "pipeline/state/seed.json")
ARCHIVE_NAME = re.compile(r"\d{4}-\d{2}-\d{2}\.(?:png|json)\Z")


class BuildError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise BuildError(message)


def read_regular(path):
    require(path.is_file() and not path.is_symlink(), f"Missing or unsafe input: {path.name}")
    return path.read_bytes()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()


def file_metadata(path):
    raw = read_regular(path)
    return {"sha256": digest(raw), "bytes": len(raw)}


def parse_array(text, name):
    matches = re.findall(r"const " + name + r"\s*=\s*(\[.*?\]);", text, re.S)
    require(len(matches) == 1, "Missing or duplicated " + name)
    result = json.loads(matches[0])
    require(isinstance(result, list) and len(result) > 100, "Unexpectedly empty " + name)
    for event in result:
        require(isinstance(event, dict), "Invalid " + name + " event")
        date.fromisoformat(event["d"])
        for key, limit in (("la", 90), ("lo", 180)):
            number = event[key]
            require(isinstance(number, (int, float)) and not isinstance(number, bool)
                    and math.isfinite(number) and abs(number) <= limit,
                    "Invalid event coordinate")
    return result


def validate_source(text, state, today):
    require(len(text) > 500000 and "</html>" in text.lower() and "L.map(" in text,
            "Incomplete map application")
    ucdp = parse_array(text, "EVENTS")
    gdelt = parse_array(text, "GDELT_EVENTS")
    maximum = date.fromisoformat(max(e["d"] for e in ucdp))
    start = maximum - timedelta(days=7)
    through = today - timedelta(days=1)
    require(start <= through and maximum <= today, "Invalid UCDP/GDELT source window")
    require(isinstance(state, dict) and isinstance(state.get("ingested"), list)
            and isinstance(state.get("missing"), list), "Incomplete ingestion state")
    require(not state["missing"], "GDELT has missing daily inputs")
    ingested = state["ingested"]
    require(all(isinstance(d, str) and re.fullmatch(r"\d{8}", d) for d in ingested),
            "Malformed ingestion date")
    for d in ingested:
        datetime.strptime(d, "%Y%m%d")
    require(len(ingested) == len(set(ingested)), "Duplicate ingestion date")
    required = {(start + timedelta(days=n)).strftime("%Y%m%d")
                for n in range((through - start).days + 1)}
    missing = required - set(ingested)
    require(not missing, "Incomplete GDELT inputs: " + ", ".join(sorted(missing)))
    require(all(start.isoformat() <= e["d"] <= through.isoformat() for e in gdelt),
            "GDELT event outside the ingested source window")
    return ucdp, gdelt, {
        "ucdp": {"min_event_date": min(e["d"] for e in ucdp),
                 "max_event_date": maximum.isoformat(), "events": len(ucdp)},
        "gdelt": {"input_from": start.isoformat(), "input_through": through.isoformat(),
                  "input_days": len(required), "min_event_date": min(e["d"] for e in gdelt),
                  "max_event_date": max(e["d"] for e in gdelt), "events": len(gdelt)},
    }


def check_pair(repo):
    raw = read_regular(repo / "index.html")
    state_raw = read_regular(repo / STATE_FILES[0])
    seed = json.loads(read_regular(repo / STATE_FILES[1]))
    require(seed.get("schema_version") == 1, "Unsupported seed binding")
    require(seed.get("index_sha256") == digest(raw), "Map and ingestion-state binding mismatch")
    require(seed.get("gdelt_state_sha256") == digest(state_raw), "Ingestion-state binding mismatch")
    # A stale seed is a valid refresh input. Completeness is required after ingestion.
    text = raw.decode("utf-8")
    parse_array(text, "EVENTS")
    parse_array(text, "GDELT_EVENTS")
    return text, json.loads(state_raw)


def validate_png(path, html_path=None):
    from PIL import Image
    raw = read_regular(path)
    require(len(raw) > 100000 and raw.startswith(b"\x89PNG\r\n\x1a\n"), "Incomplete PNG")
    with Image.open(path) as image:
        require(image.format == "PNG" and image.width >= 1000 and image.height >= 500,
                "Unexpected PNG dimensions")
        image.verify()
    with Image.open(path) as image:
        image.load()
    if html_path:
        require(path.stat().st_mtime_ns >= html_path.stat().st_mtime_ns, "PNG was not freshly rendered")


def validate_heat(path, gdelt):
    heat = json.loads(read_regular(path))
    selected = [e for e in gdelt if int(e["r"]) in (18, 19, 20)]
    require(bool(selected), "Empty heat source")
    grid = defaultdict(int)
    for e in selected:
        grid[(round(e["la"] * 4) / 4, round(e["lo"] * 4) / 4)] += max(1, int(e.get("n", 1)))
    expected = [[lat, lon, weight] for (lat, lon), weight in sorted(grid.items())]
    require(heat.get("points") == expected and heat.get("count") == len(expected)
            and heat.get("raw") == len(selected), "Heat differs from complete GDELT source")
    window = {"from": min(e["d"] for e in selected), "to": max(e["d"] for e in selected)}
    require(all(heat.get(key) == value for key, value in window.items()), "Incorrect heat source window")
    require(heat.get("source") == "https://rbmglobal.co.uk/nigeria-activity-map/"
            and "not verified incidents or opinion polling" in heat.get("method", ""),
            "Heat provenance/caveat missing")
    return window


def child_environment(work):
    # No GitHub credential, Actions token or UCDP secret is inherited by subprocesses.
    allowed = ("PATH", "HOME", "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR", "SYSTEMROOT")
    env = {k: os.environ[k] for k in allowed if k in os.environ}
    env.update(TZ="Europe/London", MPLBACKEND="Agg", MPLCONFIGDIR=str(work / ".matplotlib"),
               PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1")
    return env


def run_script(work, name, arguments=(), secret=""):
    result = subprocess.run([sys.executable, str(work / "scripts" / name), *arguments],
                            cwd=work, env=child_environment(work), text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=2700)
    output = result.stdout
    if secret:
        output = output.replace(secret, "[REDACTED]")
    print(output, end="", flush=True)
    require(result.returncode == 0, f"Stage failed: {name}")
    return output


def stage_workspace(repo, work):
    scripts = work / "scripts"
    (scripts / "geodata").mkdir(parents=True)
    (work / "Published").mkdir()
    for name in SCRIPTS:
        (scripts / name).write_bytes(read_regular(repo / "pipeline/scripts" / name))
    for name in GEODATA:
        (scripts / "geodata" / name).write_bytes(read_regular(repo / "pipeline/scripts/geodata" / name))
    (scripts / ".reliefweb.json").write_bytes(read_regular(repo / "pipeline/scripts/.reliefweb.json"))
    (scripts / ".gdelt_state.json").write_bytes(read_regular(repo / STATE_FILES[0]))
    (work / "Nigeria_Events_Map.html").write_bytes(read_regular(repo / "index.html"))
    # Deliberately do not copy the old PNG: a failed/no-op renderer cannot pass.


def copy_archives(repo, site):
    source = repo / "archive/maps"
    if not source.exists():
        return
    require(source.is_dir() and not source.is_symlink(), "Unsafe map archive")
    for path in sorted(source.iterdir()):
        require(bool(ARCHIVE_NAME.fullmatch(path.name)), "Unexpected file in map archive")
        destination = site / "archive/maps" / path.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(read_regular(path))
    for path in (site / "archive/maps").glob("*.json"):
        archive = json.loads(path.read_text())
        image = path.with_suffix(".png")
        require(image.is_file() and archive.get("png") == file_metadata(image), "Damaged archived map")
    require({p.stem for p in source.glob("*.png")} == {p.stem for p in source.glob("*.json")},
            "Unpaired map archive")


def build(repo, output, *, token="", retained=False, now=None, runner=run_script):
    repo, output = Path(repo).resolve(), Path(output).resolve()
    use_real_clock = now is None
    now = now or datetime.now(timezone.utc)
    require(now.tzinfo is not None, "Build time must include timezone")
    today = now.astimezone(LONDON).date()
    require(not output.exists(), "Output already exists; choose a new build directory")
    check_pair(repo)
    require(retained or (bool(token.strip()) and "\n" not in token and "\r" not in token),
            "UCDP_TOKEN is required for a source refresh")
    output.parent.mkdir(parents=True, exist_ok=True)
    warnings = []
    with tempfile.TemporaryDirectory(prefix=".rbm-build-", dir=output.parent) as temp:
        temp = Path(temp)
        work = temp / "work"
        stage_workspace(repo, work)
        html_path = work / "Nigeria_Events_Map.html"
        sources = None
        if not retained:
            token_path = work / "scripts/.ucdp_token"
            fd = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(fd, "w") as stream:
                    stream.write(token.strip())
                log = runner(work, "update_events_map.py", secret=token.strip())
                source_lines = [line[9:] for line in (log or "").splitlines() if line.startswith("Sources: ")]
                sources = source_lines[-1] if source_lines else None
            finally:
                token_path.unlink(missing_ok=True)
            runner(work, "update_gdelt_layer.py", ("3660",))
            before = html_path.read_bytes()
            try:
                runner(work, "update_reliefweb_layer.py")
            except (BuildError, subprocess.TimeoutExpired):
                html_path.write_bytes(before)
                warnings.append("ReliefWeb refresh failed; prior report snapshot and source dates retained.")
        # Check full coverage BEFORE spending time rendering; event max is not input coverage.
        text = html_path.read_text()
        state = json.loads((work / "scripts/.gdelt_state.json").read_text())
        _, gdelt, coverage = validate_source(text, state, today)
        if sources:
            coverage["ucdp"]["source_versions"] = sources
        rw = re.search(r"const RELIEFWEB_META = (\{.*?\});", text, re.S)
        if rw:
            meta = json.loads(rw[1])
            coverage["reliefweb"] = {key: meta[key] for key in
                                     ("from", "to", "fetched_at", "latest_publication", "count") if key in meta}
            coverage["reliefweb"]["refresh"] = "retained" if retained or warnings else "checked"
        runner(work, "render_events_lastweek.py")
        image_path = work / "Nigeria_Events_Map_LastWeek_latest.png"
        validate_png(image_path, html_path)
        runner(work, "validate_map_release.py")
        runner(work, "refresh_sentiment_heat.py")
        heat_path = work / "Published/sentiment-gdelt.json"
        coverage["heat"] = validate_heat(heat_path, gdelt)
        # Reject a run spanning London midnight instead of assigning inconsistent coverage.
        if use_real_clock:  # main() additionally checks at the end; injected test times stay deterministic.
            require(datetime.now(LONDON).date() == today, "London date changed during build; retry")
        release = temp / "release"
        site, changes = release / "site", release / "repository"
        site.mkdir(parents=True)
        changes.mkdir()
        for name, source in (("index.html", html_path), ("lastweek.png", image_path),
                             ("sentiment-gdelt.json", heat_path)):
            (site / name).write_bytes(read_regular(source))
        generated = now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        manifest = {"schema_version": 1, "generated_at": generated,
                    "mode": "retained-source-rebuild" if retained else "source-refresh",
                    "coverage": coverage, "warnings": warnings,
                    "files": {name: file_metadata(site / name) for name in PUBLIC_FILES}}
        (site / "release.json").write_bytes(json_bytes(manifest))
        (site / ".nojekyll").write_bytes(b"")
        copy_archives(repo, site)
        commit_paths = [*PUBLIC_FILES, "release.json", *STATE_FILES]
        if today.weekday() == 0:
            stem = "archive/maps/" + today.isoformat()
            archive_png = site / (stem + ".png")
            archive_meta = site / (stem + ".json")
            if not archive_png.exists():
                archive_png.parent.mkdir(parents=True, exist_ok=True)
                archive_png.write_bytes(read_regular(image_path))
                archive_meta.write_bytes(json_bytes({
                    "schema_version": 1, "captured_on": today.isoformat(), "generated_at": generated,
                    "date_basis": "Monday capture date; panel windows below are actual event dates.",
                    "ucdp": {"from": (date.fromisoformat(coverage["ucdp"]["max_event_date"]) - timedelta(days=6)).isoformat(),
                             "to": coverage["ucdp"]["max_event_date"]},
                    "gdelt": {"from": (date.fromisoformat(coverage["gdelt"]["max_event_date"]) - timedelta(days=6)).isoformat(),
                              "to": coverage["gdelt"]["max_event_date"]},
                    "gdelt_input_through": coverage["gdelt"]["input_through"], "png": file_metadata(archive_png)}))
                commit_paths += [stem + ".png", stem + ".json"]
        for name in commit_paths:
            destination = changes / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            if name == STATE_FILES[0]:
                destination.write_bytes(read_regular(work / "scripts/.gdelt_state.json"))
            elif name == STATE_FILES[1]:
                destination.write_bytes(json_bytes({"schema_version": 1,
                    "index_sha256": file_metadata(site / "index.html")["sha256"],
                    "gdelt_state_sha256": digest(read_regular(work / "scripts/.gdelt_state.json"))}))
            else:
                destination.write_bytes(read_regular(site / name))
        (release / "commit-manifest.json").write_bytes(json_bytes({
            "schema_version": 1, "files": {name: file_metadata(changes / name) for name in commit_paths}}))
        # No work directory, credentials, logs, Python source or ingestion state enters site/.
        for path in release.rglob("*"):
            require(not path.is_symlink(), "Symlink in release")
            if path.is_file() and token:
                require(token.strip().encode() not in path.read_bytes(), "Credential found in release; refusing output")
        release.rename(output)
    return manifest


def accepted_release(repo, output):
    """Redeploy already accepted bytes without collecting or changing any date."""
    repo, output = Path(repo).resolve(), Path(output).resolve()
    require(not output.exists(), "Output already exists; choose a new build directory")
    manifest_raw = read_regular(repo / "release.json")
    manifest = json.loads(manifest_raw)
    require(manifest.get("schema_version") == 1, "Unsupported release schema")
    require(set(manifest.get("files", {})) == set(PUBLIC_FILES), "Incomplete release manifest")
    for name in PUBLIC_FILES:
        require(manifest["files"][name] == file_metadata(repo / name), "Release hash mismatch: " + name)
    stamp = datetime.fromisoformat(manifest["generated_at"].replace("Z", "+00:00"))
    require(stamp.tzinfo is not None, "Release time missing timezone")
    text, state = check_pair(repo)
    # Last-good redeploy may be old; validate its own original input date, not today.
    through = date.fromisoformat(manifest["coverage"]["gdelt"]["input_through"])
    require(through <= datetime.now(LONDON).date() - timedelta(days=1), "Release has future inputs")
    _, gdelt, coverage = validate_source(text, state, through + timedelta(days=1))
    for source in ("ucdp", "gdelt"):
        require(all(manifest["coverage"][source].get(k) == v for k, v in coverage[source].items()),
                "Release coverage differs from source: " + source)
    require(validate_heat(repo / "sentiment-gdelt.json", gdelt) == manifest["coverage"]["heat"],
            "Release heat window mismatch")
    validate_png(repo / "lastweek.png")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".rbm-redeploy-", dir=output.parent) as temp:
        release = Path(temp) / "release"
        site = release / "site"
        site.mkdir(parents=True)
        (release / "repository").mkdir()
        for name in PUBLIC_FILES:
            (site / name).write_bytes(read_regular(repo / name))
        (site / "release.json").write_bytes(manifest_raw)
        (site / ".nojekyll").write_bytes(b"")
        copy_archives(repo, site)
        (release / "commit-manifest.json").write_bytes(json_bytes({"schema_version": 1, "files": {}}))
        release.rename(output)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, default=Path("build"))
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--check-seed", action="store_true", help="Offline integrity/coverage check; no writes")
    modes.add_argument("--rebuild-from-seed", action="store_true", help="Offline render using retained sources; requires current input coverage")
    modes.add_argument("--accepted-release", action="store_true", help="Verify and stage the committed accepted release without changing dates")
    args = parser.parse_args()
    started = datetime.now(timezone.utc)
    if args.check_seed:
        text, state = check_pair(args.repo)
        _, _, coverage = validate_source(text, state, started.astimezone(LONDON).date())
        validate_png(args.repo / "lastweek.png")
        print(json.dumps(coverage, indent=2))
        return
    if args.accepted_release:
        manifest = accepted_release(args.repo, args.output)
        print("Accepted release staged unchanged:", manifest["generated_at"])
        return
    manifest = build(args.repo, args.output, token=os.environ.get("UCDP_TOKEN", ""),
                     retained=args.rebuild_from_seed, now=started)
    if datetime.now(LONDON).date() != started.astimezone(LONDON).date():
        shutil.rmtree(args.output)
        raise BuildError("London date changed during build; output discarded, retry")
    print("Validated release:", manifest["mode"], "GDELT inputs through",
          manifest["coverage"]["gdelt"]["input_through"], "event maximum",
          manifest["coverage"]["gdelt"]["max_event_date"])


if __name__ == "__main__":
    try:
        main()
    except (BuildError, ValueError, KeyError, OSError, subprocess.TimeoutExpired) as error:
        print("Release build failed; existing publication retained:", error, file=sys.stderr)
        sys.exit(1)
