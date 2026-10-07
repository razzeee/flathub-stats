# Flathub Stats

Script to parse server logs into json files used for stats on flathub sites.

## Data-quality reports

Each input prints a `Quality` JSON object with these counters (missing keys mean zero):

- `lines`: decoded lines examined by the parser
- `counted`, `filtered`, `malformed`, `unresolved_dirtrees`, `unresolved_refs`
- `missing_os`, `missing_flatpak_version`: subsets of counted events

For completed parses, `lines = counted + filtered + malformed + unresolved_dirtrees
+ unresolved_refs`. Filtering includes unsuccessful requests, unrelated objects,
excluded refs, and deliberately ignored deltas. Invalid dates are reported as
malformed instead of aborting the entire batch. If parsing aborts, its partial
report is still printed.

Unresolved dirtrees include ordinary non-root trees, so that counter is not an
estimate of lost downloads. Missing OS/version fields describe reporting coverage,
not additional events. These diagnostics do not change the published JSON schema.

## Running via devcontainer

- Check out repo
- Start in vscode and let it start the container
- Generate some test data via `uv run python generate-test-data.py`
- Run `uv run python update-stats.py test/test-data.log`
