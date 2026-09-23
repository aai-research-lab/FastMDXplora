"""The GUI says what a folder is when it is not a run.

A folder holding several runs, like the one the GPU shakedown writes, was
described as a single run that had turned its live telemetry off, and so
was an empty folder opened to start a study. Asked of the running server.
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

import pytest

from fastmdxplora.gui.server import start_test_server


def _a_run(folder: Path) -> Path:
    # Every run writes manifest.json at its top; that is what makes a
    # folder a run for everything that reads one.
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text(json.dumps({"phases": []}), encoding="utf-8")
    return folder


def _health(root: Path) -> dict:
    server, url = start_test_server(root)
    try:
        with urllib.request.urlopen(url + "/api/status", timeout=30) as response:
            return json.load(response)["health"]
    finally:
        server.shutdown()
        server.server_close()


def test_a_folder_of_runs_names_them(tmp_path: Path) -> None:
    root = tmp_path / "shakedown"
    for name in ("whole", "seg0", "seg1", "seg2"):
        _a_run(root / name)
    (root / "shakedown.json").write_text("{}", encoding="utf-8")
    health = _health(root)
    assert health["message"] == "This folder holds 4 runs rather than being one."
    assert "seg0, seg1, seg2, whole" in health["explanation"]
    assert "telemetry" not in health["explanation"]


def test_an_empty_folder_says_nothing_has_run(tmp_path: Path) -> None:
    root = tmp_path / "new-study"
    root.mkdir()
    health = _health(root)
    assert health["message"] == "Nothing has run in this folder yet."
    assert "telemetry" not in health["explanation"]


@pytest.mark.parametrize("marker", ["manifest.json", "simulation"])
def test_a_run_without_telemetry_is_still_told_about_the_setting(tmp_path: Path, marker) -> None:
    root = tmp_path / "run"
    root.mkdir()
    if marker == "manifest.json":
        (root / marker).write_text(json.dumps({"phases": []}), encoding="utf-8")
    else:
        (root / marker).mkdir()
    health = _health(root)
    assert health["message"] == "Live telemetry is not available."
    assert "live_telemetry: false" in health["explanation"]
