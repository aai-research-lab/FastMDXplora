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


def _ended_at_the_first_status(tmp_path, started=None, ended_started=None, app_word=None):
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
        if ended_started == "cleared":
            # A rerun forced in its folder clears the record first.
            live.unlink(missing_ok=True)
        else:
            live.write_text(json.dumps(ended), encoding="utf-8")

    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        for page in _open(session, "#overview"):
            page.set_default_timeout(60000)
            page.expose_function("endTheRun", end_the_run)
            if app_word:
                def app_state(route):
                    answer = route.fetch()
                    state = answer.json()
                    state["status"] = app_word
                    route.fulfill(response=answer, body=json.dumps(state))
                page.route("**/api/app-state*", app_state)
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


def test_a_record_that_says_completed_is_said_so(tmp_path) -> None:
    """The process's word (an earlier run stopped from this page, its stop
    still in the app state) is not taken over a record that says how the
    run ended."""
    pytest.importorskip("playwright.sync_api")
    _, notices = _ended_at_the_first_status(tmp_path, app_word="stopped")
    assert notices == ["study completed"]


STARTED = "2026-10-08T09:00:00+00:00"


@pytest.mark.parametrize(("ended_started", "said"), [
    (STARTED, ["study completed"]), ("2026-10-08T09:30:00+00:00", ["study completed"]),
    ("2026-10-08T08:30:00+00:00", []), (None, []), ("from no record", ["study completed"]),
    ("cleared", [])])
def test_a_notice_is_for_the_run_that_ran(tmp_path, ended_started, said) -> None:
    """A run is the run its live record says started when: the folder's
    record keeps it until a rerun clears it. The same folder's record
    started later and ended is the study run again (a rerun forced in its
    folder that ended before the page saw it going), and says so; one
    started earlier, or a status with no start after one with a start,
    is not this run ending."""
    pytest.importorskip("playwright.sync_api")
    if ended_started == "cleared":
        # Seen running with no start, then its record cleared (a rerun
        # forced in its folder, its own record not written yet): nothing
        # has ended that can be said.
        _, notices = _ended_at_the_first_status(tmp_path, None, "cleared")
    elif ended_started == "from no record":
        # Seen running before its record said when it started (the run's
        # process going, its record not written yet): its end is its end.
        _, notices = _ended_at_the_first_status(tmp_path, None, STARTED)
    else:
        _, notices = _ended_at_the_first_status(tmp_path, STARTED, ended_started)
    assert notices == said


_PAGE_WATCHED = """
    window.__notices = [];
    window.__statuses = 0;
    window.Notification = function (t) { window.__notices.push(t); };
    window.Notification.permission = 'granted';
    localStorage.setItem('fmx.preferences', JSON.stringify({notify: true}));
    window.addEventListener('dashboard:status-updated', () => { window.__statuses += 1; });
"""


def _touch(path):
    """The file written again as it is: the page's change stream asks for
    a poll."""
    path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")


@pytest.mark.parametrize("started", [None, STARTED])
def test_another_study_s_status_ends_nothing(tmp_path, started) -> None:
    """Another study opened elsewhere as a poll was answered: the poll's
    app state is this study's, its status the other's, finished. A run seen
    running here, with or without a start, has not ended, and nothing is
    said; the server names the study each status is of."""
    pytest.importorskip("playwright.sync_api")
    from fastmdxplora.gui.server import start_dashboard_session

    study = _write_study(tmp_path / "study")
    _running(study)
    live = study / "simulation" / "live_status.json"
    if started:
        live.write_text(json.dumps({**json.loads(live.read_text()), "run_started_at": started}),
                        encoding="utf-8")
    other = _write_study(tmp_path / "other")
    other_live = other / "simulation" / "live_status.json"
    other_live.write_text(json.dumps(
        {"status": "completed", "stage": "production", "current_step": 2500,
         "total_steps": 2500}), encoding="utf-8")
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    armed, kept, stale, switched = [], [], [], []

    def app_state(route):
        # The poll after the run was seen running answers this study's app
        # state as it was before the switch, whichever request comes first.
        if armed and not stale:
            stale.append(True)
            route.fulfill(status=200, content_type="application/json", body=kept[-1])
            return
        answer = route.fetch()
        kept.append(answer.text())
        route.fulfill(response=answer)

    def status(route):
        # And its status after the switch: the other study's.
        if armed and not switched:
            switched.append(session.runtime.switch_to(other))
        route.fulfill(response=route.fetch())

    try:
        for page in _open(session, "#overview"):
            page.set_default_timeout(60000)
            page.add_init_script(_PAGE_WATCHED)
            page.route("**/api/app-state*", app_state)
            page.route("**/api/status*", status)
            page.reload(wait_until="domcontentloaded")
            page.wait_for_function("() => /^\\d+% · /.test(document.title)")
            armed.append(True)
            seen = page.evaluate("() => window.__statuses")
            _touch(live)
            page.wait_for_function(f"() => window.__statuses > {seen}")
            # And the next poll, which reads the other study whole.
            seen = page.evaluate("() => window.__statuses")
            _touch(other_live)
            page.wait_for_function(f"() => window.__statuses > {seen}")
            notices = page.evaluate("() => window.__notices")
            assert not page.errors, page.errors
    finally:
        session.server.shutdown()
    assert stale and switched and switched[0]["ok"], (stale, switched)
    assert notices == []


@pytest.mark.parametrize(("how", "said"), [
    ("completed", "study completed"), ("failed", "study failed"), ("stopped", "study stopped")])
def test_a_study_with_no_record_of_its_own_says_its_end(tmp_path, how, said) -> None:
    """A study of several runs keeps its live records in its runs, none at
    its top, and a run's process can end before it writes one: it runs
    while its process does, and its end is said as the process ends, in the
    process's own word (a failure at its start said as failed, not as
    completed)."""
    pytest.importorskip("playwright.sync_api")
    from fastmdxplora.gui.server import start_dashboard_session

    study = _write_study(tmp_path / "study")
    (study / "simulation" / "live_status.json").unlink(missing_ok=True)
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    going = [True]

    def app_state(route):
        answer = route.fetch()
        state = answer.json()
        state["process_running"] = going[0]
        if not going[0]:
            state["status"] = how
            state["returncode"] = {"completed": 0, "failed": 1, "stopped": -15}[how]
        route.fulfill(response=answer, body=json.dumps(state))

    try:
        for page in _open(session, "#overview"):
            page.set_default_timeout(60000)
            page.add_init_script(_PAGE_WATCHED)
            page.route("**/api/app-state*", app_state)
            page.reload(wait_until="domcontentloaded")
            page.wait_for_function(
                "() => document.getElementById('sidebar-progress')"
                ".getAttribute('data-run') === 'running'")
            going[0] = False
            page.wait_for_function("() => window.__notices.length > 0")
            notices = page.evaluate("() => window.__notices")
            assert not page.errors, page.errors
    finally:
        session.server.shutdown()
    assert notices == [said]


def test_the_status_names_its_study(tmp_path) -> None:
    """`/api/status` names the study it read, as a key and not a path."""
    from urllib.request import urlopen

    from fastmdxplora.gui.server import start_dashboard_session

    study = _write_study(tmp_path / "study")
    _running(study)
    other = _write_study(tmp_path / "other")
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        def key():
            with urlopen(session.url + "/api/status") as answer:
                return json.loads(answer.read())["study"]
        first = key()
        session.runtime.switch_to(other)
        second = key()
    finally:
        session.server.shutdown()
    assert first and second and first != second
    assert str(tmp_path) not in first and "/" not in first


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
