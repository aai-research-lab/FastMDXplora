"""A study that stopped with an error is headed briefly on the Overview.

The health card set the failure's message as its headline, in the card's
largest type, and a message can run to a paragraph with a path in it:
"Simulation cannot start because setup outputs are missing in /Users/...
/setup. Run the setup phase first, or choose an analysis-only workflow." read
as a banner (reported 10-03). A failure now has a short headline, and the
message is said beneath it at the size of prose, on the live page and in the
study's standalone dashboard. The Overview's subtitle and its charts' title
said a finished study was running; they say it as it is.
"""

from __future__ import annotations

import json

import pytest

MESSAGE = ("Simulation cannot start because setup outputs are missing in "
           "/Users/someone/studies/fastmdxplora_output_20260918_101714/setup. Run the "
           "setup phase first, or choose an analysis-only workflow.")


def test_the_record_gives_a_headline_and_keeps_the_message():
    from fastmdxplora.gui.telemetry import analyze_health

    health = analyze_health({"status": "failed", "latest_error": MESSAGE}, [])
    assert health["headline"] == "Stopped with an error"
    assert health["message"] == MESSAGE


def test_the_standalone_dashboard_says_the_message(tmp_path):
    from fastmdxplora.gui.report_dashboard import _render_static_live_panel

    (tmp_path / "simulation").mkdir()
    (tmp_path / "simulation" / "live_status.json").write_text(json.dumps(
        {"status": "failed", "stage": "setup", "latest_error": MESSAGE}), encoding="utf-8")
    assert "setup outputs are missing" in _render_static_live_panel(tmp_path)


def test_the_overview_heads_it_briefly(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_the_drawing_scripts_run_in_a_browser import _write_study

    study = _write_study(tmp_path / "study")
    (study / "simulation" / "live_status.json").write_text(json.dumps(
        {"status": "failed", "stage": "setup", "latest_error": MESSAGE}), encoding="utf-8")
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.set_default_timeout(60000)
            page.goto(session.url + "#overview", wait_until="domcontentloaded")
            page.wait_for_function(
                "() => document.getElementById('health-headline').textContent"
                " === 'Stopped with an error'")
            said = page.text_content("#health-explanation")
            # And the page is worded for a run that has ended.
            worded = (page.text_content("#overview-subtitle"),
                      page.text_content("#overview-charts-title"))
            sizes = page.evaluate(
                "() => ['health-headline', 'health-explanation'].map((id) =>"
                " parseFloat(getComputedStyle(document.getElementById(id)).fontSize))")
            browser.close()
    finally:
        session.server.shutdown()
    assert MESSAGE in said
    assert sizes[0] > sizes[1]
    assert worded == ("What the run did, and how it went.", "Charts")
