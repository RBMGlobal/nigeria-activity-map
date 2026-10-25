#!/usr/bin/env python3
"""
Render a static PNG of recent conflict events for the weekly threat brief.

Two panels since 2026-07-15:
  LEFT  — UCDP (curated): most recent 7 days of available UCDP data.
          UCDP lags ~5-6 weeks; titles state the real dates.
  RIGHT — GDELT (news-derived): the actual last 7 days from the GDELT layer
          (`const GDELT_EVENTS`), machine-coded from press reporting,
          deduplicated and cross-checked against UCDP by update_gdelt_layer.py.
          If the GDELT array is empty the script falls back to the original
          single-panel UCDP figure, so the brief never breaks.

- Parses both arrays from Nigeria_Events_Map.html (one directory up); run
  AFTER update_events_map.py and update_gdelt_layer.py.
- Basemap: Esri World Imagery tiles stitched once and cached in
  scripts/geodata/nigeria_sat_z8.png; falls back to Natural Earth vectors.
- Output: ../Nigeria_Events_Map_LastWeek_<newest-data-date>.png
  (build_brief.js picks the newest file matching that pattern).

Deps:  matplotlib (+ bundled Pillow). No geopandas.
Usage: python3 render_events_lastweek.py   (no arguments)
"""
import io
import json
import math
import re
import sys
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from embedded_data import load_json

import matplotlib.pyplot as plt
import matplotlib.patheffects as pe
from matplotlib.collections import LineCollection
from matplotlib.lines import Line2D

SCRIPT_DIR = Path(__file__).resolve().parent
HTML_PATH = SCRIPT_DIR.parent / "Nigeria_Events_Map.html"
_G50 = SCRIPT_DIR / "geodata" / "ne_50m_west_africa.geojson"
_G110 = SCRIPT_DIR / "geodata" / "ne_110m_admin_0_countries.geojson"
GEOJSON = _G50 if _G50.exists() else _G110
MOSAIC_PNG = SCRIPT_DIR / "geodata" / "nigeria_sat_z8.png"
MOSAIC_META = SCRIPT_DIR / "geodata" / "nigeria_sat_z8.json"

ZOOM = 8
TILE_URL = ("https://server.arcgisonline.com/ArcGIS/rest/services/"
            "World_Imagery/MapServer/tile/{z}/{y}/{x}")
ATTRIBUTION = "Imagery: Esri World Imagery (Esri, Maxar, Earthstar Geographics)"

VIEW = (2.0, 3.6, 15.3, 14.5)  # minlon, minlat, maxlon, maxlat

# Palettes mirror the interactive map: warm = UCDP curated, cool = GDELT news.
TYPE = {
    1: ("State-based conflict", "#ff3b30"),
    2: ("Non-state conflict", "#ff9500"),
    3: ("One-sided violence", "#bf5af2"),
}
GTYPE = {
    14: ("Protest", "#ffd60a"),
    18: ("Assault", "#64d2ff"),
    19: ("Armed clash", "#0a84ff"),
    20: ("Mass violence", "#5e5ce6"),
}

NEIGHBORS = {"Niger", "Chad", "Cameroon", "Benin", "Equatorial Guinea"}

CITIES = {  # (lon, lat) — orientation reference
    "Maiduguri": (13.16, 11.85), "Kano": (8.52, 12.00), "Abuja": (7.49, 9.06),
    "Lagos": (3.38, 6.52), "Port Harcourt": (7.01, 4.82), "Jos": (8.89, 9.92),
    "Sokoto": (5.24, 13.06),
}

R = 6378137.0  # Web Mercator sphere radius


def merc(lon, lat):
    x = math.radians(lon) * R
    y = math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * R
    return x, y


def tile_bounds_merc(z, x, y):
    n = 2 ** z
    world = 2 * math.pi * R
    x0 = -world / 2 + x / n * world
    y1 = world / 2 - y / n * world
    return x0, y1 - world / n, x0 + world / n, y1


def lonlat_to_tile(lon, lat, z):
    n = 2 ** z
    xt = int((lon + 180) / 360 * n)
    lat_r = math.radians(lat)
    yt = int((1 - math.log(math.tan(lat_r) + 1 / math.cos(lat_r)) / math.pi) / 2 * n)
    return xt, yt


def build_mosaic():
    from PIL import Image
    minlon, minlat, maxlon, maxlat = VIEW
    x0, y1 = lonlat_to_tile(minlon, minlat, ZOOM)
    x1, y0 = lonlat_to_tile(maxlon, maxlat, ZOOM)
    cols, rows = x1 - x0 + 1, y1 - y0 + 1
    print(f"Fetching {cols * rows} satellite tiles (z{ZOOM})...")
    mosaic = Image.new("RGB", (cols * 256, rows * 256))

    def fetch(xy):
        xt, yt = xy
        url = TILE_URL.format(z=ZOOM, x=xt, y=yt)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return xy, Image.open(io.BytesIO(r.read())).convert("RGB")

    from concurrent.futures import ThreadPoolExecutor
    coords = [(xt, yt) for xt in range(x0, x1 + 1) for yt in range(y0, y1 + 1)]
    with ThreadPoolExecutor(max_workers=12) as ex:
        for (xt, yt), tile in ex.map(fetch, coords):
            mosaic.paste(tile, ((xt - x0) * 256, (yt - y0) * 256))
    minx = tile_bounds_merc(ZOOM, x0, y0)[0]
    maxy = tile_bounds_merc(ZOOM, x0, y0)[3]
    maxx = tile_bounds_merc(ZOOM, x1, y1)[2]
    miny = tile_bounds_merc(ZOOM, x1, y1)[1]
    mosaic.save(MOSAIC_PNG)
    MOSAIC_META.write_text(json.dumps(
        {"extent_merc": [minx, miny, maxx, maxy], "zoom": ZOOM,
         "source": ATTRIBUTION}))
    print(f"Cached mosaic {mosaic.size} -> {MOSAIC_PNG}")


def load_mosaic():
    if not (MOSAIC_PNG.exists() and MOSAIC_META.exists()):
        try:
            build_mosaic()
        except Exception as e:
            print(f"WARNING: satellite mosaic unavailable ({e}); "
                  f"falling back to vector basemap")
            return None
    meta = json.loads(MOSAIC_META.read_text())
    img = plt.imread(MOSAIC_PNG)
    return img, meta["extent_merc"]


def load_events():
    html = HTML_PATH.read_text(encoding="utf-8")
    events = load_json(html, "EVENTS")
    gdelt = load_json(html, "GDELT_EVENTS", required=False) or []
    sub = re.search(r'<div class="sub">(.*?)</div>', html)
    source = (sub.group(1) if sub else "UCDP GED").split("·")[0].strip()
    return events, gdelt, source


def country_rings(feature):
    geom = feature["geometry"]
    if geom["type"] == "Polygon":
        return [geom["coordinates"][0]]
    if geom["type"] == "MultiPolygon":
        return [poly[0] for poly in geom["coordinates"]]
    return []


def rings_to_merc(rings):
    return [[merc(x, y) for x, y in ring] for ring in rings]


def draw_base(ax, sat, nigeria_rings, neighbor_rings):
    """Basemap + borders + reference cities; returns (label_fx, on_sat)."""
    if sat is not None:
        img, (mnx, mny, mxx, mxy) = sat
        ax.imshow(img, extent=(mnx, mxx, mny, mxy), origin="upper",
                  interpolation="bilinear", zorder=0)
        label_fx = [pe.withStroke(linewidth=2.5, foreground="black")]
        border_col, city_col = "#FFD60A", "white"
    else:
        ax.set_facecolor("#F7FBFF")
        from matplotlib.collections import PolyCollection
        ax.add_collection(PolyCollection(
            neighbor_rings, facecolors="#EFEFEF", edgecolors="#BBBBBB",
            linewidths=0.6))
        ax.add_collection(PolyCollection(
            nigeria_rings, facecolors="#FFFFFF", edgecolors="none"))
        label_fx = [pe.withStroke(linewidth=2.5, foreground="white")]
        border_col, city_col = "#333333", "#333333"

    ax.add_collection(LineCollection(
        neighbor_rings, colors=border_col, linewidths=0.5, alpha=0.5, zorder=3))
    ax.add_collection(LineCollection(
        nigeria_rings, colors=border_col, linewidths=1.8, zorder=3))

    for name, (lon, lat) in CITIES.items():
        x, y = merc(lon, lat)
        ax.plot(x, y, marker="s", markersize=3.5, color=city_col, zorder=4)
        ax.annotate(name, (x, y), xytext=(4, 4), textcoords="offset points",
                    fontsize=7, color=city_col, path_effects=label_fx, zorder=4)

    vx0, vy0 = merc(VIEW[0], VIEW[1])
    vx1, vy1 = merc(VIEW[2], VIEW[3])
    ax.set_xlim(vx0, vx1)
    ax.set_ylim(vy0, vy1)
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)
    return label_fx, sat is not None


def caveat(ax, text, label_fx, on_sat):
    ax.text(0.99, 0.01, text, transform=ax.transAxes, ha="right", va="bottom",
            fontsize=6.5, color="white" if on_sat else "#555555",
            style="italic", path_effects=label_fx)


FMT = "%d %b %Y"


def draw_ucdp_panel(ax, events, source, sat, nrings, brings):
    label_fx, on_sat = draw_base(ax, sat, nrings, brings)
    max_d = date.fromisoformat(max(e["d"] for e in events))
    start = max_d - timedelta(days=6)
    week = [e for e in events if start.isoformat() <= e["d"] <= max_d.isoformat()]
    kills = sum(e["k"] for e in week)
    civ = sum(e["c"] for e in week)
    print(f"UCDP window {start} .. {max_d}: {len(week)} events, "
          f"{kills} best-est. fatalities ({civ} civilian)")

    for t in (1, 2, 3):
        pts = [e for e in week if e["t"] == t]
        if not pts:
            continue
        xy = [merc(e["lo"], e["la"]) for e in pts]
        ax.scatter(
            [p[0] for p in xy], [p[1] for p in xy],
            s=[max(30, 22 * (e["k"] ** 0.5) + 30) for e in pts],
            c=TYPE[t][1], alpha=0.85, edgecolors="white", linewidths=0.9, zorder=5,
        )
    for e in sorted(week, key=lambda x: -x["k"])[:6]:
        if e["k"] <= 0:
            continue
        x, y = merc(e["lo"], e["la"])
        ax.annotate(
            f"{e['w'] or e['s']} ({e['k']})", (x, y),
            xytext=(6, 6), textcoords="offset points",
            fontsize=7.5, color="white" if on_sat else "#222222",
            fontweight="bold", path_effects=label_fx, zorder=6,
        )

    ax.set_title(
        f"UCDP (curated) — latest available week "
        f"({start.strftime(FMT)} – {max_d.strftime(FMT)})\n"
        f"{len(week)} events · {kills} best-est. fatalities ({civ} civilian) · {source}",
        fontsize=10.5, fontweight="bold", pad=10,
    )
    handles = [
        Line2D([], [], marker="o", linestyle="", markerfacecolor=c,
               markeredgecolor="white", markersize=9, label=lbl)
        for lbl, c in TYPE.values()
    ]
    handles.append(Line2D([], [], marker="o", linestyle="", markerfacecolor="#999999",
                          markeredgecolor="white", markersize=9,
                          label="Marker size = fatalities"))
    leg = ax.legend(handles=handles, loc="upper left", fontsize=8, frameon=True,
                    title="UCDP type of violence", title_fontsize=7.8)
    leg.get_frame().set_alpha(0.9)
    caveat(ax, "UCDP GED/Candidate data lags ~5-6 weeks; this panel is the most "
               "recent week of recorded data, not the current calendar week.",
           label_fx, on_sat)
    return max_d


def draw_gdelt_panel(ax, gdelt, sat, nrings, brings):
    label_fx, on_sat = draw_base(ax, sat, nrings, brings)
    gmax = date.fromisoformat(max(e["d"] for e in gdelt))
    gstart = gmax - timedelta(days=6)
    week = [e for e in gdelt if gstart.isoformat() <= e["d"] <= gmax.isoformat()]
    arts = sum(e["n"] for e in week)
    print(f"GDELT window {gstart} .. {gmax}: {len(week)} events, {arts} articles")

    for r_code in (14, 18, 19, 20):
        pts = [e for e in week if e["r"] == r_code]
        if not pts:
            continue
        xy = [merc(e["lo"], e["la"]) for e in pts]
        ax.scatter(
            [p[0] for p in xy], [p[1] for p in xy],
            s=[min(240, 14 * (e["n"] ** 0.5) + 18) for e in pts],
            c=GTYPE[r_code][1], alpha=0.75, edgecolors="white",
            linewidths=0.7, zorder=5,
        )
    for e in sorted(week, key=lambda x: -x["n"])[:6]:
        x, y = merc(e["lo"], e["la"])
        ax.annotate(
            f"{e['w'] or e['s']}", (x, y),
            xytext=(6, 6), textcoords="offset points",
            fontsize=7.5, color="white" if on_sat else "#222222",
            fontweight="bold", path_effects=label_fx, zorder=6,
        )

    ax.set_title(
        f"GDELT (news-derived, unverified) — actual last 7 days "
        f"({gstart.strftime(FMT)} – {gmax.strftime(FMT)})\n"
        f"{len(week)} deduplicated events · marker size = press coverage",
        fontsize=10.5, fontweight="bold", pad=10,
    )
    handles = [
        Line2D([], [], marker="o", linestyle="", markerfacecolor=c,
               markeredgecolor="white", markersize=9, label=lbl)
        for lbl, c in GTYPE.values()
    ]
    leg = ax.legend(handles=handles, loc="upper left", fontsize=8, frameon=True,
                    title="GDELT CAMEO category", title_fontsize=7.8)
    leg.get_frame().set_alpha(0.9)
    caveat(ax, "GDELT machine-codes press reports; locations may be state/LGA "
               "centroids and events are not fatality-verified.  " + ATTRIBUTION,
           label_fx, on_sat)
    return gmax


def main():
    events, gdelt, source = load_events()
    if not events:
        sys.exit("ERROR: EVENTS array is empty")

    gj = json.loads(GEOJSON.read_text(encoding="utf-8"))
    nrings, brings = [], []
    for f in gj["features"]:
        name = f["properties"].get("NAME")
        if name == "Nigeria":
            nrings = rings_to_merc(country_rings(f))
        elif name in NEIGHBORS:
            brings.extend(rings_to_merc(country_rings(f)))
    if not nrings:
        sys.exit("ERROR: Nigeria not found in basemap GeoJSON")

    sat = load_mosaic()
    if gdelt:
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(21, 10))
        ucdp_max = draw_ucdp_panel(ax1, events, source, sat, nrings, brings)
        gdelt_max = draw_gdelt_panel(ax2, gdelt, sat, nrings, brings)
        newest = max(ucdp_max, gdelt_max)
        dpi = 160
    else:
        print("NOTE: GDELT layer empty — rendering single UCDP panel")
        fig, ax1 = plt.subplots(figsize=(11, 10))
        newest = draw_ucdp_panel(ax1, events, source, sat, nrings, brings)
        dpi = 200

    # Stable name (overwritten daily; served on the website). The dated copy —
    # which build_brief.js requires — is only written on Mondays, so daily runs
    # don't accumulate ~4 MB files (deletions are blocked in this folder).
    out = SCRIPT_DIR.parent / "Nigeria_Events_Map_LastWeek_latest.png"
    plt.tight_layout()
    plt.savefig(out, dpi=dpi, bbox_inches="tight")
    print(f"Saved {out}")
    # Mondays: also write the dated copy build_brief.js needs. The map task
    # runs daily ~11:30 and the weekly brief follows Mondays ~12:30, so the
    # brief embeds a PNG with GDELT data through Sunday.
    if date.today().weekday() == 0:
        dated = SCRIPT_DIR.parent / f"Nigeria_Events_Map_LastWeek_{newest.isoformat()}.png"
        dated.write_bytes(out.read_bytes())
        print(f"Saved {dated} (for today's weekly brief)")


if __name__ == "__main__":
    main()
