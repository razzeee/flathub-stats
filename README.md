# Flathub Stats

Script to parse server logs into json files used for stats on flathub sites.

`process-logs.sh LOGDIR STATSDIR` rotates old logs, processes the pending batch,
then archives it. It requires Bash, `flock`, and `xz`; run it with `uv run` or
set `PYTHON` to the appropriate interpreter. Run as root when it needs to signal
rsyslog; otherwise the caller must arrange for the logger to close rotated files.
Failed processing leaves the pending logs available for retry, and existing
rotations and archives are preserved when a log pathname is reused.

## Running via devcontainer

- Check out repo
- Start in vscode and let it start the container
- Generate some test data via `uv run python generate-test-data.py`
- Run `uv run python update-stats.py test/test-data.log`
