# Flathub Stats

Script to parse server logs into json files used for stats on flathub sites.

Metadata requests have a 15-second socket timeout and up to three attempts for
transient errors. Failed resolutions are suppressed for the rest of that run,
but do not become permanent negative cache entries. Successfully resolved
historical mappings are preserved, including legacy ref-only cache entries.

## Running via devcontainer

- Check out repo
- Start in vscode and let it start the container
- Generate some test data via `uv run python generate-test-data.py`
- Run `uv run python update-stats.py test/test-data.log`
