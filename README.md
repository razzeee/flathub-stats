# Flathub Stats

Script to parse server logs into json files used for stats on flathub sites.

## Counting

Metadata is resolved across the whole input batch before dirtree requests are
counted. Delta and `.commit` requests identify historical commits; summaries
provide current-head mappings. Shared roots require unambiguous ref attribution.
Historical counts still depend on available metadata; unresolved roots are skipped.

These are request-based estimates, not unique users or confirmed installations:

- `downloads` includes updates and runtimes.
- `updates` counts events with an update-from header or an incremental delta.
- `delta_downloads` counts incremental deltas, excluding from-scratch deltas.
- Per-ref pairs are `[downloads_including_updates, updates]`.
- `downloads - updates` estimates non-update events, not unique new installs.
- OS and Flatpak-version totals cover only events supplying those fields.

The counters satisfy `0 <= delta_downloads <= updates <= downloads`. Summing
ref/architecture pairs reproduces the respective daily totals. Retries and
delta-to-object fallback can still inflate estimates.

## Running via devcontainer

- Check out repo
- Start in vscode and let it start the container
- Generate some test data via `uv run python generate-test-data.py`
- Run `uv run python update-stats.py test/test-data.log`
