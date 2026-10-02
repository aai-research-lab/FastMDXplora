"""The Overview says why a study open in the GUI has no live record.

A finished study of several runs keeps no record of a simulation in its own
folder; each run keeps its own. Its Overview said "Waiting for the
simulation" and "Setup is under way", as it did for any study open without
one: the page told only "a study is open" from "none is". Now a study of
several runs says how many it has and where to view them, a study being run
from the GUI waits for its simulation, and any other study says it kept no
live record and why it may not have.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from tests.test_a_study_of_runs_is_shown_as_one import _a_sweep_study
from tests.test_the_frame_holds_the_study import _a_finished_study


def _said(study: Path) -> tuple[str, str, bool]:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#overview", wait_until="domcontentloaded")
            page.wait_for_selector("#live-absent:not([hidden])")
            page.wait_for_function(
                "() => document.getElementById('live-absent-title').textContent"
                " !== 'Nothing running'")
            said = (page.text_content("#live-absent-title"),
                    page.text_content("#live-absent-body"),
                    page.eval_on_selector("#live-absent-actions", "e => e.hidden"))
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    return said


def test_a_finished_study_of_several_runs_says_where_they_are():
    root = _a_sweep_study(Path(tempfile.mkdtemp()))
    for status in root.glob("runs/*/simulation/live_status.json"):
        status.write_text(json.dumps({"stage": "completed", "current_step": 500,
                                      "total_planned_steps": 500}), encoding="utf-8")
    title, body, hidden = _said(root)
    assert title == "A study of 2 runs"
    assert body.startswith("2 of 2 completed.")
    assert "view one from Runs in the sidebar" in body
    assert hidden


def test_a_study_with_no_live_record_says_so():
    study = Path(tempfile.mkdtemp()) / "study"
    shutil.copytree(_a_finished_study(), study)
    assert not (study / "simulation" / "live_status.json").exists()
    title, body, hidden = _said(study)
    assert title == "No live record"
    assert "simulation.live_telemetry" in body
    assert hidden
