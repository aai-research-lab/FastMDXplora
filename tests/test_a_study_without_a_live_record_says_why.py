"""The Overview says why a study open in the GUI has no live record.

A finished study of several runs keeps no record of a simulation in its own
folder; each run keeps its own. Its Overview said "Waiting for the
simulation" and "Setup is under way", as it did for any study open without
one: the page told only "a study is open" from "none is". Now a study of
several runs shows them on its Overview instead, a study being run
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


def test_a_finished_study_of_several_runs_shows_them_instead():
    """Its runs are on its Overview since ac336f15 (overview-runs.js), so the
    note that there is no live record is not shown over them."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    root = _a_sweep_study(Path(tempfile.mkdtemp()))
    for status in root.glob("runs/*/simulation/live_status.json"):
        status.write_text(json.dumps({"stage": "completed", "current_step": 500,
                                      "total_planned_steps": 500}), encoding="utf-8")
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#overview", wait_until="domcontentloaded")
            page.wait_for_selector("#overview-runs:not([hidden]) #overview-runs-table tbody tr")
            rows = page.locator("#overview-runs-table tbody tr").count()
            absent_hidden = page.eval_on_selector("#live-absent", "e => e.hidden")
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert rows == 2
    assert absent_hidden


def test_a_study_with_no_live_record_says_so():
    study = Path(tempfile.mkdtemp()) / "study"
    shutil.copytree(_a_finished_study(), study)
    assert not (study / "simulation" / "live_status.json").exists()
    title, body, hidden = _said(study)
    assert title == "No live record"
    assert "simulation.live_telemetry" in body
    assert hidden


def test_a_study_opened_after_one_with_a_live_record_says_none_of_its_line(tmp_path):
    """The Overview's line under the study's name is read from the live
    record's cells. A study opened in its place without a page loaded again
    (the server's study switched from elsewhere) and with no live record of
    its own said the last one's wall time and last update until its own
    first status."""
    pytest.importorskip("playwright.sync_api")
    import urllib.request

    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_the_sidebar_says_the_study_and_where_its_run_stands import _live
    from tests.test_the_workspace_says_its_studies import _study

    first = _live(_study(tmp_path / "first", system="1AKE", started="2026-09-02T10:00:00+00:00"),
                  status="completed", stage="production", current_step=2000000,
                  total_planned_steps=2000000, elapsed_wall_time_s=5400,
                  last_update_timestamp="2026-09-02T11:30:00+00:00", platform="CUDA")
    second = _study(tmp_path / "second", system="1UBQ", started="2026-09-03T10:00:00+00:00")
    assert not (second / "simulation" / "live_status.json").exists()
    session = start_dashboard_session(output=str(first), host="127.0.0.1", port=0)
    facts = "() => document.getElementById('verdict-facts').textContent"
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#overview", wait_until="domcontentloaded")
            page.wait_for_function(f"() => ({facts})().includes('1h 30m')")
            first_said = page.evaluate(facts)
            asked = urllib.request.Request(
                session.url + "/api/explore/switch", method="POST",
                data=json.dumps({"folder": str(second)}).encode(),
                headers={"content-type": "application/json"})
            with urllib.request.urlopen(asked, timeout=30) as answer:
                assert json.loads(answer.read())["ok"]
            page.wait_for_function(
                "() => document.getElementById('verdict-name').textContent === 'second'")
            page.wait_for_selector("#live-absent:not([hidden])")
            second_said = page.evaluate(facts)
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert "1h 30m" in first_said
    assert "1h 30m" not in second_said
    assert "11:30" not in second_said
