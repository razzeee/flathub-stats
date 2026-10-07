import gzip
import io
import subprocess
import sys

import flathub
from tests.helpers import COMMIT, ROOT, REF, DIRTREE, commit_bytes, log_line, run_stats


def test_gzip_reaches_eof(tmp_path):
    path = tmp_path / "input.log.gz"
    with gzip.open(path, "wt") as stream:
        stream.write(log_line())
    code = (
        "import flathub; from types import SimpleNamespace; "
        "cache=SimpleNamespace(valid_arches={'x86_64'}, "
        "update_from_summary=lambda ref: None, has_commit=lambda commit: True); "
        f"assert len(list(flathub.parse_log({str(path)!r}, cache))) == 1"
    )
    subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        timeout=2,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def test_identical_input_is_not_added_twice(tmp_path, monkeypatch, offline_network):
    path = tmp_path / "input.log"
    path.write_text(log_line())
    dest = tmp_path / "stats"
    cache_path = tmp_path / "cache.json"
    first = run_stats(monkeypatch, [path], dest, cache_path)
    second = run_stats(monkeypatch, [path], dest, cache_path)
    assert first == second
    assert second["downloads"] == 1


def test_dirtree_count_is_independent_of_log_order(
    tmp_path, monkeypatch, offline_network
):
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


def test_failed_commit_resolution_can_be_retried(monkeypatch, offline_network):
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
