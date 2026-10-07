"""The standalone dashboard's Files page is the GUI's, and works as it does.

The report's `dashboard.html` carried its own Files page, cards of two
across with the run record folded, written by a second renderer that
followed the GUI's by hand. It is now the GUI's page, rendered by the
GUI's renderer and worked by the GUI's script: found, filtered, sorted,
folded and its files opened in place, with nothing that needs the server.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from fastmdxplora.gui.report_dashboard import build_dashboard


def _write(root: Path, rel: str, text: str = "x") -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


@pytest.fixture()
def page(tmp_path: Path) -> Path:
    root = tmp_path / "study"
    for rel in ("analysis/rmsd/rmsd.dat", "analysis/rmsd/rmsd.svg", "analysis/rg/rg.dat",
                "simulation/production.dcd", "simulation/trajectory_topology.pdb",
                "setup/input.pdb"):
        _write(root, rel)
    _write(root, "resolved_config.yml", "simulation:\n  duration_ns: 1.0\n")
    for i in range(10):
        _write(root, f"simulation/live_frames/frame_{i}.pdb")
    _write(root, "manifest.json", json.dumps({"system": "1L2Y", "phases": [
        {"name": "analysis", "status": "ok"}, {"name": "report", "status": "ok"}]}))
    _write(root, "analysis/analysis_manifest.json",
           json.dumps({"results": {"rmsd": {"status": "ok"}, "rg": {"status": "ok"}}}))
    (root / "report").mkdir()
    build_dashboard(orchestrator=SimpleNamespace(output_dir=root, system="1L2Y"),
                    output_dir=root / "report", title="A study")
    return root / "report" / "dashboard.html"


def test_it_is_found_filtered_and_folded_where_it_lies(page):
    playwright = pytest.importorskip("playwright.sync_api")
    problems: list[str] = []
    with playwright.sync_playwright() as pw:
        browser = pw.chromium.launch()
        tab = browser.new_page(viewport={"width": 1400, "height": 900})
        tab.on("pageerror", lambda error: problems.append(str(error)))
        tab.goto(page.as_uri() + "#files")
        tab.wait_for_selector(".files-toolbar", state="visible", timeout=20000)
        tab.fill("[data-find]", "rmsd")
        tab.wait_for_timeout(100)
        shown = tab.eval_on_selector_all(
            ".files-row", "els => els.filter(e => e.offsetParent !== null)"
                          ".map(e => e.getAttribute('data-path'))")
        assert sorted(shown) == ["analysis/rmsd/rmsd.dat", "analysis/rmsd/rmsd.svg"]
        tab.fill("[data-find]", "")
        # Its links lead beside the page, and the time is the reader's.
        href = tab.get_attribute('.files-row[data-path="setup/input.pdb"] a[download]', "href")
        assert href == "../setup/input.pdb"
        # The folders, the other view, with no server to ask.
        tab.click("[data-view]")
        assert tab.eval_on_selector('[data-body="folders"]', "e => !e.hidden")
        assert tab.eval_on_selector('[data-body="phases"]', "e => e.hidden")
        tab.click("[data-view]")
        # The scratch is not listed: the page travels in the bundle, which
        # leaves it out, and its rows would lead nowhere.
        assert tab.eval_on_selector_all('[data-phase="scratch"]', "els => els.length") == 0
        tab.set_viewport_size({"width": 390, "height": 800})
        tab.wait_for_timeout(200)
        assert tab.evaluate("document.documentElement.scrollWidth") <= 390
        browser.close()
    assert problems == []


def test_the_side_panel_lists_the_snapshots_as_one_line():
    import fastmdxplora.gui as gui

    frame = (Path(gui.__file__).parent / "static" / "frame.js").read_text(encoding="utf-8")
    assert 'if (it.path.indexOf("/live_frames/") !== -1) { snapshots += 1; return; }' in frame
    assert '" live view snapshots"' in frame
    order = frame[frame.index('var order = ["", "setup"'):frame.index("];", frame.index('var order = ["", "setup"'))]
    assert order.index('"report"') < order.index('"previous"')
