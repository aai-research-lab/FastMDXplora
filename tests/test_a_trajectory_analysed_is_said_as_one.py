"""A study that analysed a trajectory is said as one.

It ran no simulation, yet its Overview said "No live record ... it stopped
before the simulation began", and the narration panel stood open and empty
on every page.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml


def _analysed(root: Path, include: list[str] | None = None, phases=("analysis", "report")) -> Path:
    from tests.test_the_drawing_scripts_run_in_a_browser import _write_study

    _write_study(root)
    (root / "simulation" / "live_status.json").unlink(missing_ok=True)
    (root / "simulation" / "live_metrics.csv").unlink(missing_ok=True)
    (root / "manifest.json").write_text(json.dumps({"phases": [
        {"name": name, "status": "ok"} for name in phases]}), encoding="utf-8")
    config = {"include_phase": include if include is not None else ["analysis"],
              "analysis": {"trajectory": "elsewhere/production.dcd"}}
    (root / "resolved_config.yml").write_text(yaml.safe_dump(config), encoding="utf-8")
    return root


def test_only_a_study_that_asked_for_no_simulation_is_analysed_only(tmp_path):
    from fastmdxplora.gui.exploration import _analysed_only

    assert _analysed_only(_analysed(tmp_path / "a"))
    # Stopped before its simulation began: it asked for one.
    assert not _analysed_only(_analysed(tmp_path / "b", include=[
        "setup", "simulation", "analysis", "report"], phases=("setup",)))
    assert not _analysed_only(_analysed(tmp_path / "c", phases=(
        "setup", "simulation", "analysis")))
    assert not _analysed_only(tmp_path / "nothing")


def test_its_overview_says_so_and_the_narration_is_closed(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    study = _analysed(tmp_path / "study")
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.set_default_timeout(60000)
            page.goto(session.url + "#overview", wait_until="domcontentloaded")
            page.wait_for_function(
                "() => !document.getElementById('live-absent').hidden && "
                "document.getElementById('live-absent-title').textContent.trim() !== ''")
            page.wait_for_timeout(1500)
            title = page.text_content("#live-absent-title")
            body = page.text_content("#live-absent-body")
            panel = page.evaluate(
                "() => document.body.classList.contains('panel-collapsed')")
            browser.close()
    finally:
        session.server.shutdown()
    assert title == "A trajectory analysed"
    assert "stopped before" not in body
    assert panel
