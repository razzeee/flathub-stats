# Flathub Stats

Parse Flathub CDN request logs into daily JSON files used by Flathub sites.

## Running

Install Python 3.10+ and the system development libraries needed by PyGObject,
then install the project dependencies:

```sh
uv sync --all-groups
uv run python generate-test-data.py --seed 42 --output test/test-data.log
uv run python update-stats.py --dest stats test/test-data.log
```

Pass one or more **closed, immutable** log files. Plain text, gzip (`.gz`), and
xz (`.xz`) are supported. Daily files retain the existing
`YYYY/MM/DD.json` schema. Metadata is fetched from `dl.flathub.org` and cached
in `ref-cache.json`; use `--ref-cache PATH` to choose a different cache.

`process-logs.sh LOGDIR STATSDIR` rotates old logs, processes the pending batch,
then archives it. It requires Bash, `flock`, and `xz`; run it with `uv run` or
set `PYTHON` to the appropriate interpreter. Run as root when it needs to signal
rsyslog; otherwise the caller must arrange for the logger to close rotated files.

## What the numbers mean

These are **request-based estimates**, not unique users, active installations,
or confirmed completed installs. A counted event is a successful HTTP `GET`
(`200`) for a delta superblock or a known root dirtree, attributed to a retained
app/runtime ref. Runtime `.Debug`, `.Locale`, and `.Sources` extensions are excluded.
App branches are combined by app ID; runtime branches remain separate.

- `downloads`: all counted events, **including updates and runtimes**.
- `updates`: counted events with an update-from header or an incremental delta.
- `delta_downloads`: incremental-delta events only. Full, from-scratch deltas
  count towards `downloads`, but not this field.
- Ref/architecture, ref/country, and ref/OS-version pairs are
  `[downloads_including_updates, updates]`.
- `downloads - updates` estimates non-update events, not unique new installs.
- OS and Flatpak-version dimensions include only events supplying those fields.
  Their totals can be smaller than `downloads`; their proportions describe the
  reporting subset, not necessarily all clients.
- Timestamps must be UTC (`+0000`). Missing libostree versions retain the legacy
  `2017.15` attribution.

The counters satisfy `0 <= delta_downloads <= updates <= downloads`.
Summing all ref/architecture download and update pairs reproduces the respective
daily totals. Requests are not deduplicated into client sessions: retries and
delta-to-object fallback can still inflate estimates.

## Retry safety and recovery

The destination contains a private `.ingestion.sqlite3` database with input
identities, per-input daily contributions, existing-data baselines, and committed
daily totals. **Keep and back up this database with the JSON output.** It is
operational state, not a public statistics file; exclude hidden files from web
serving or publishing.

- Input identity is SHA-256 of the **decompressed bytes**. Replaying, renaming,
  or recompressing an identical file skips it, including duplicates in one batch.
  Two byte-identical files are treated as the same input. Overlapping files or
  edited copies are different inputs; their overlapping requests are not deduplicated.
- A batch's contributions and input identities commit in one SQLite transaction.
  Changed input detected between fingerprinting and counting aborts the batch.
- Locks serialize writers sharing a cache or destination. JSON and cache files
  are atomically replaced; an interrupted publication is repaired at the start
  of the next invocation without recounting the inputs. Publishing multiple
  daily files is recoverable, but not an atomic multi-file snapshot for readers.
- Once tracked, a day's JSON is an export of the database. Editing that JSON
  does not change the stored totals.

To correct an already tracked input after recovering metadata:

```sh
uv run python update-stats.py --dest stats --reprocess old.log.xz related.log.xz
```

`--reprocess` replaces each supplied input's contribution. It does not add it
again. Supply the same decompressed bytes; a changed file has a different identity.
It can also replace a previous run's `--ignore-deltas` setting. A normal replay
with a conflicting setting fails rather than silently changing the totals.

### Existing deployments

Stop the old writer before deploying this version. Each existing daily JSON is
imported as a baseline when that day is first updated. **Only feed new logs after
the cutover:** historical inputs already included in that baseline have no ledger
entries and cannot be recognized as previously counted. `--reprocess` cannot
replace contributions hidden inside a legacy baseline. For a reproducible full
rebuild, use a fresh destination and the complete historical input set.

Do not delete the database while retaining the exported JSON and then replay
tracked logs: that would import their totals as a baseline and add them again.

## Metadata and data quality

Processing scans the whole pending batch for metadata before counting. Delta
and `.commit` requests identify commits, including historical commits; summaries
provide additional current-head mappings. The second pass streams events into
daily aggregates instead of retaining all requests in memory.

Metadata requests have a 15-second socket timeout and up to three attempts for
transient errors. Failed resolutions are suppressed for the rest of that run,
but do not become permanent negative cache entries. Successfully resolved
historical mappings are preserved. Ambiguous shared dirtrees require a ref
header for attribution.

Each processed file prints a `Quality` JSON object and stores it in the database's
`inputs.quality` column. Missing counters mean zero:

- `lines`, `counted`, `filtered`, `malformed`
- `unresolved_dirtrees`, `unresolved_refs`
- `missing_os`, `missing_flatpak_version` (subsets of counted events)

`lines = counted + filtered + malformed + unresolved_dirtrees + unresolved_refs`.
`filtered` includes unsuccessful requests, unrelated objects, excluded refs,
and deltas skipped by `--ignore-deltas`. Malformed records, invalid encoding,
invalid candidate checksums, and invalid/non-UTC timestamps are skipped.
The run also prints the number of failed commit resolutions.

Unresolved dirtrees include ordinary non-root trees, so that counter is **not**
an estimate of lost downloads. Reordering the same batch with the same starting
cache does not change counts. Complete historical attribution still requires
available metadata: processing related logs in separate runs can leave earlier
inputs unresolved until explicitly reprocessed. Current summaries alone cannot
reconstruct every old root dirtree.

## Tests

```sh
uv run --all-groups pytest tests/ -v
uv run ruff check .
uv run ruff format --check .
uv run ty check
```

Tests use offline metadata fixtures, exact counting assertions, recovery and
concurrency scenarios, and snapshots. See [tests/README.md](tests/README.md).

## Running via devcontainer

- Check out repo
- Start in vscode and let it start the container
- Generate some test data via `uv run python generate-test-data.py`
- Run `uv run python update-stats.py test/test-data.log`
