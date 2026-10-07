# Flathub Stats

Script to parse server logs into json files used for stats on flathub sites.

Locks serialize writers sharing a cache or destination. JSON and cache files
are atomically replaced, so a failed write preserves the previous complete file.
This protects individual files; it does not make a batch of daily updates atomic
or prevent replaying an input from adding its counts again.

## Running via devcontainer

- Check out repo
- Start in vscode and let it start the container
- Generate some test data via `uv run python generate-test-data.py`
- Run `uv run python update-stats.py test/test-data.log`
