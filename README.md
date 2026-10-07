# Flathub Stats

Script to parse server logs into json files used for stats on flathub sites.

The parser yields events lazily, and the statistics script accumulates daily
counters directly instead of retaining all requests or repeatedly concatenating
lists. Memory use scales with aggregate dimensions and the metadata cache rather
than the number of download events. Callers needing a list can use
`list(flathub.parse_log(...))`.

## Running via devcontainer

- Check out repo
- Start in vscode and let it start the container
- Generate some test data via `uv run python generate-test-data.py`
- Run `uv run python update-stats.py test/test-data.log`
