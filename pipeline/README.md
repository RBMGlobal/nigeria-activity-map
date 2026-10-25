# Hosted events build

This package builds the existing RBM Nigeria events map independently of a desktop
application. It does not commit, push, deploy, change polling assessments or write
weekly editorial conclusions. The publication workflow supplies those separate
commit/deployment/verification steps.

## Runtime and inputs

- Ubuntu runner, Python 3.12, pinned packages in `requirements.txt`.
- `index.html` is both the complete public application and the incremental event
  store. `pipeline/state/gdelt.json` contains processed daily input dates.
  `pipeline/state/seed.json` binds those two files by SHA-256. Commit them together.
- The existing rendering scripts and four cached geodata files are included. Set
  `MPLBACKEND=Agg`; no display server, browser or Mac installation is needed.
- Required repository Actions secret: `UCDP_TOKEN`. It is materialised only in a
  disposable workspace as `.ucdp_token` with mode 0600, then removed immediately
  after UCDP collection. No local GitHub token or other credential is copied.
- ReliefWeb uses the existing approved nonsecret app name in
  `scripts/.reliefweb.json`. GDELT needs no credential.
- Build subprocesses receive an environment allowlist; GitHub/Actions tokens and
  UCDP_TOKEN are not inherited. Only the UCDP script can read the temporary file.

## Commands

From the repository root:

```sh
python -m pip install -r pipeline/requirements.txt
python -m unittest discover -s pipeline/tests -p 'test_*.py' -v
python pipeline/build_release.py --check-seed
python pipeline/build_release.py --output build
```

The normal build requires UCDP_TOKEN in its environment. It runs UCDP, GDELT,
optional ReliefWeb, rendering, the existing validator and heat preparation in that
order. Every required GDELT daily input from UCDP's maximum event date minus seven
days through yesterday in Europe/London must be accounted for. A maximum event date
does not prove input coverage; a daily file may contain no matching events.

`--rebuild-from-seed` performs a real offline render/heat build using the committed
sources. It still requires complete input coverage through yesterday, and the
release is explicitly labelled `retained-source-rebuild`. It never refreshes or
redates source evidence. Use a fresh output directory for every build.

`--accepted-release` verifies and stages an already committed release for redeploy.
It does not collect or render, preserves release/source timestamps and produces an
empty commit manifest. It accepts an older last-good release according to its own
published source window, permitting recovery after a commit succeeded but Pages
deployment failed.

## Output contract

The new output directory appears only after every mandatory step passes:

- `site/`: complete public site. Only index.html, lastweek.png,
  sentiment-gdelt.json, release.json, .nojekyll and retained archive/maps pairs.
- `repository/`: exact changed source/publication files for one atomic commit.
- `commit-manifest.json`: schema_version 1 and a `files` object whose keys are
  repository-relative paths, each with `sha256` and `bytes`.

Normal commits contain these six paths:

```text
index.html
lastweek.png
sentiment-gdelt.json
release.json
pipeline/state/gdelt.json
pipeline/state/seed.json
```

Monday adds `archive/maps/YYYY-MM-DD.png` and `.json`. The filename is the London
capture date; its metadata separately records the actual seven-day UCDP and GDELT
panel windows and GDELT input-through date. A successful Monday capture is immutable;
retries retain it. All existing archive pairs are included in every deployment.

`release.json` includes schema_version 1, generated_at (UTC), build mode, warnings,
coverage.ucdp min/max event dates, coverage.gdelt input_from/input_through and min/max
event dates, coverage.heat from/to and hashes/byte sizes for all three public assets.
The company repository can fetch release → heat → release, check stable manifest
and hash/window equality, then update only its reporting heat file.

## Failure and publication rules

The checkout and prior publication are never modified by the builder. No partial
output directory survives a mandatory stage failure. A failed optional ReliefWeb
refresh restores the exact preceding report snapshot and records a warning without
advancing its source dates. The old PNG is never copied into the build workspace,
so a missing renderer output cannot masquerade as success. The new PNG is decoded
fully and heat is checked against the actual full GDELT array.

Publish only after a non-force atomic commit of the complete manifest succeeds.
Refuse to overwrite a concurrently changed branch. Deploy `build/site`, never the
repository root or temporary workspace. A source commit is not evidence of live
success: verify GitHub's deployment result, stable live release and exact bytes of
the three public files. Reruns must attempt deployment even if no Git diff remains.

App-only edits to index.html also change its binding hash. Before intentionally
rebinding, verify that both embedded event arrays still match the accepted source
state; never rebind unrelated state merely to suppress a validation error.

## Provenance and bounded port changes

`source-provenance.json` records original and packaged SHA-256 values. Event
selection, UCDP version discovery, rolling windows, CAMEO filters, geographic
filters, deduplication, report classification and rendering remain the same.

The staged UCDP collector rejects malformed/truncated/changing pagination rather
than silently accepting a partial response. UCDP and GDELT JSON output escapes HTML
script delimiters without changing decoded data. GDELT uses HTTPS for the same
daily archives (both endpoints returned 200 for 24 October during setup). The heat
script's publisher import and --publish branch are removed; aggregation is unchanged.
Embedded data extraction and replacement now use a shared JSON decoder across collectors, renderer, validator and builder. Literal `];` or `};` in a source string cannot truncate the value. Declaration uniqueness and terminating semicolons are checked; JSON values, collection rules and surrounding HTML are preserved. The helper is included in each isolated build workspace. The provenance register identifies these bounded parser changes; other copied scripts remain byte-identical to their source versions.

The satellite mosaic retains its Esri/Maxar/Earthstar attribution in metadata and
rendered output. Natural Earth vector files remain unchanged. These files are
existing project rendering inputs; this migration does not change their licensing
or grant additional reuse rights.

## Verification performed before deployment

29 offline regression tests passed locally. A real Agg render/validation/heat build
from the existing seed passed without ingestion or publication: 2,291 UCDP records
through 30 September 2026; 1,605 GDELT records with all 32 daily inputs from
23 September through 24 October; heat 1,331 records/279 cells through 24 October.
The complete two-panel PNG was visually inspected. Hosted Linux execution and live
UCDP authentication still require the first GitHub Actions run and its deployment
verification; local offline success is not a claim that those completed.
