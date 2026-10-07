import gzip
import io
import json
import lzma
import sqlite3
import urllib.error
from collections import Counter
from email.message import Message

import pytest
from gi.repository import GLib  # type: ignore[import-untyped]

import flathub
from tests.helpers import (
    COMMIT,
    DELTA,
    DIRTREE,
    REF,
    ROOT,
    commit_bytes,
    log_line,
    run_stats,
)


@pytest.mark.parametrize(
    "suffix,compress",
    [
        (".log", lambda value: value),
        (".gz", gzip.compress),
        (".xz", lzma.compress),
    ],
    ids=["plain", "gzip", "xz"],
)
@pytest.mark.parametrize(
    "contents,expected,malformed",
    [
        (b"", 0, 0),
        (log_line().encode(), 1, 0),
        (
            b"invalid first line\n\xff\n"
            + log_line().encode()
            + b"\xff\n"
            + log_line().encode(),
            2,
            3,
        ),
    ],
    ids=["empty", "valid", "malformed"],
)
def test_stream_handles_empty_and_malformed_lines(
    tmp_path, suffix, compress, contents, expected, malformed
):
    path = tmp_path / f"input{suffix}"
    path.write_bytes(compress(contents))
    quality = Counter()
    events = list(flathub.parse_log(path, flathub.CommitCache({}), quality=quality))
    assert len(events) == expected
    assert quality["malformed"] == malformed
    assert quality["lines"] == expected + malformed


def test_exact_counts_and_quality(tmp_path, monkeypatch):
    path = tmp_path / "input.log"
    path.write_text(
        log_line()
        + log_line(update="previous-commit")
        + log_line(f"/repo/deltas/{DELTA}-{DELTA}/superblock")
        + log_line(DIRTREE, os_info="").replace(" flatpak/1.14.0", "")
        + log_line().replace("200 100", "404 100")
        + log_line(ref="runtime/org.example.App.Locale/x86_64/stable")
        + log_line().replace("16/May/2023", "99/May/2023")
        + log_line("/repo/deltas/not-a-checksum/superblock")
        + log_line(f"/repo/objects/cc/{'c' * 62}.dirtree")
    )
    dest = tmp_path / "stats"
    data = run_stats(monkeypatch, [path], dest, tmp_path / "cache.json")
    assert (data["downloads"], data["updates"], data["delta_downloads"]) == (4, 2, 1)
    assert data["refs"] == {"org.example.App": {"x86_64": [4, 2]}}
    assert data["ref_by_country"] == {"org.example.App": {"US": [4, 2]}}
    assert data["ref_by_os_version"] == {"org.example.App": {"fedora;42": [3, 2]}}
    assert sum(data["countries"].values()) == 4
    assert sum(data["ostree_versions"].values()) == 4
    assert sum(data["flatpak_versions"].values()) == 3
    assert data["os_versions"] == {"fedora;42": 3}
    assert data["os_flatpak_versions"] == {"fedora;42": {"1.14.0": 3}}
    with sqlite3.connect(dest / ".ingestion.sqlite3") as db:
        quality = json.loads(db.execute("SELECT quality FROM inputs").fetchone()[0])
    assert quality == {
        "lines": 9,
        "counted": 4,
        "filtered": 2,
        "malformed": 2,
        "unresolved_dirtrees": 1,
        "missing_os": 1,
        "missing_flatpak_version": 1,
    }


@pytest.mark.parametrize("historical", [False, True])
def test_metadata_resolved_across_files(tmp_path, monkeypatch, historical):
    dirtree_log = tmp_path / "dirtree.log"
    metadata_log = tmp_path / "metadata.log"
    dirtree_log.write_text(log_line(DIRTREE))
    metadata_log.write_text(
        log_line(f"/repo/objects/{COMMIT[:2]}/{COMMIT[2:]}.commit")
        if historical
        else log_line()
    )
    results = []
    for index, paths in enumerate(
        [[dirtree_log, metadata_log], [metadata_log, dirtree_log]]
    ):
        results.append(
            run_stats(
                monkeypatch,
                paths,
                tmp_path / f"stats-{index}",
                tmp_path / f"cache-{index}.json",
            )
        )
    assert results[0] == results[1]
    assert results[0]["downloads"] == (1 if historical else 2)


def test_shared_root_requires_unambiguous_ref(tmp_path):
    other_commit = "c" * 64
    other_ref = "app/org.example.Other/x86_64/stable"
    cache = flathub.CommitCache({COMMIT: [REF, ROOT], other_commit: [other_ref, ROOT]})
    path = tmp_path / "input.log"
    path.write_text(
        log_line(DIRTREE) + log_line(DIRTREE, ref=other_ref) + log_line(DIRTREE, ref="")
    )
    quality = Counter()
    events = list(flathub.parse_log(path, cache, quality=quality))
    assert [event[flathub.REF] for event in events] == [REF, other_ref]
    assert quality["unresolved_dirtrees"] == 1


def test_transient_metadata_failure_retries_with_timeout(monkeypatch):
    calls = []

    def fetch(url, timeout):
        calls.append(timeout)
        if len(calls) < 3:
            raise OSError("temporary outage")
        return io.BytesIO(commit_bytes())

    monkeypatch.setattr(flathub.urllib.request, "urlopen", fetch)
    assert flathub.fetch_metadata("https://example.test/object") == commit_bytes()
    assert calls == [15, 15, 15]


def test_missing_metadata_does_not_retry_within_run(monkeypatch):
    cache = flathub.CommitCache({COMMIT: [None, None]})
    calls = []

    def fetch(url, timeout):
        calls.append(url)
        raise urllib.error.HTTPError(url, 404, "Not found", Message(), None)

    monkeypatch.setattr(flathub.urllib.request, "urlopen", fetch)
    cache.update_for_commit(COMMIT)
    cache.update_for_commit(COMMIT)
    assert len(calls) == 1
    assert cache.resolution_failures == 1
    assert not cache.has_commit(COMMIT)


def test_architecture_fallback_retains_historical_arches(monkeypatch):
    def fail(url, **kwargs):
        raise OSError("offline")

    monkeypatch.setattr(flathub.urllib.request, "urlopen", fail)
    cache = flathub.CommitCache({})
    assert {"x86_64", "aarch64", "i386", "arm"} <= cache.valid_arches


def test_legacy_ref_only_cache_still_resolves_historical_dirtrees(tmp_path):
    path = tmp_path / "input.log"
    path.write_text(log_line(DIRTREE, ref=""))
    cache = flathub.CommitCache({COMMIT: REF})
    events = list(flathub.parse_log(path, cache))
    assert len(events) == 1
    assert events[0][flathub.REF] == REF


def test_additional_log_fields_remain_compatible(tmp_path):
    path = tmp_path / "input.log"
    path.write_text(log_line().rstrip("\n") + ' "additional-field" \n')
    assert len(list(flathub.parse_log(path, flathub.CommitCache({})))) == 1


def test_summary_head_resolves_dirtree_without_commit_request(tmp_path, monkeypatch):
    summary = (
        GLib.Variant(
            "(a(s(taya{sv}))a{sv})",
            ([(REF, (0, list(bytes.fromhex(COMMIT)), {}))], {}),
        )
        .get_data_as_bytes()
        .get_data()
    )
    fetch = flathub.urllib.request.urlopen

    def urlopen(url, **kwargs):
        if url.endswith("/summary"):
            return io.BytesIO(summary)
        return fetch(url, **kwargs)

    monkeypatch.setattr(flathub.urllib.request, "urlopen", urlopen)
    path = tmp_path / "input.log"
    path.write_text(log_line(DIRTREE))
    assert len(list(flathub.parse_log(path, flathub.CommitCache({})))) == 1
