# Events-page code review — 25 October 2026

The proposed client change escapes external incident metadata and permits only HTTP(S) source links without embedded credentials. Marker popups and tooltips now format on demand rather than thousands of times on each redraw. One Leaflet canvas renderer replaces the per-marker SVG elements. Counts, colours, source windows, heatmap weights and incident data are unchanged.

Validation: `node --test tests/*.test.cjs` covers inline JavaScript parsing, malicious source fields/link protocols and deferred marker content. A local browser render showed both incident layers and the report layer at the original counts. The three source arrays were compared byte-for-byte with the original revision. Browser/device interaction and performance profiling remain release checks; no latency percentage is claimed.

## Deployment dependency

This repository holds a generated page. The scheduled publisher currently reads and modifies the maintained local `Nigeria_Events_Map.html` in the Nigeria project. When releasing this PR, apply the same **client-only diff** to that maintained HTML, preserving its newest event arrays. Back it up first. Do not replace it with an older full HTML snapshot: that would regress data. This PR alone does not update the local publisher's input, and a later scheduled publish could otherwise undo the client fixes. Original ingestion, rendering and publishing scripts need no rewrite.

## Further improvements

- Separate the maintained client code from source snapshots with a reproducible build, so code fixes cannot be overwritten by scheduled publications.
- Add import/export contract fixtures shared with PED before extending supported data formats.
- Validate and escape the script-embedding boundary in ingestion: HTML-safe JSON serialization is distinct from popup escaping.
- Profile redraw costs on lower-powered mobile devices before implementing marker reuse or additional complexity.
