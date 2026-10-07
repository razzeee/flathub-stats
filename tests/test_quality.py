import json
import runpy
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

import flathub

DELTA = "A" * 43
COMMIT = "0" * 64
ROOT = "b" * 64
DIRTREE = f"/repo/objects/{ROOT[:2]}/{ROOT[2:]}.dirtree"


def log_line(path=f"/repo/deltas/{DELTA}/superblock", update="", os_info="fedora;42"):
    return (
        '151.100.102.134 "-" "-" [16/May/2023:10:01:16 +0000] '
        f'"GET {path} HTTP/1.1" 200 100 "" "libostree/2023.1 flatpak/1.14.0" '
        f'"app/org.example.App/x86_64/stable" "{update}" US "{os_info}"\n'
    )


def cached_commit():
    return SimpleNamespace(
        valid_arches={"x86_64"},
        update_from_summary=lambda ref: None,
        has_commit=lambda commit: True,
        save=lambda path: None,
        lookup_by_dirtree=lambda root: COMMIT if root == ROOT else None,
        lookup_ref=lambda commit: None,
    )


def test_quality_counters_reconcile_with_counted_events(tmp_path, monkeypatch, capsys):
    path = tmp_path / "input.log"
    path.write_text(
        log_line()
        + log_line(update="previous")
        + log_line(f"/repo/deltas/{DELTA}-{DELTA}/superblock")
        + log_line(DIRTREE, os_info="").replace(" flatpak/1.14.0", "")
        + log_line().replace("200 100", "404 100")
        + log_line().replace("app/org.example.App", "runtime/org.example.App.Locale")
        + log_line().replace("16/May/2023", "99/May/2023")
        + log_line("/repo/deltas/!/superblock")
        + log_line(f"/repo/objects/cc/{'c' * 62}.dirtree")
        + "malformed line\n"
        + log_line().replace('"app/org.example.App/x86_64/stable"', '""')
        + log_line().replace("+0000", "+0100")
        + log_line("/repo/summary")
    )
    dest = tmp_path / "stats"
    monkeypatch.setattr(flathub, "load_cache", lambda path: cached_commit())
    monkeypatch.setattr(
        sys, "argv", ["update-stats.py", "--dest", str(dest), str(path)]
    )
    runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "update-stats.py"),
        run_name="__main__",
    )
    data = json.loads((dest / "2023/05/16.json").read_text())
    assert (data["downloads"], data["updates"], data["delta_downloads"]) == (4, 2, 1)
    report = next(
        line
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("Quality ")
    )
    quality = json.loads(report.split(": ", 1)[1])
    assert quality == {
        "lines": 13,
        "counted": 4,
        "filtered": 3,
        "malformed": 4,
        "unresolved_dirtrees": 1,
        "unresolved_refs": 1,
        "missing_os": 1,
        "missing_flatpak_version": 1,
    }


def test_ignored_deltas_are_reported_as_filtered(tmp_path):
    path = tmp_path / "input.log"
    path.write_text(log_line())
    quality = Counter()
    assert (
        list(flathub.parse_log(str(path), cached_commit(), True, quality=quality)) == []
    )
    assert quality == {"lines": 1, "filtered": 1}


def test_partial_report_is_printed_when_parsing_aborts(tmp_path, monkeypatch, capsys):
    path = tmp_path / "input.log"
    path.write_text(log_line())

    def fail(*args):
        raise RuntimeError("date parser failed")

    monkeypatch.setattr(flathub.time, "strptime", fail)
    monkeypatch.setattr(flathub, "load_cache", lambda path: cached_commit())
    monkeypatch.setattr(sys, "argv", ["update-stats.py", str(path)])
    with pytest.raises(RuntimeError, match="date parser failed"):
        runpy.run_path(
            str(Path(__file__).resolve().parents[1] / "update-stats.py"),
            run_name="__main__",
        )
    report = next(
        line
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("Quality ")
    )
    assert json.loads(report.split(": ", 1)[1]) == {"lines": 1}
