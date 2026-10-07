import base64
import json
import runpy
import sys
from pathlib import Path

from gi.repository import GLib  # type: ignore[import-untyped]


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


def run_stats(monkeypatch, logfiles, dest, cache_path, *options):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "update-stats.py",
            "--dest",
            str(dest),
            "--ref-cache",
            str(cache_path),
            *options,
            *map(str, logfiles),
        ],
    )
    runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "update-stats.py"),
        run_name="__main__",
    )
    path = dest / "2023/05/16.json"
    return json.loads(path.read_text()) if path.exists() else None
