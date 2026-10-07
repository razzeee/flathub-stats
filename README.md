# Flathub Stats

Script to parse server logs into json files used for stats on flathub sites.

## Retry-safe ingestion

Pass closed, immutable logs to `update-stats.py`. The destination now contains
`.ingestion.sqlite3`, which records input identities, per-input contributions,
legacy baselines, and committed daily totals. Keep this database with the JSON
output when backing up or moving the statistics; exclude hidden operational
files from public JSON publishing.

Input identity is SHA-256 of decompressed bytes. Identical inputs are skipped
even when renamed or recompressed as gzip/xz. Byte-identical files are treated
as one input; overlapping or edited files have different identities and their
overlap is not deduplicated.

Each input is copied to a temporary, uncompressed file before parsing, requiring
temporary disk space for one decompressed log at a time. Content is checked
against its original fingerprint before and after parsing; a detected change
aborts the batch. Contributions and input identities commit together in SQLite.
Locks serialize writers, and JSON is exported atomically. Interrupted exports
are repaired on the next invocation without adding the inputs again.

Use `--reprocess` to replace a tracked input's contribution after recovering
metadata, or to change its `--ignore-deltas` setting:

```sh
uv run python update-stats.py --dest stats --reprocess old.log.xz
```

Supply the original decompressed bytes: editing a log creates a new identity.
Once tracked, daily JSON is an export of the database; editing it does not
change the stored totals. Publication is recoverable, but readers may see a
partially updated set of daily files while a batch is being exported.

### Existing deployments

Stop the old writer before deploying. Existing daily JSON is imported as a
baseline when first updated. Feed only new logs after this cutover: inputs
already included in the baseline have no ledger entries and cannot be recognized
as previously counted. `--reprocess` cannot replace contributions hidden in a
legacy baseline. For a full historical rebuild, use a fresh destination.

Do not delete the database while retaining exported JSON and then replay the
tracked logs: that would import their totals as a baseline and add them again.

## Running via devcontainer

- Check out repo
- Start in vscode and let it start the container
- Generate some test data via `uv run python generate-test-data.py`
- Run `uv run python update-stats.py test/test-data.log`
