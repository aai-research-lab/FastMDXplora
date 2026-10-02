"""The Overview gives the checkpoint from the study and the last update briefly.

Its health card gave the checkpoint by its absolute path and the last update
as a full date and time, and both were cut off in their cells: a study under
a temporary folder read "/tmp/tmpa1b2c3d4/st..." and "9/29/2026, 5:36:1...".
The checkpoint is now given from the study, the time to the minute, and both
in full in the cell's title. A run can write its path through another name
for the same folder (on a Mac, /var is /private/var), so the study's own
folder name is looked for too.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_the_drawing_scripts_run_in_a_browser import _write_study  # noqa: E402


def _cells(study: Path, checkpoint: str, *, hours: str = "24h") -> dict[str, tuple[str, str]]:
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    (study / "simulation" / "live_status.json").write_text(json.dumps({
        "status": "completed", "stage": "production", "current_step": 2500,
        "total_planned_steps": 2500, "current_checkpoint_path": checkpoint,
        "last_update_timestamp": datetime.now(timezone.utc).isoformat()}), encoding="utf-8")
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.set_default_timeout(60000)
            page.goto(session.url + "#overview", wait_until="domcontentloaded")
            page.wait_for_function(
                "() => document.getElementById('live-checkpoint-cell').textContent !== '\\u2014'")
            if hours == "12h":
                page.evaluate("""() => { const s = document.getElementById('setting-time-format');
                    s.value = '12h'; s.dispatchEvent(new Event('change', {bubbles: true})); }""")
                page.wait_for_function(
                    "() => /[AP]M/i.test(document.getElementById('live-lastupdate-cell')"
                    ".textContent)")
            cells = {name: page.eval_on_selector(
                f"#live-{name}-cell", "e => [e.textContent, e.title]")
                for name in ("checkpoint", "lastupdate")}
            browser.close()
    finally:
        session.server.shutdown()
    return {name: tuple(value) for name, value in cells.items()}


def test_the_checkpoint_from_the_study_and_the_time_to_the_minute(tmp_path):
    study = _write_study(tmp_path / "study")
    whole = str(study.resolve() / "simulation" / "checkpoint.chk")
    cells = _cells(study, whole)
    assert cells["checkpoint"] == ("simulation/checkpoint.chk", whole)
    said, title = cells["lastupdate"]
    assert re.fullmatch(r"\d{2}:\d{2}", said), said
    assert title and title != said


def test_a_checkpoint_written_through_another_name_for_the_folder(tmp_path):
    study = _write_study(tmp_path / "study")
    elsewhere = "/private" + str(study.resolve() / "segment-001" / "simulation" / "checkpoint.chk")
    assert _cells(study, elsewhere)["checkpoint"] == (
        "segment-001/simulation/checkpoint.chk", elsewhere)


def test_the_time_follows_the_time_setting(tmp_path):
    study = _write_study(tmp_path / "study")
    said, _ = _cells(study, str(study / "simulation" / "checkpoint.chk"),
                     hours="12h")["lastupdate"]
    assert re.fullmatch(r"\d{1,2}:\d{2}\s?[AP]M", said, re.IGNORECASE), said
