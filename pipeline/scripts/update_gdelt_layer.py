#!/usr/bin/env python3
"""
Update the GDELT news-derived layer (`const GDELT_EVENTS = [...]`) in
Nigeria_Events_Map.html. Run AFTER update_events_map.py (UCDP), which
rewrites the subtitle this script appends to.

Design (agreed 2026-07-15):
- Source: GDELT 1.0 daily event files
  https://data.gdeltproject.org/events/YYYYMMDD.export.CSV.zip
  (posted ~6AM EST the following morning; no API key; English-language press,
  which dominates Nigeria coverage).
- Scope: CAMEO root codes 14 (protest), 18 (assault), 19 (fight),
  20 (mass violence); ActionGeo country NI; geo precision ADM1 or better.
- Window: GAP-FILLER ONLY — from (max UCDP date in the map − OVERLAP_DAYS)
  to yesterday. As UCDP Candidate data catches up monthly, the GDELT layer
  retreats automatically. UCDP remains the authoritative 24-month record.
- Dedup, three levels:
    1. GlobalEventID       — exact re-ingest protection
    2. collapse key        — (day, lat/lon @3dp, root code) and
                             (day, root, source URL): GDELT codes the same
                             incident many times across outlets
    3. cross-source        — drop GDELT events within DEDUP_KM and ±1 day of
                             any UCDP event already on the map
- Incremental: ingested days tracked in .gdelt_state.json; each run fetches
  only missing days (max MAX_FILES per invocation — pass a number as argv[1]
  to override). Prints "REMAINING: n" — rerun until 0. Days that 404 and are
  ≥3 days old are marked missing and not retried.
- Refuses to touch the HTML if markers are absent; writes atomically.

Usage: python3 update_gdelt_layer.py [max_files]     (stdlib only)
"""
import io
import json
import math
import re
import sys
import urllib.error
import urllib.request
import zipfile
from datetime import date, timedelta
from pathlib import Path
from embedded_data import load_json, replace_json

ROOTS = {"14", "18", "19", "20"}   # protest, assault, fight, mass violence
GEO_TYPES = {"2", "3", "4", "5"}   # ADM1, ADM2, city, landmark (drop country-level)
OVERLAP_DAYS = 7                   # window starts this many days before UCDP max
DEDUP_KM = 15.0                    # cross-source proximity threshold
MIN_ARTICLES = 1                   # keep single-article events (thin NE coverage)
MAX_FILES_DEFAULT = 8              # daily files per invocation (~4-6 s each)
URL_TPL = "https://data.gdeltproject.org/events/{day}.export.CSV.zip"

SCRIPT_DIR = Path(__file__).resolve().parent
HTML_PATH = SCRIPT_DIR.parent / "Nigeria_Events_Map.html"
STATE_PATH = SCRIPT_DIR / ".gdelt_state.json"

GTYPE_LABEL = {14: "Protest", 18: "Assault", 19: "Armed clash", 20: "Mass violence"}


def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {"ingested": [], "missing": []}


def save_state(st):
    STATE_PATH.write_text(json.dumps(st, indent=0))


def embedded_json(events):
    """Preserve data values without allowing source text to close a script tag."""
    return (json.dumps(events, ensure_ascii=False, separators=(", ", ": "))
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def parse_array(html, name):
    return load_json(html, name)


def clean_url(u):
    u = (u or "").strip()
    if not u.startswith("http"):
        return ""
    for bad, rep in (("]", "%5D"), ("<", "%3C"), (">", "%3E"), ('"', "%22"), ("'", "%27")):
        u = u.replace(bad, rep)
    return u[:220]


def title_case(s):
    s = (s or "").strip()
    return s.title() if s else ""


def fetch_day(day):
    """Download and parse one daily file. Returns list of compact events."""
    req = urllib.request.Request(URL_TPL.format(day=day),
                                 headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=40) as r:
        z = zipfile.ZipFile(io.BytesIO(r.read()))
    raw = z.read(z.namelist()[0]).decode("utf-8", "replace")
    out = []
    for line in raw.splitlines():
        c = line.split("\t")
        if len(c) < 58 or c[51] != "NI" or c[28] not in ROOTS or c[49] not in GEO_TYPES:
            continue
        try:
            la, lo = float(c[53]), float(c[54])
            arts = int(c[33] or 0)
        except ValueError:
            continue
        if arts < MIN_ARTICLES:
            continue
        full = [p.strip() for p in (c[50] or "").split(",")]
        # "City, State, Nigeria" | "State, Nigeria" | edge cases
        state = full[-2] if len(full) >= 2 else ""
        place = full[0] if len(full) >= 3 else ""
        out.append({
            "id": c[0],
            "d": f"{c[1][:4]}-{c[1][4:6]}-{c[1][6:8]}",
            "la": round(la, 5), "lo": round(lo, 5),
            "r": int(c[28]),
            "e": c[26],  # full CAMEO code, e.g. 193 "fight with small arms"
            "a": title_case(c[6]), "b": title_case(c[16]),
            "w": place, "s": state,
            "n": arts, "g": int(c[49]),
            "u": clean_url(c[57]),
            "x": 1,  # raw records collapsed into this event
        })
    return out


def collapse(events):
    """Merge near-duplicate records (same incident, many articles)."""
    by_key = {}
    for e in events:
        keys = [(e["d"], round(e["la"], 3), round(e["lo"], 3), e["r"])]
        if e["u"]:
            keys.append((e["d"], e["r"], e["u"]))
        hit = None
        for k in keys:
            if k in by_key:
                hit = by_key[k]
                break
        if hit is None:
            for k in keys:
                by_key[k] = e
        else:
            hit["x"] += e.get("x", 1)
            if e["n"] > hit["n"]:            # keep best-reported representative
                for f in ("id", "la", "lo", "e", "a", "b", "w", "s", "n", "g", "u"):
                    hit[f] = e.get(f, hit.get(f))
            for k in keys:
                by_key.setdefault(k, hit)
    seen, out = set(), []
    for e in by_key.values():
        if id(e) not in seen:
            seen.add(id(e))
            out.append(e)
    return out


def km(la1, lo1, la2, lo2):
    dla = math.radians(la2 - la1)
    dlo = math.radians(lo2 - lo1)
    a = (math.sin(dla / 2) ** 2 +
         math.cos(math.radians(la1)) * math.cos(math.radians(la2)) * math.sin(dlo / 2) ** 2)
    return 2 * 6371 * math.asin(math.sqrt(a))


def drop_near_ucdp(gdelt, ucdp, start_iso):
    """Remove GDELT events within DEDUP_KM and ±1 day of a UCDP event."""
    recent = [u for u in ucdp if u["d"] >= start_iso]
    if not recent:
        return gdelt, 0
    by_day = {}
    for u in recent:
        by_day.setdefault(u["d"], []).append(u)
    kept, dropped = [], 0
    for e in gdelt:
        d0 = date.fromisoformat(e["d"])
        cands = []
        for dd in (-1, 0, 1):
            cands += by_day.get((d0 + timedelta(days=dd)).isoformat(), [])
        if any(km(e["la"], e["lo"], u["la"], u["lo"]) <= DEDUP_KM for u in cands):
            dropped += 1
        else:
            kept.append(e)
    return kept, dropped


def main():
    max_files = int(sys.argv[1]) if len(sys.argv) > 1 else MAX_FILES_DEFAULT
    html = HTML_PATH.read_text(encoding="utf-8")
    ucdp = parse_array(html, "EVENTS")
    existing = parse_array(html, "GDELT_EVENTS")

    ucdp_max = date.fromisoformat(max(e["d"] for e in ucdp))
    start = ucdp_max - timedelta(days=OVERLAP_DAYS)
    yesterday = date.today() - timedelta(days=1)
    print(f"UCDP data through {ucdp_max}; GDELT window {start} .. {yesterday}")

    st = load_state()
    window_days = []
    d = start
    while d <= yesterday:
        window_days.append(d.strftime("%Y%m%d"))
        d += timedelta(days=1)
    st["ingested"] = [x for x in st["ingested"] if x in window_days]
    st["missing"] = [x for x in st["missing"] if x in window_days]
    todo = [x for x in window_days if x not in st["ingested"] and x not in st["missing"]]

    fetched_events, done = [], 0
    for day in todo:
        if done >= max_files:
            break
        try:
            evs = fetch_day(day)
            fetched_events += evs
            st["ingested"].append(day)
            print(f"  {day}: {len(evs)} candidate rows")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                age = (date.today() - date(int(day[:4]), int(day[4:6]), int(day[6:8]))).days
                if age >= 3:
                    st["missing"].append(day)
                    print(f"  {day}: 404 (marked missing, will not retry)")
                else:
                    print(f"  {day}: not posted yet (normal for the most recent day)")
            else:
                print(f"  {day}: HTTP {e.code} — will retry next run")
        except Exception as e:
            print(f"  {day}: {e} — will retry next run")
        done += 1

    remaining = len([x for x in todo[done:]])
    # ---- merge, dedup, trim ----
    # GDELT daily files occasionally contain old (or future-miscoded) event
    # dates — a file is "events FOUND that day", not "events OF that day" —
    # so clamp BOTH stored and fresh events to the window.
    start_iso, end_iso = start.isoformat(), yesterday.isoformat()
    in_window = lambda e: start_iso <= e["d"] <= end_iso
    merged = [e for e in existing if in_window(e)]
    known_ids = {e["id"] for e in merged}
    merged += [e for e in fetched_events if in_window(e) and e["id"] not in known_ids]
    merged = collapse(merged)
    merged, dropped = drop_near_ucdp(merged, ucdp, start_iso)
    merged.sort(key=lambda e: (e["d"], e["s"], e["w"]))

    print(f"Layer: {len(merged)} events after collapse "
          f"({dropped} dropped as already in UCDP), window {start_iso} .. "
          f"{max((e['d'] for e in merged), default='n/a')}")

    # ---- rewrite HTML ----
    ev_json = embedded_json(merged)
    new_html = replace_json(html, "GDELT_EVENTS", ev_json)

    if merged:
        gmax = date.fromisoformat(max(e["d"] for e in merged)).strftime("%d %b %Y")
        sub_m = re.search(r'(<div class="sub">)(.*?)(</div>)', new_html)
        if sub_m:
            base = re.sub(r"\s*·\s*GDELT to [^<]*", "", sub_m.group(2))
            new_html = new_html.replace(sub_m.group(0),
                                        f"{sub_m.group(1)}{base} · GDELT to {gmax}{sub_m.group(3)}", 1)

    tmp = HTML_PATH.with_suffix(".html.tmp")
    tmp.write_text(new_html, encoding="utf-8")
    tmp.replace(HTML_PATH)
    save_state(st)
    print(f"Wrote {HTML_PATH} ({len(new_html):,} bytes)")
    print(f"REMAINING: {remaining}")


if __name__ == "__main__":
    main()
