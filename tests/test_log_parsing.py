import gzip
import lzma
import subprocess
import sys
from types import SimpleNamespace

import pytest

import flathub


def log_line():
    return (
        '151.100.102.134 "-" "-" [16/May/2023:10:01:16 +0000] '
        f'"GET /repo/deltas/{"A" * 43}/superblock HTTP/1.1" 200 100 "" '
        '"libostree/2023.1 flatpak/1.14.0" '
        '"app/org.example.App/x86_64/stable" "" US "fedora;42"\n'
    )


def cached_commit():
    return SimpleNamespace(
        valid_arches={"x86_64"},
        update_from_summary=lambda ref: None,
        has_commit=lambda commit: True,
    )


@pytest.mark.parametrize(
    "suffix,compress",
    [(".log", lambda value: value), (".gz", gzip.compress), (".xz", lzma.compress)],
    ids=["plain", "gzip", "xz"],
)
@pytest.mark.parametrize(
    "contents,expected",
    [
        (b"", 0),
        (log_line().encode(), 1),
        (
            b"invalid first line\n\xff\n"
            + log_line().encode()
            + b"\xff\n"
            + log_line().encode(),
            2,
        ),
    ],
    ids=["empty", "valid", "malformed"],
)
def test_empty_and_malformed_lines(tmp_path, suffix, compress, contents, expected):
    path = tmp_path / f"input{suffix}"
    path.write_bytes(compress(contents))
    events = list(flathub.parse_log(str(path), cached_commit()))
    assert len(events) == expected


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


def test_additional_log_fields_remain_compatible(tmp_path):
    path = tmp_path / "input.log"
    path.write_text(log_line().rstrip("\n") + ' "additional-field" \n')
    assert len(list(flathub.parse_log(str(path), cached_commit()))) == 1
