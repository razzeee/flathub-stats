import json
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import flathub


def log_line(update=""):
    return (
        '151.100.102.134 "-" "-" [16/May/2023:10:01:16 +0000] '
        f'"GET /repo/deltas/{"A" * 43}/superblock HTTP/1.1" 200 100 "" '
        '"libostree/2023.1 flatpak/1.14.0" '
        f'"app/org.example.App/x86_64/stable" "{update}" US "fedora;42"\n'
    )


def cached_commit():
    return SimpleNamespace(
        valid_arches={"x86_64"},
        update_from_summary=lambda ref: None,
        has_commit=lambda commit: True,
        save=lambda path: None,
    )


def test_first_event_does_not_parse_rest_of_file(tmp_path, monkeypatch):
    path = tmp_path / "input.log"
    path.write_text(log_line() * 2)
    parse_date = flathub.time.strptime
    calls = 0

    def stop_at_second_record(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("second record reached")
        return parse_date(*args)

    monkeypatch.setattr(flathub.time, "strptime", stop_at_second_record)
    events = flathub.parse_log(str(path), cached_commit())
    assert next(events)[flathub.DATE] == "2023/05/16"
    with pytest.raises(RuntimeError, match="second record reached"):
        next(events)


def test_empty_log_has_no_events(tmp_path):
    path = tmp_path / "empty.log"
    path.write_text("")
    assert list(flathub.parse_log(str(path), cached_commit())) == []


def test_streamed_files_preserve_daily_totals(tmp_path, monkeypatch):
    first, second = tmp_path / "first.log", tmp_path / "second.log"
    first.write_text(log_line() * 100)
    second.write_text(log_line(update="previous") * 50)
    dest = tmp_path / "stats"
    monkeypatch.setattr(flathub, "load_cache", lambda path: cached_commit())
    monkeypatch.setattr(
        sys, "argv", ["update-stats.py", "--dest", str(dest), str(first), str(second)]
    )
    runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "update-stats.py"),
        run_name="__main__",
    )
    data = json.loads((dest / "2023/05/16.json").read_text())
    assert (data["downloads"], data["updates"]) == (150, 50)
    assert data["refs"] == {"org.example.App": {"x86_64": [150, 50]}}
