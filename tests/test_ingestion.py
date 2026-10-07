import gzip
import base64
import io
import json
import lzma
import multiprocessing
import runpy
import sqlite3
import sys
from pathlib import Path

import pytest
from gi.repository import GLib  # type: ignore[import-untyped]

import flathub
import stats_store

COMMIT = "a" * 64
ROOT = "b" * 64
REF = "app/org.example.App/x86_64/stable"
DELTA = base64.b64encode(bytes.fromhex(COMMIT)).decode().rstrip("=").replace("/", "_")
DIRTREE = f"/repo/objects/{ROOT[:2]}/{ROOT[2:]}.dirtree"


def log_line(path=f"/repo/deltas/{DELTA}/superblock", update=""):
    return (
        '151.100.102.134 "-" "-" [16/May/2023:10:01:16 +0000] '
        f'"GET {path} HTTP/1.1" 200 100 "" "libostree/2023.1 flatpak/1.14.0" '
        f'"{REF}" "{update}" US "fedora;42"\n'
    )


@pytest.fixture(autouse=True)
def offline_metadata(monkeypatch):
    index = (
        GLib.Variant(
            "(a{s(ayaaya{sv})}a{sv})",
            ({"x86_64": ([], [], {})}, {}),
        )
        .get_data_as_bytes()
        .get_data()
    )
    summary = (
        GLib.Variant(
            "(a(s(taya{sv}))a{sv})",
            ([], {}),
        )
        .get_data_as_bytes()
        .get_data()
    )
    commit = (
        GLib.Variant(
            "(a{sv}aya(say)sstayay)",
            (
                {"xa.ref": GLib.Variant("s", REF)},
                [],
                [],
                "",
                "",
                0,
                list(bytes.fromhex(ROOT)),
                [],
            ),
        )
        .get_data_as_bytes()
        .get_data()
    )

    def urlopen(url, **kwargs):
        if url.endswith("/summary.idx"):
            return io.BytesIO(index)
        if url.endswith("/summary"):
            return io.BytesIO(summary)
        return io.BytesIO(commit)

    monkeypatch.setattr(flathub.urllib.request, "urlopen", urlopen)


def run_stats(monkeypatch, paths, dest, cache, *options):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "update-stats.py",
            "--dest",
            str(dest),
            "--ref-cache",
            str(cache),
            *options,
            *map(str, paths),
        ],
    )
    runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "update-stats.py"),
        run_name="__main__",
    )
    path = dest / "2023/05/16.json"
    return json.loads(path.read_text()) if path.exists() else None


def ledger_rows(dest):
    with sqlite3.connect(dest / ".ingestion.sqlite3") as db:
        return db.execute("SELECT digest FROM inputs").fetchall()


@pytest.mark.parametrize(
    "suffix,compress",
    [
        (".log", lambda data: data),
        (".gz", gzip.compress),
        (".xz", lzma.compress),
    ],
)
def test_first_ingestion_and_replay(tmp_path, monkeypatch, suffix, compress):
    path = tmp_path / f"input{suffix}"
    path.write_bytes(compress(log_line().encode()))
    dest, cache = tmp_path / "stats", tmp_path / "cache.json"
    first = run_stats(monkeypatch, [path], dest, cache)
    assert first["downloads"] == 1
    assert run_stats(monkeypatch, [path], dest, cache) == first


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
    dirtree.write_text(log_line(DIRTREE))
    dest, cache = tmp_path / "stats", tmp_path / "cache.json"
    assert run_stats(monkeypatch, [dirtree], dest, cache) is None
    cache.write_text(json.dumps({COMMIT: [REF, ROOT]}))
    data = run_stats(monkeypatch, [dirtree], dest, cache, "--reprocess")
    assert data["downloads"] == 1
    assert run_stats(monkeypatch, [dirtree], dest, cache) == data


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
    parse = flathub.parse_log

    def mutate(path, cache, ignore_deltas=False):
        second.write_text(log_line(update="previous") * 2)
        return parse(path, cache, ignore_deltas)

    with monkeypatch.context() as patch:
        patch.setattr(flathub, "parse_log", mutate)
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
