#!/usr/bin/env python3
"""Snapshot tests for flathub-stats with reproducible seeds and offline metadata."""

import subprocess
import sys
from pathlib import Path

import pytest
from syrupy.extensions.amber import AmberSnapshotExtension

from tests.helpers import run_stats


class SeparateFileExtension(AmberSnapshotExtension):
    @classmethod
    def dirname(cls, *, test_location):
        return (
            Path(test_location.filepath).parent
            / "__snapshots__"
            / test_location.testname
        )


@pytest.fixture
def snapshot(snapshot):
    return snapshot.use_extension(SeparateFileExtension)


def generated_stats(tmp_path, monkeypatch, seed, count):
    log = tmp_path / "input.log"
    subprocess.run(
        [
            sys.executable,
            "generate-test-data.py",
            "--seed",
            str(seed),
            "--count",
            str(count),
            "--output",
            str(log),
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        capture_output=True,
        text=True,
    )
    return run_stats(monkeypatch, [log], tmp_path / "stats", tmp_path / "cache.json")


def test_stats_with_seed_42(snapshot, tmp_path, monkeypatch):
    assert generated_stats(tmp_path, monkeypatch, 42, 100) == snapshot


def test_stats_with_seed_123(snapshot, tmp_path, monkeypatch):
    assert generated_stats(tmp_path, monkeypatch, 123, 50) == snapshot


def test_stats_with_seed_999_small_dataset(snapshot, tmp_path, monkeypatch):
    assert generated_stats(tmp_path, monkeypatch, 999, 10) == snapshot
