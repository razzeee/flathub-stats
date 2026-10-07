import io

import pytest
from gi.repository import GLib  # type: ignore[import-untyped]

import flathub
from tests.helpers import commit_bytes


@pytest.fixture(autouse=True)
def offline_network(monkeypatch):
    """All metadata is deterministic; an unexpected URL fails instead of using the network."""
    index = (
        GLib.Variant(
            "(a{s(ayaaya{sv})}a{sv})",
            (
                {
                    arch: ([], [], {})
                    for arch in ("x86_64", "aarch64", "i386", "riscv64")
                },
                {},
            ),
        )
        .get_data_as_bytes()
        .get_data()
    )
    summary = (
        GLib.Variant("(a(s(taya{sv}))a{sv})", ([], {})).get_data_as_bytes().get_data()
    )

    def urlopen(url, **kwargs):
        if url == "https://dl.flathub.org/repo/summary.idx":
            return io.BytesIO(index)
        if url == "https://dl.flathub.org/repo/summary":
            return io.BytesIO(summary)
        if url.startswith("https://dl.flathub.org/repo/objects/") and url.endswith(
            ".commit"
        ):
            return io.BytesIO(commit_bytes())
        raise AssertionError(f"Unexpected metadata request: {url}")

    monkeypatch.setattr(flathub.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(flathub.time, "sleep", lambda seconds: None)
