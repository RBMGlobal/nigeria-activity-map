#!/usr/bin/env python3
"""
Update Nigeria_Events_Map.html with the latest UCDP data (annual GED + monthly Candidate).

- Reads API token from .ucdp_token in this script's directory
  (header: x-ucdp-access-token).
- Discovers the newest annual GED version (yy.1), then layers monthly
  Candidate versions (yy.0.m) covering months after the annual cutoff.
- Keeps a rolling WINDOW_MONTHS window (default 24 months).
- Rewrites the `const EVENTS = [...]` array, <title>, and the panel
  subtitle in Nigeria_Events_Map.html (one directory up).

Usage: python3 update_events_map.py   (no arguments; stdlib only)
"""
import json
import re
import sys
import urllib.request
import urllib.error
from datetime import date
from pathlib import Path
from embedded_data import replace_json

WINDOW_MONTHS = 24
COUNTRY = 475  # Nigeria, Gleditsch & Ward code
API = "https://ucdpapi.pcr.uu.se/api/gedevents/{version}"
PAGESIZE = 1000

SCRIPT_DIR = Path(__file__).resolve().parent
HTML_PATH = SCRIPT_DIR.parent / "Nigeria_Events_Map.html"
TOKEN_PATH = SCRIPT_DIR / ".ucdp_token"


def token():
    if not TOKEN_PATH.exists():
        sys.exit(f"ERROR: token file not found at {TOKEN_PATH}")
    return TOKEN_PATH.read_text().strip()


def api_get(url, tok):
    req = urllib.request.Request(url, headers={"x-ucdp-access-token": tok})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def version_exists(version, tok):
    try:
        api_get(API.format(version=version) + "?pagesize=1", tok)
        return True
    except urllib.error.HTTPError as e:
        if e.code in (400, 404):  # API returns 400 or 404 for unknown versions
            return False
        raise


def fetch_all(version, tok, start_date=None):
    """Fetch all Nigeria events for a dataset version, following pagination."""
    events, page = [], 0
    total_pages, total_count = None, None
    while True:
        url = API.format(version=version) + f"?pagesize={PAGESIZE}&page={page}&Country={COUNTRY}"
        if start_date:
            url += f"&StartDate={start_date}"
        d = api_get(url, tok)
        if not isinstance(d, dict) or not isinstance(d.get("Result"), list):
            raise ValueError("UCDP response lacks a complete Result array")
        pages = d.get("TotalPages")
        if type(pages) is not int or pages < 0:
            raise ValueError("UCDP response has invalid TotalPages")
        count = d.get("TotalCount")
        if count is not None and (type(count) is not int or count < 0):
            raise ValueError("UCDP response has invalid TotalCount")
        if total_pages is not None and (pages != total_pages or count != total_count):
            raise ValueError("UCDP pagination changed during collection; retry a complete snapshot")
        total_pages, total_count = pages, count
        batch = d["Result"]
        if pages == 0:
            if batch or count not in (None, 0):
                raise ValueError("UCDP empty pagination contradicts its records")
            return []
        if not batch:
            raise ValueError("UCDP pagination ended early; refusing partial data")
        events.extend(batch)
        page += 1
        if page >= pages:
            if total_count is not None and len(events) != total_count:
                raise ValueError("UCDP collected record count differs from TotalCount")
            break
    return events


def embedded_json(events):
    """Preserve data values without allowing source text to close a script tag."""
    return (json.dumps(events, ensure_ascii=False, separators=(", ", ": "))
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def window_start(today):
    """First day of the month, WINDOW_MONTHS months before today."""
    m = today.year * 12 + (today.month - 1) - WINDOW_MONTHS
    return date(m // 12, m % 12 + 1, 1)


MASKED = re.compile(r"^XXX\d+$")

# Free-text carried into the map so the keyword search can reach UCDP events.
# UCDP returns every reporting outlet's headline in one semicolon-joined string;
# the first few are enough to search on and short enough to show in the popup.
HEADLINE_CAP = 250


def headline_text(e):
    """Readable, capped headline string for search + popup display."""
    raw = (e.get("source_headline") or "").strip()
    # de-duplicate near-identical headlines from syndicating outlets
    seen, parts = set(), []
    for h in raw.split(";"):
        h = re.sub(r"\s+", " ", h).strip(" .")
        if not h:
            continue
        key = re.sub(r"[^a-z0-9]+", "", h.lower())[:60]
        if key in seen:
            continue
        seen.add(key)
        parts.append(h)
    text = " · ".join(parts)
    if len(text) > HEADLINE_CAP:
        cut = text.rfind(" · ", 0, HEADLINE_CAP)
        text = text[:cut] if cut > 60 else text[:HEADLINE_CAP].rstrip() + "…"
    return text


def compact(e):
    """Map a UCDP event to the map's compact format."""
    def side(name):
        name = (name or "").strip()
        return "Unconfirmed actor" if MASKED.match(name) else name
    return {
        "la": e["latitude"],
        "lo": e["longitude"],
        "d": str(e["date_start"])[:10],
        "de": str(e["date_end"])[:10],
        "t": e["type_of_violence"],
        "a": side(e.get("side_a")),
        "b": side(e.get("side_b")),
        "w": (e.get("where_coordinates") or "").strip(),
        "s": (e.get("adm_1") or "").strip(),
        "k": e.get("best", 0) or 0,
        "c": e.get("deaths_civilians", 0) or 0,
        "h": headline_text(e),
    }


def main():
    tok = token()
    today = date.today()
    start = window_start(today)
    print(f"Window: {start} .. {today}")

    # 1. Newest annual GED version (yy.1). Annual yy.1 covers through Dec of year (2000+yy-1).
    annual_yy = None
    for yy in range(today.year - 2000 + 1, 20, -1):
        if version_exists(f"{yy}.1", tok):
            annual_yy = yy
            break
    if annual_yy is None:
        sys.exit("ERROR: could not find any annual GED version")
    annual_ver = f"{annual_yy}.1"
    annual_end_year = 2000 + annual_yy - 1
    print(f"Annual GED: {annual_ver} (covers through Dec {annual_end_year})")

    events = [compact(e) for e in fetch_all(annual_ver, tok, start_date=start.isoformat())]
    print(f"  {len(events)} annual events in window")

    # 2. Candidate versions yy.0.m for months after the annual cutoff, up to today.
    cand_versions_used = []
    year = annual_end_year + 1
    while year <= today.year:
        cyy = year - 2000
        for m in range(1, 13):
            if date(year, m, 1) > today:
                break
            ver = f"{cyy}.0.{m}"
            try:
                batch = fetch_all(ver, tok)
            except urllib.error.HTTPError as e:
                if e.code in (400, 404):
                    continue  # not released yet
                raise
            events.extend(compact(x) for x in batch)
            cand_versions_used.append(ver)
            print(f"  Candidate {ver}: {len(batch)} events")
        year += 1

    # 3. Filter to window, sort by date.
    events = [e for e in events if e["d"] >= start.isoformat()]
    events.sort(key=lambda e: (e["d"], e["s"], e["w"]))
    if not events:
        sys.exit("ERROR: zero events after filtering — refusing to overwrite map")
    d_min, d_max = events[0]["d"], max(e["d"] for e in events)
    fat = sum(e["k"] for e in events)
    print(f"Total: {len(events)} events, {fat} best-est. fatalities, {d_min} .. {d_max}")

    # 4. Rewrite HTML.
    html = HTML_PATH.read_text(encoding="utf-8")

    ev_json = embedded_json(events)
    new_html = replace_json(html, "EVENTS", ev_json)

    def mon(iso):
        return date.fromisoformat(iso).strftime("%b %Y")

    if cand_versions_used:
        src = f"UCDP GED v{annual_ver} + Candidate to {mon(d_max)}"
    else:
        src = f"UCDP GED v{annual_ver}"
    sub = f"{src} · {mon(d_min)} – {mon(d_max)}"
    new_html = re.sub(r'(<div class="sub">).*?(</div>)', rf"\g<1>{sub}\g<2>", new_html, count=1)
    new_html = re.sub(r"<title>.*?</title>",
                      f"<title>Nigeria Conflict Events — {src}</title>", new_html, count=1)

    tmp = HTML_PATH.with_suffix(".html.tmp")
    tmp.write_text(new_html, encoding="utf-8")
    tmp.replace(HTML_PATH)
    print(f"Wrote {HTML_PATH} ({len(new_html):,} bytes)")
    print(f"Sources: annual {annual_ver}; candidate: {', '.join(cand_versions_used) or 'none'}")


if __name__ == "__main__":
    main()
