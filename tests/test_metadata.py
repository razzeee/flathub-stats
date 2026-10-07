import io
import urllib.error
from email.message import Message

import pytest
from gi.repository import GLib  # type: ignore[import-untyped]

import flathub

COMMIT = "a" * 64
ROOT = "b" * 64
REF = "app/org.example.App/x86_64/stable"
DIRTREE = f"/repo/objects/{ROOT[:2]}/{ROOT[2:]}.dirtree"


def commit_bytes():
    return (
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


def log_line(path, ref):
    return (
        '151.100.102.134 "-" "-" [16/May/2023:10:01:16 +0000] '
        f'"GET {path} HTTP/1.1" 200 100 "" "libostree/2023.1 flatpak/1.14.0" '
        f'"{ref}" "" US "fedora;42"\n'
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

    def urlopen(url, **kwargs):
        if url.endswith("/summary.idx"):
            return io.BytesIO(index)
        if url.endswith("/summary"):
            return io.BytesIO(summary)
        return io.BytesIO(commit_bytes())

    monkeypatch.setattr(flathub.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(flathub.time, "sleep", lambda seconds: None)


def test_failed_commit_resolution_can_be_retried(monkeypatch):
    cache = flathub.CommitCache({})

    def fail(url, **kwargs):
        raise OSError("temporary outage")

    monkeypatch.setattr(flathub.urllib.request, "urlopen", fail)
    cache.update_for_commit(COMMIT)
    assert not cache.has_commit(COMMIT)
    # A new run must retry legacy negative-cache entries too.
    monkeypatch.setattr(
        flathub.urllib.request, "urlopen", lambda url, **kwargs: io.BytesIO(b"")
    )
    restored = flathub.CommitCache(cache.commit_map)
    monkeypatch.setattr(
        flathub.urllib.request,
        "urlopen",
        lambda url, **kwargs: io.BytesIO(commit_bytes()),
    )
    restored.update_for_commit(COMMIT)
    assert restored.lookup_ref(COMMIT) == REF
    assert restored.lookup_by_dirtree(ROOT) == COMMIT


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
    events = list(flathub.parse_log(str(path), cache))
    assert len(events) == 1
    assert events[0][flathub.REF] == REF
