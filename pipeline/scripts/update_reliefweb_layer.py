#!/usr/bin/env python3
"""Fetch Nigeria-tagged ReliefWeb report metadata; preserve the map on any error.

Uses the read-only v2 API with the approved appname in .reliefweb.json.
Includes all reports with original publication dates in the last 90 days.
State links are conservative title matches on Nigeria-primary reports, not
incident geocoding. Regional/national reports without such links stay unlocated.
Only metadata is republished: titles, dates, organisations, themes and links.
Called by publish_map.py; can also run standalone. Stdlib only.
"""
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from embedded_data import load_json
from zoneinfo import ZoneInfo

from state_data import STATE_CENTROIDS

ROOT = Path(__file__).resolve().parent
HTML_PATH = ROOT.parent / "Nigeria_Events_Map.html"
CONFIG_PATH = ROOT / ".reliefweb.json"
START = "// BEGIN RELIEFWEB DATA"
END = "// END RELIEFWEB DATA"
FIELDS = ["title", "date", "source", "theme", "format", "url_alias", "url",
          "country.iso3", "primary_country.iso3"]


def title_states(title, primary_country):
    if primary_country != "nga":
        return []
    found = []
    for name in STATE_CENTROIDS:
        # Avoid Niger the country, Niger Delta the region, rivers and plateaus.
        if name in ("Niger", "Delta", "Rivers", "Plateau"):
            pattern = r"\b" + name + r"\s+State\b"
        elif name == "FCT":
            pattern = r"\b(?:FCT|Federal Capital Territory)\b"
        else:
            pattern = r"\b" + re.escape(name) + r"\b"
        if re.search(pattern, title, re.I):
            found.append(name)
    return sorted(found)


def safe_report_url(fields, report_id):
    for url in (fields.get("url_alias"), fields.get("url")):
        parsed = urllib.parse.urlparse(url or "")
        if parsed.scheme == "https" and parsed.netloc == "reliefweb.int":
            return url
    return "https://reliefweb.int/node/" + report_id


def compact(item, start, end):
    fields = item["fields"]
    report_id = str(item["id"])
    if not report_id.isdigit() or "nga" not in [c["iso3"] for c in fields.get("country", [])]:
        raise ValueError("Unexpected report ID/country in API response")
    title = " ".join(fields["title"].split())
    published = fields["date"]["original"][:10]
    datetime.strptime(published, "%Y-%m-%d")
    if not title or not start <= published <= end:
        raise ValueError("Unexpected title/publication date in API response")
    sources = [s.get("shortname") or s["name"] for s in fields.get("source", [])]
    source_names = [s["name"] for s in fields.get("source", [])]
    if not sources:
        raise ValueError("Report missing source attribution")
    primary = fields.get("primary_country", {}).get("iso3", "")
    return {"id": report_id, "d": published, "title": title,
            "sources": sources, "source_names": source_names,
            "themes": [s["name"] for s in fields.get("theme", [])],
            "formats": [s["name"] for s in fields.get("format", [])],
            "states": title_states(title, primary),
            "scope": "Nigeria" if primary == "nga" else "Regional / international",
            "u": safe_report_url(fields, report_id)}


def fetch_page(appname, start, end, offset):
    params = {
        "appname": appname, "limit": 1000, "offset": offset,
        "sort[0]": "date.original:desc", "sort[1]": "id:desc",
        "filter[operator]": "AND",
        "filter[conditions][0][field]": "country.iso3",
        "filter[conditions][0][value]": "nga",
        "filter[conditions][1][field]": "date.original",
        "filter[conditions][1][value][from]": start + "T00:00:00+00:00",
        "filter[conditions][1][value][to]": end + "T23:59:59+00:00",
    }
    params.update({f"fields[include][{i}]": field for i, field in enumerate(FIELDS)})
    url = "https://api.reliefweb.int/v2/reports?" + urllib.parse.urlencode(params)
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "RBMGlobal-Nigeria-Map/1.0"})
            with urllib.request.urlopen(request, timeout=45) as response:
                result = json.load(response)
            if result.get("error") or not isinstance(result.get("data"), list):
                raise ValueError("Invalid ReliefWeb response")
            return result
        except urllib.error.HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise
        time.sleep(2 ** attempt)


def fetch_reports(appname, start, end):
    reports = {}
    offset = 0
    total = None
    for _ in range(20):
        page = fetch_page(appname, start, end, offset)
        current_total = int(page["totalCount"])
        if total is not None and current_total != total:
            raise ValueError("ReliefWeb changed during pagination; retry for a complete snapshot")
        total = current_total
        batch = page["data"]
        if not batch:
            raise ValueError("Empty/incomplete ReliefWeb result; previous layer retained")
        for item in batch:
            report = compact(item, start, end)
            if report["id"] in reports:
                raise ValueError("Overlapping API pages; previous layer retained")
            reports[report["id"]] = report
        offset += len(batch)
        if offset >= total:
            if len(reports) != total:
                raise ValueError("Incomplete ReliefWeb snapshot")
            return sorted(reports.values(), key=lambda r: (r["d"], int(r["id"])), reverse=True)
    raise ValueError("ReliefWeb pagination limit reached; previous layer retained")


def js_json(value):
    # The API content is untrusted. Never allow an embedded closing script tag
    # or a literal line separator to escape the JSON data block.
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def refresh(force=False):
    html = HTML_PATH.read_text(encoding="utf-8")
    if html.count(START) != 1 or html.count(END) != 1:
        raise ValueError("ReliefWeb data markers missing or duplicated; map left unchanged")
    old_meta = load_json(html, "RELIEFWEB_META", dict, required=False)
    now = datetime.now(timezone.utc)
    if old_meta and not force:
        stamp = old_meta.get("fetched_at")
        if stamp and 0 <= (now - datetime.fromisoformat(stamp)).total_seconds() < 3600:
            print(f"ReliefWeb: recent snapshot retained ({old_meta['count']} reports)")
            return
    config = json.loads(CONFIG_PATH.read_text())
    appname = config["appname"].strip()
    days = int(config.get("window_days", 90))
    if not appname or not 1 <= days <= 730:
        raise ValueError("Invalid ReliefWeb configuration")
    today = now.astimezone(ZoneInfo("Europe/London")).date()
    start = (today - timedelta(days=days - 1)).isoformat()
    end = today.isoformat()
    reports = fetch_reports(appname, start, end)
    meta = {"fetched_at": now.isoformat(), "from": start, "to": end,
            "latest_publication": reports[0]["d"], "count": len(reports),
            "mapped_count": sum(bool(r["states"]) for r in reports), "window_days": days,
            "locations": {s: {"la": lat, "lo": lon} for s, (lon, lat) in STATE_CENTROIDS.items()},
            "location_method": "State explicitly named in title; approximate state reference point, not an incident location"}
    block = (START + "\nconst RELIEFWEB_REPORTS = " + js_json(reports) + ";\n"
             + "const RELIEFWEB_META = " + js_json(meta) + ";\n" + END)
    updated = re.sub(re.escape(START) + r".*?" + re.escape(END), lambda _: block, html, count=1, flags=re.S)
    # Avoid overwriting another updater's changes if they landed during the API call.
    if HTML_PATH.read_text(encoding="utf-8") != html:
        raise ValueError("Map changed during fetch; retry ReliefWeb update")
    tmp = HTML_PATH.with_suffix(".reliefweb.tmp")
    tmp.write_text(updated, encoding="utf-8")
    tmp.replace(HTML_PATH)
    print(f"ReliefWeb: {len(reports)} reports, {meta['mapped_count']} with title-based state links; "
          f"{start} .. {end}; latest publication {reports[0]['d']}")


if __name__ == "__main__":
    try:
        refresh(force="--force" in sys.argv)
    except Exception as error:
        sys.exit(f"ReliefWeb update failed; previous layer retained: {error}")
