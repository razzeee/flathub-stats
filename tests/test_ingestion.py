import gzip
import json
import lzma
import multiprocessing
import runpy
import sqlite3
import sys
from pathlib import Path

import pytest

import flathub
import stats_store
from tests.helpers import COMMIT, DIRTREE, log_line, run_stats


def ledger_rows(dest):
    with sqlite3.connect(dest / ".ingestion.sqlite3") as db:
        return db.execute("SELECT digest FROM inputs").fetchall()


def test_renamed_and_compressed_copies_are_same_input(tmp_path, monkeypatch):
    original = tmp_path / "original.log"
    renamed = tmp_path / "renamed.log"
    compressed = tmp_path / "input.gz"
    archived = tmp_path / "input.xz"
    content = log_line().encode()
    original.write_bytes(content)
    renamed.write_bytes(content)
    compressed.write_bytes(gzip.compress(content))
    archived.write_bytes(lzma.compress(content))
    dest, cache = tmp_path / "stats", tmp_path / "cache.json"
    run_stats(monkeypatch, [original], dest, cache)
    data = run_stats(monkeypatch, [renamed, compressed, archived], dest, cache)
    assert data["downloads"] == 1
    assert len(ledger_rows(dest)) == 1


def test_reprocess_recovers_previously_unresolved_input(tmp_path, monkeypatch):
    dirtree = tmp_path / "dirtree.log"
    metadata = tmp_path / "metadata.log"
    dirtree.write_text(log_line(DIRTREE))
    metadata.write_text(log_line(f"/repo/objects/{COMMIT[:2]}/{COMMIT[2:]}.commit"))
    dest, cache = tmp_path / "stats", tmp_path / "cache.json"
    assert run_stats(monkeypatch, [dirtree], dest, cache) is None
    data = run_stats(monkeypatch, [dirtree, metadata], dest, cache, "--reprocess")
    assert data["downloads"] == 1
    assert run_stats(monkeypatch, [dirtree, metadata], dest, cache) == data


def test_reprocess_replaces_instead_of_accumulating(tmp_path, monkeypatch):
    path = tmp_path / "input.log"
    path.write_text(log_line())
    dest, cache = tmp_path / "stats", tmp_path / "cache.json"
    data = run_stats(monkeypatch, [path], dest, cache)
    assert run_stats(monkeypatch, [path], dest, cache, "--reprocess") == data
    with pytest.raises(ValueError, match="different --ignore-deltas"):
        run_stats(monkeypatch, [path], dest, cache, "--ignore-deltas")
    replaced = run_stats(
        monkeypatch, [path], dest, cache, "--reprocess", "--ignore-deltas"
    )
    assert replaced["downloads"] == 0
    assert replaced["refs"] == {}
    assert replaced["countries"] == {}


def test_existing_json_is_preserved_as_baseline(tmp_path, monkeypatch):
    path = tmp_path / "input.log"
    path.write_text(log_line())
    cache = tmp_path / "cache.json"
    baseline = run_stats(monkeypatch, [path], tmp_path / "old-stats", cache)
    dest = tmp_path / "stats"
    output = dest / "2023/05/16.json"
    output.parent.mkdir(parents=True)
    output.write_text(json.dumps(baseline))
    path.write_text(log_line(update="previous") * 2)
    data = run_stats(monkeypatch, [path], dest, cache)
    assert (data["downloads"], data["updates"]) == (3, 2)
    assert data["refs"]["org.example.App"]["x86_64"] == [3, 2]
    assert run_stats(monkeypatch, [path], dest, cache) == data
    assert (
        run_stats(monkeypatch, [path], dest, cache, "--ignore-deltas", "--reprocess")
        == baseline
    )


def test_transaction_failure_leaves_input_retryable(tmp_path, monkeypatch):
    path = tmp_path / "input.log"
    path.write_text(log_line())
    dest, cache = tmp_path / "stats", tmp_path / "cache.json"

    def fail(*args):
        raise OSError("simulated failure before commit")

    with monkeypatch.context() as patch:
        patch.setattr(stats_store.StatsStore, "rebuild_days", fail)
        with pytest.raises(OSError, match="before commit"):
            run_stats(patch, [path], dest, cache)
    assert ledger_rows(dest) == []
    assert not (dest / "2023/05/16.json").exists()
    assert run_stats(monkeypatch, [path], dest, cache)["downloads"] == 1


def test_interrupted_publication_recovers_without_recounting(tmp_path, monkeypatch):
    path = tmp_path / "input.log"
    path.write_text(log_line() + log_line().replace("16/May", "17/May"))
    dest, cache = tmp_path / "stats", tmp_path / "cache.json"
    write = stats_store.atomic_json_write

    def fail_after_replace(path, data):
        write(path, data)
        raise OSError("simulated crash before publication acknowledgement")

    with monkeypatch.context() as patch:
        patch.setattr(stats_store, "atomic_json_write", fail_after_replace)
        with pytest.raises(OSError, match="acknowledgement"):
            run_stats(patch, [path], dest, cache)
    assert len(ledger_rows(dest)) == 1

    def unexpected_cache_load(path):
        raise AssertionError("Recovery of published data must not need metadata")

    monkeypatch.setattr(flathub, "load_cache", unexpected_cache_load)
    assert run_stats(monkeypatch, [path], dest, cache)["downloads"] == 1
    assert json.loads((dest / "2023/05/17.json").read_text())["downloads"] == 1
    with sqlite3.connect(dest / ".ingestion.sqlite3") as db:
        assert db.execute("SELECT SUM(published) FROM days").fetchone()[0] == 2


def test_atomic_write_preserves_previous_json_on_failure(tmp_path, monkeypatch):
    path = tmp_path / "day.json"
    path.write_text('{"downloads": 7}')

    def fail(data, stream, **kwargs):
        stream.write('{"downloads":')
        raise OSError("simulated write failure")

    monkeypatch.setattr(stats_store.json, "dump", fail)
    with pytest.raises(OSError, match="write failure"):
        stats_store.atomic_json_write(path, {"downloads": 8})
    assert json.loads(path.read_text()) == {"downloads": 7}
    assert list(tmp_path.iterdir()) == [path]


def test_changing_input_rolls_back_entire_batch(tmp_path, monkeypatch):
    first, second = tmp_path / "first.log", tmp_path / "second.log"
    first.write_text(log_line())
    second.write_text(log_line(update="previous"))
    dest, cache = tmp_path / "stats", tmp_path / "cache.json"
    prepare = flathub.prepare_logs

    def mutate(paths, cache):
        prepare(paths, cache)
        second.write_text(log_line(update="previous") * 2)

    with monkeypatch.context() as patch:
        patch.setattr(flathub, "prepare_logs", mutate)
        with pytest.raises(ValueError, match="changed during processing"):
            run_stats(patch, [first, second], dest, cache)
    assert ledger_rows(dest) == []
    assert not (dest / "2023/05/16.json").exists()
    assert run_stats(monkeypatch, [first, second], dest, cache)["downloads"] == 3


def concurrent_worker(start, log, dest, cache):
    start.wait(5)
    sys.argv = [
        "update-stats.py",
        "--dest",
        str(dest),
        "--ref-cache",
        str(cache),
        str(log),
    ]
    runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "update-stats.py"),
        run_name="__main__",
    )


@pytest.mark.parametrize("same_input", [False, True])
def test_concurrent_writers_preserve_exact_totals(tmp_path, same_input):
    first, second = tmp_path / "first.log", tmp_path / "second.log"
    first.write_text(log_line())
    second.write_text(log_line() if same_input else log_line(update="previous"))
    dest = tmp_path / "stats"
    # Fork inherits the offline metadata fixture. Distinct caches ensure this
    # exercises the destination lock rather than only the shared-cache lock.
    context = multiprocessing.get_context("fork")
    start = context.Event()
    workers = [
        context.Process(
            target=concurrent_worker,
            args=(start, log, dest, tmp_path / f"cache-{i}.json"),
        )
        for i, log in enumerate([first, second])
    ]
    try:
        for worker in workers:
            worker.start()
        start.set()
        for worker in workers:
            worker.join(5)
            assert worker.exitcode == 0
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join()
    data = json.loads((dest / "2023/05/16.json").read_text())
    assert data["downloads"] == (1 if same_input else 2)
    assert data["updates"] == (0 if same_input else 1)
    assert len(ledger_rows(dest)) == (1 if same_input else 2)
