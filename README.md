# Flathub Stats

Script to parse server logs into json files used for stats on flathub sites.

Input files can be plain text, gzip (`.gz`), or xz (`.xz`). Empty files are
accepted; malformed and undecodable lines are skipped without discarding
adjacent valid records.

## Running via devcontainer

- Check out repo
- Start in vscode and let it start the container
- Generate some test data via `uv run python generate-test-data.py`
- Run `uv run python update-stats.py test/test-data.log`
