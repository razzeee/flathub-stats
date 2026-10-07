# Tests

This directory contains tests for the flathub-stats project.

## Running Tests

```bash
# Run all tests
uv run --all-groups pytest tests/ -v

# Run with coverage
uv run --all-groups pytest tests/ -v --cov=. --cov-report=term-missing

# Run snapshot tests only
uv run --all-groups pytest tests/test_snapshots.py -v
```

## Snapshot Tests

The snapshot tests use [syrupy](https://github.com/tophat/syrupy) to verify the output JSON structure and data remain consistent.

### How They Work

Each test:
1. Generates test data using `generate-test-data.py` with a **specific seed** for reproducibility
2. Runs `update-stats.py` on the generated data
3. Processes it with deterministic, offline repository metadata
4. Compares the output JSON against a stored snapshot

Generated logs live in each test's temporary directory. Tests do not overwrite
`test/test-data.log`, and all metadata requests are mocked; live CDN availability
and changes in current repository contents cannot affect the results.

## Correctness and recovery tests

- `test_correctness.py`: the original gzip, replay, ordering, and negative-cache regressions.
- `test_counting.py`: independent expected totals, reporting coverage, malformed input,
  compressed input, historical commit discovery, ambiguous roots, and bounded retries.
- `test_ingestion.py`: input identity, replacement/reprocessing, legacy baselines,
  transaction rollback, interrupted publication, changing files, and concurrent writers.
- `test_process_logs.py`: batched rotation/archival, paths containing spaces,
  retry after processing failure, and preservation of existing archives.

Concurrency tests use Linux `fork` so child processes inherit the offline fixtures.
The original snapshot outputs are unchanged.

### Updating Snapshots

When you intentionally change the output format:

```bash
# Update all snapshots
uv run pytest tests/test_snapshots.py --snapshot-update

# Review the changes
git diff tests/__snapshots__/

# Commit if changes are expected
git add tests/__snapshots__/
git commit -m "Update snapshots for output format change"
```

### Reproducing Test Data

To manually reproduce the test data:

```bash
# Generate same data as seed 42 test
python generate-test-data.py --seed 42 --count 100

# Process it
python update-stats.py test/test-data.log --dest test-output
```
