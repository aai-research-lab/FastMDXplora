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


def _ended_at_the_first_status(tmp_path, started=None, ended_started=None):
    """A run that ends as the page hears its first status: the end is
    written from that status's own listener, so the page's next status is,
    unless a poll was already on its way, the run's last. Gives the title
    while it ran and the notices shown, read once a status after the end
    has come too."""
    from fastmdxplora.gui.server import start_dashboard_session

    study = _write_study(tmp_path / "study")
    _running(study)
    live = study / "simulation" / "live_status.json"
    if started:
        live.write_text(json.dumps({**json.loads(live.read_text()), "run_started_at": started}),
                        encoding="utf-8")
    ended = {"status": "completed", "stage": "production", "current_step": 2500,
             "total_steps": 2500}
    if ended_started:
        ended["run_started_at"] = ended_started

    def end_the_run():
        live.write_text(json.dumps(ended), encoding="utf-8")

    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        for page in _open(session, "#overview"):
            page.set_default_timeout(60000)
            page.expose_function("endTheRun", end_the_run)
            page.add_init_script("""
                window.__notices = [];
                window.__statuses = 0;
                window.Notification = function (t) { window.__notices.push(t); };
                window.Notification.permission = 'granted';
                localStorage.setItem('fmx.preferences', JSON.stringify({notify: true}));
                window.addEventListener('dashboard:status-updated', (event) => {
                    window.__statuses += 1;
                    const said = (event.detail && event.detail.status) || {};
                    if (said.status === 'running' && !window.__ended) {
                        window.__ended = true;
                        window.endTheRun();
                    }
                });
            """)
            page.reload(wait_until="domcontentloaded")
            # The title as the wait saw it: read after, the end may be in.
            running = page.wait_for_function(
                "() => /^\\d+% · /.test(document.title) && document.title").json_value()
            page.wait_for_function("() => !/%/.test(document.title)")
            # One more status after the end: a notice shown twice, or late,
            # would be in by then. The record written again asks for it.
            seen = page.evaluate("() => window.__statuses")
            end_the_run()
            page.wait_for_function(f"() => window.__statuses > {seen}")
            notices = page.evaluate("() => window.__notices")
            assert not page.errors, page.errors
    finally:
        session.server.shutdown()
    return running, notices


def test_a_run_ending_as_the_page_opens_still_says_so(tmp_path) -> None:
    """A poll's status comes before the study's records, so a run was first
    named by the dashboard's own name and then, once the records came, by
    the study's. A run that ended at the page's next status had changed
    name as it ended, and was taken for another run: no notice (CI run
    #705). The tab's title had the dashboard's name until then too."""
    pytest.importorskip("playwright.sync_api")
    running, notices = _ended_at_the_first_status(tmp_path)
    assert running.startswith("40% · study · "), running
    assert notices == ["study completed"]


STARTED = "2026-10-08T09:00:00+00:00"


@pytest.mark.parametrize(("ended_started", "said"), [
    (STARTED, ["study completed"]), ("2026-10-08T09:30:00+00:00", []), (None, []),
    ("from no record", ["study completed"])])
def test_a_notice_is_for_the_run_that_ran(tmp_path, ended_started, said) -> None:
    """A run is the run its live record says started when: the folder's
    record keeps it until a rerun clears it. The status of a record started
    at another time, or of no record, after a running one (another study
    opened elsewhere as a poll was answered, its app state and status read
    either side of the change) is not this run ending, and says nothing."""
    pytest.importorskip("playwright.sync_api")
    if ended_started == "from no record":
        # Seen running before its record said when it started (the run's
        # process going, its record not written yet): its end is its end.
        _, notices = _ended_at_the_first_status(tmp_path, None, STARTED)
    else:
        _, notices = _ended_at_the_first_status(tmp_path, STARTED, ended_started)
    assert notices == said


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
