import json
import multiprocessing
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import flathub
import stats_io


def log_line(update=""):
    return (
        '151.100.102.134 "-" "-" [16/May/2023:10:01:16 +0000] '
        f'"GET /repo/deltas/{"A" * 43}/superblock HTTP/1.1" 200 100 "" '
        '"libostree/2023.1 flatpak/1.14.0" '
        f'"app/org.example.App/x86_64/stable" "{update}" US "fedora;42"\n'
    )


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


def test_atomic_write_preserves_previous_json_on_failure(tmp_path, monkeypatch):
    path = tmp_path / "day.json"
    path.write_text('{"downloads": 7}')

    def fail(data, stream, **kwargs):
        stream.write('{"downloads":')
        raise OSError("simulated write failure")

    monkeypatch.setattr(stats_io.json, "dump", fail)
    with pytest.raises(OSError, match="write failure"):
        stats_io.atomic_json_write(path, {"downloads": 8})
    assert json.loads(path.read_text()) == {"downloads": 7}
    assert list(tmp_path.iterdir()) == [path]


def test_concurrent_writers_preserve_both_inputs(tmp_path, monkeypatch):
    monkeypatch.setattr(
        flathub,
        "load_cache",
        lambda path: SimpleNamespace(
            valid_arches={"x86_64"},
            update_from_summary=lambda ref: None,
            has_commit=lambda commit: True,
            save=lambda path: None,
        ),
    )
    first, second = tmp_path / "first.log", tmp_path / "second.log"
    first.write_text(log_line())
    second.write_text(log_line(update="previous"))
    dest = tmp_path / "stats"
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
    assert (data["downloads"], data["updates"]) == (2, 1)
