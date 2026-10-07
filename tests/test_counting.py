import base64
import json
import runpy
import sys
from pathlib import Path

import pytest

import flathub

COMMIT = "a" * 64
ROOT = "b" * 64
REF = "app/org.example.App/x86_64/stable"
DELTA = base64.b64encode(bytes.fromhex(COMMIT)).decode().rstrip("=").replace("/", "_")
SUPERBLOCK = f"/repo/deltas/{DELTA}/superblock"
DIRTREE = f"/repo/objects/{ROOT[:2]}/{ROOT[2:]}.dirtree"


def log_line(path=SUPERBLOCK, ref=REF, update="", os_info="fedora;42"):
    return (
        '151.100.102.134 "-" "-" [16/May/2023:10:01:16 +0000] '
        f'"GET {path} HTTP/1.1" 200 100 "" "libostree/2023.1 flatpak/1.14.0" '
        f'"{ref}" "{update}" US "{os_info}"\n'
    )


@pytest.fixture(autouse=True)
def offline_metadata(monkeypatch):
    def unavailable(url):
        raise OSError("offline summary")

    def resolve(cache, commit, ref=None):
        cache.commit_map[commit] = [ref or REF, ROOT]
        cache.dirtree_map.setdefault(ROOT, set()).add(commit)
        cache.modified = True

    monkeypatch.setattr(flathub.urllib.request, "urlopen", unavailable)
    monkeypatch.setattr(flathub.CommitCache, "update_for_commit", resolve)


def run_stats(monkeypatch, paths, dest, cache):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "update-stats.py",
            "--dest",
            str(dest),
            "--ref-cache",
            str(cache),
            *map(str, paths),
        ],
    )
    runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "update-stats.py"),
        run_name="__main__",
    )
    return json.loads((dest / "2023/05/16.json").read_text())


def test_dirtree_count_is_independent_of_log_order(tmp_path, monkeypatch):
    results = []
    for number, lines in enumerate(
        [log_line(DIRTREE) + log_line(), log_line() + log_line(DIRTREE)]
    ):
        path = tmp_path / f"input-{number}.log"
        path.write_text(lines)
        results.append(
            run_stats(
                monkeypatch,
                [path],
                tmp_path / f"stats-{number}",
                tmp_path / f"cache-{number}.json",
            )
        )
    assert results[0] == results[1]
    assert results[0]["downloads"] == 2


def test_exact_counts(tmp_path, monkeypatch):
    path = tmp_path / "input.log"
    path.write_text(
        log_line()
        + log_line(update="previous")
        + log_line(f"/repo/deltas/{DELTA}-{DELTA}/superblock")
        + log_line(DIRTREE, os_info="").replace(" flatpak/1.14.0", "")
    )
    data = run_stats(monkeypatch, [path], tmp_path / "stats", tmp_path / "cache.json")
    assert (data["downloads"], data["updates"], data["delta_downloads"]) == (4, 2, 1)
    assert data["refs"] == {"org.example.App": {"x86_64": [4, 2]}}
    assert data["ref_by_country"] == {"org.example.App": {"US": [4, 2]}}
    assert data["ref_by_os_version"] == {"org.example.App": {"fedora;42": [3, 2]}}
    assert sum(data["ostree_versions"].values()) == 4
    assert sum(data["flatpak_versions"].values()) == 3


@pytest.mark.parametrize("historical", [False, True])
def test_metadata_resolved_across_files(tmp_path, monkeypatch, historical):
    dirtree_log, metadata_log = tmp_path / "dirtree.log", tmp_path / "metadata.log"
    dirtree_log.write_text(log_line(DIRTREE))
    metadata_log.write_text(
        log_line(f"/repo/objects/{COMMIT[:2]}/{COMMIT[2:]}.commit")
        if historical
        else log_line()
    )
    results = []
    for number, paths in enumerate(
        [[dirtree_log, metadata_log], [metadata_log, dirtree_log]]
    ):
        results.append(
            run_stats(
                monkeypatch,
                paths,
                tmp_path / f"stats-{number}",
                tmp_path / f"cache-{number}.json",
            )
        )
    assert results[0] == results[1]
    assert results[0]["downloads"] == (1 if historical else 2)


def test_shared_root_requires_unambiguous_ref(tmp_path):
    other_ref = "app/org.example.Other/x86_64/stable"
    cache = flathub.CommitCache({COMMIT: [REF, ROOT], "c" * 64: [other_ref, ROOT]})
    path = tmp_path / "input.log"
    path.write_text(
        log_line(DIRTREE) + log_line(DIRTREE, ref=other_ref) + log_line(DIRTREE, ref="")
    )
    events = flathub.parse_log(str(path), cache)
    assert [event[flathub.REF] for event in events] == [REF, other_ref]


def test_summary_head_resolves_dirtree(tmp_path):
    cache = flathub.CommitCache({})
    cache.summary_map = {REF: COMMIT}
    path = tmp_path / "input.log"
    path.write_text(log_line(DIRTREE))
    assert len(list(flathub.parse_log(str(path), cache))) == 1
