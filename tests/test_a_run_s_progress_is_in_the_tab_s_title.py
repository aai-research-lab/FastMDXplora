"""How far a run is, in the tab's title, and a notice when it ends, asked for.

A run of hours is watched from another tab. While it runs, the tab's title
begins with how far it is; once it ends, the page's own title is back.
Asked for in Preferences, the browser shows a notice as the run ends: only
for a run seen running in the page, never on opening a finished study.
"""

from __future__ import annotations

import json

import pytest

from tests.test_the_drawing_scripts_run_in_a_browser import _open, _write_study


def _running(study):
    (study / "simulation" / "live_status.json").write_text(json.dumps(
        {"status": "running", "stage": "production", "current_step": 1000,
         "total_steps": 2500, "total_planned_steps": 2500, "progress_percent": 40.0}),
        encoding="utf-8")


def test_the_title_and_the_notice(tmp_path) -> None:
    pytest.importorskip("playwright.sync_api")
    from fastmdxplora.gui.server import start_dashboard_session

    study = _write_study(tmp_path / "study")
    _running(study)
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        for page in _open(session, "#overview"):
            page.set_default_timeout(60000)
            page.evaluate("""() => {
                window.__notices = [];
                window.Notification = function (title, options) {
                    window.__notices.push([title, options && options.body]);
                };
                window.Notification.permission = 'granted';
                window.Notification.requestPermission = () => Promise.resolve('granted');
            }""")
            page.wait_for_function("() => /^\\d+% · /.test(document.title)")
            running = page.title()
            # Asked for, in Preferences.
            page.evaluate("() => window.FastMDXDialog.open('prefs-dialog')")
            page.check("#setting-notify")
            page.evaluate("() => window.FastMDXDialog.close('prefs-dialog')")
            (study / "simulation" / "live_status.json").write_text(json.dumps(
                {"status": "completed", "stage": "production", "current_step": 2500,
                 "total_steps": 2500}), encoding="utf-8")
            page.wait_for_function("() => !/%/.test(document.title)")
            page.wait_for_function("() => window.__notices.length > 0")
            notices = page.evaluate("() => window.__notices")
            kept = page.evaluate("() => JSON.parse(localStorage.getItem('fmx.preferences')).notify")
            assert not page.errors, page.errors
    finally:
        session.server.shutdown()
    assert running.startswith("40% · ")
    assert notices[0][0].endswith(" completed")
    assert kept is True


def test_a_finished_study_opened_says_nothing(tmp_path) -> None:
    pytest.importorskip("playwright.sync_api")
    from fastmdxplora.gui.server import start_dashboard_session

    study = _write_study(tmp_path / "study")
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        for page in _open(session, "#overview"):
            page.set_default_timeout(60000)
            page.add_init_script("""
                window.__notices = [];
                window.Notification = function (t) { window.__notices.push(t); };
                window.Notification.permission = 'granted';
                localStorage.setItem('fmx.preferences', JSON.stringify({notify: true}));
            """)
            page.reload(wait_until="domcontentloaded")
            page.wait_for_function(
                "() => document.getElementById('topbar-status-text').textContent.trim() !== ''")
            page.wait_for_timeout(4000)
            notices = page.evaluate("() => window.__notices")
            title = page.title()
    finally:
        session.server.shutdown()
    assert notices == []
    assert "%" not in title


def test_refused_by_the_browser_it_is_unticked_and_says_why(tmp_path) -> None:
    pytest.importorskip("playwright.sync_api")
    from fastmdxplora.gui.server import start_dashboard_session

    study = _write_study(tmp_path / "study")
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        for page in _open(session, "#overview"):
            page.set_default_timeout(60000)
            page.evaluate("""() => {
                window.Notification = function () {};
                window.Notification.permission = 'denied';
            }""")
            page.evaluate("() => window.FastMDXDialog.open('prefs-dialog')")
            page.click("#setting-notify")
            page.wait_for_selector("#setting-notify-note:not([hidden])")
            ticked = page.is_checked("#setting-notify")
            said = page.text_content("#setting-notify-note")
    finally:
        session.server.shutdown()
    assert not ticked
    assert "turned off" in said
