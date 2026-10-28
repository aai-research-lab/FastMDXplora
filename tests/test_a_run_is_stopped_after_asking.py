"""Stop the run: offered where the run is followed, asked once more, said
as a stop.

A run could be stopped only from New study, at one press with nothing
asked. The builder read the server's answer as a failure ("Could not
stop."), the page then said the log's last line, a citation, with "exit
code -15" as why it failed; a run stopped before production left a record
saying "running", which the open page kept saying and, read again, called
"Interrupted: the machine restarted", offering to carry it on from a
checkpoint it never wrote.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_the_sidebar_says_the_study_and_where_its_run_stands import (  # noqa: F401
    _live, browser)
from tests.test_the_workspace_says_its_studies import _study


def _going(root: Path, stage: str) -> Path:
    study = _study(root)
    (study / "manifest.json").unlink()
    return _live(study, status="running", stage=stage, current_step=0,
                 total_planned_steps=5000, current_checkpoint_path="simulation/checkpoint.chk")


def _runtime_running(study: Path):
    from fastmdxplora.gui.exploration import DashboardRuntime

    runtime = DashboardRuntime(workspace_root=study.parent, exploration_root=study.parent)
    runtime.active_root = study
    runtime.running_root = study
    runtime.process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"],
                                       start_new_session=True)
    return runtime


def test_a_run_stopped_before_production_is_recorded_stopped(tmp_path):
    from fastmdxplora.gui.telemetry import analyze_health, status_as_it_stands

    study = _going(tmp_path / "study", "minimization")
    runtime = _runtime_running(study)
    said = runtime.stop()
    assert said["stopped"]
    state = runtime.snapshot()
    assert state["status"] == "stopped" and state["error"] is None
    record = json.loads((study / "simulation" / "live_status.json").read_text())
    assert record["status"] == "stopped"
    assert "minimisation" in record["latest_error"]
    assert "no checkpoint" in record["latest_error"]
    stands = status_as_it_stands(study)
    assert stands["status"] == "stopped"
    assert stands["current_checkpoint_path"] is None
    health = analyze_health(stands, [])
    assert health["state"] == "stopped"


def test_it_is_offered_to_run_again_not_from_a_checkpoint(tmp_path):
    from fastmdxplora.remedies import remedies_of

    study = _going(tmp_path / "study", "minimization")
    _runtime_running(study).stop()
    [remedy] = remedies_of(study)
    assert remedy.code == "simulation.run.stopped"
    assert "Production already written is kept" not in remedy.fix
    # Said once, in the stop's own words: the card read "wrote no
    # checkpoint" in the reason and again in the fix.
    assert "no checkpoint" in remedy.why
    assert "checkpoint" not in remedy.fix
    assert remedy.fix == "Run it again from the start."


def test_the_problems_list_says_once_that_a_stop_in_nvt_has_no_checkpoint(tmp_path):
    """The Problems list's reason said the stop wrote no checkpoint, and its
    fix said again "it wrote no checkpoint to carry on from": the fix was
    read from the reason's first sentence only."""
    from fastmdxplora.gui.problems_view import problems_payload

    study = _going(tmp_path / "study", "nvt")
    _runtime_running(study).stop()
    [item] = [item for item in problems_payload(study)["items"]
              if item["id"] in ("health:stopped", "simulation.run.stopped")]
    assert "NVT equilibration" in item["reason"] and "no checkpoint" in item["reason"]
    assert item["fix"] == "Run it again from the start."
    assert f"{item['reason']} {item['fix']}".count("checkpoint") == 1


def test_a_run_stopped_in_production_keeps_what_it_recorded(tmp_path):
    study = _going(tmp_path / "study", "production")
    runtime = _runtime_running(study)
    # The run records its own stop in production; nothing is written over it.
    _live(study, status="stopped", stage="production", latest_error="Production was stopped.")
    runtime.stop()
    record = json.loads((study / "simulation" / "live_status.json").read_text())
    assert record["latest_error"] == "Production was stopped."


def _with_a_run(page, stopped: list) -> None:
    def app_state(route):
        response = route.fetch()
        body = response.json()
        body.update(process_running=not stopped, status="stopped" if stopped else "running")
        route.fulfill(response=response, json=body)

    def stop(route):
        stopped.append(route.request.method)
        route.fulfill(json={"stopped": True, "detail": "Workflow stopped."})

    page.route("**/api/app-state*", app_state)
    page.route("**/api/explore/stop", stop)


def test_stop_beside_the_run_in_the_status_bar_and_on_its_overview():
    """The sidebar's Stop and the builder's went; Stop is beside the run in
    the status bar (user, 10-18) and on the Overview beside its health."""
    from fastmdxplora.gui.server import _load_template

    page = _load_template()
    assert page.count("data-stop-run=") == 2
    assert 'data-stop-run="health-stop-ask"' in page and 'data-stop-run="sb-stop-ask"' in page
    bar = page[page.index('id="status-bar"'):page.index("</footer>")]
    assert 'id="sb-stop"' in bar
    assert 'id="sidebar-stop"' not in page and 'id="run-stop"' not in page


@pytest.mark.parametrize("where", ["health-stop", "sb-stop"])
def test_stop_asks_first_and_says_it_stopped(browser, tmp_path, where):  # noqa: F811
    from fastmdxplora.gui.server import start_dashboard_session

    study = _going(tmp_path / "study", "nvt")
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    stopped: list = []
    errors: list = []
    try:
        page.set_default_timeout(60000)
        page.on("pageerror", lambda error: errors.append(str(error)))
        _with_a_run(page, stopped)
        # The status bar's Stop from another page than the Overview.
        shown = "#analysis" if where == "sb-stop" else "#overview"
        page.goto(session.url + shown, wait_until="domcontentloaded")
        page.wait_for_selector(f"#{where}:not([hidden])")
        page.click(f"#{where}")
        ask = f"#{where}-ask"
        page.wait_for_selector(f"{ask} .stop-run-yes")
        question = page.text_content(ask)
        page.click(f"{ask} .stop-run-no")
        assert stopped == []                       # nothing until it is answered yes
        page.click(f"#{where}")
        page.evaluate("() => { window.__fixesAsked = 0; const load = window.FastMDXFixes.load; "
                      "window.FastMDXFixes.load = () => { window.__fixesAsked += 1; "
                      "return load(); }; }")
        page.click(f"{ask} .stop-run-yes")
        page.wait_for_function(f"() => document.querySelector('{ask}').textContent === 'Stopped.'")
        # What would fix it is asked for again once the stop is recorded.
        page.wait_for_function("() => window.__fixesAsked > 0", timeout=10000)
    finally:
        page.close()
        session.server.shutdown()
    assert errors == []
    assert stopped == ["POST"]
    assert question.startswith("Stop the run?") and "no checkpoint" in question


def test_stop_is_offered_by_cmd_k_while_a_run_goes(browser, tmp_path):  # noqa: F811
    from fastmdxplora.gui.server import start_dashboard_session

    study = _going(tmp_path / "study", "production")
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    stopped: list = []
    try:
        page.set_default_timeout(60000)
        _with_a_run(page, stopped)
        page.goto(session.url + "#analysis", wait_until="domcontentloaded")
        page.wait_for_function("() => window.FastMDXStop && window.FastMDXStop.running()")
        page.keyboard.press("Control+k")
        page.fill("#palette-input", "stop")
        page.keyboard.press("Enter")
        page.wait_for_function("() => document.documentElement.dataset.page === 'overview'")
        page.wait_for_selector("#health-stop-ask .stop-run-yes")
        question = page.text_content("#health-stop-ask")
        # Enter once more answers "Not now", not "Yes, stop it".
        focused = page.evaluate("() => document.activeElement.textContent")
        page.keyboard.press("Enter")
        page.wait_for_timeout(300)
    finally:
        page.close()
        session.server.shutdown()
    assert focused == "Not now"
    assert stopped == []
    assert "checkpoint there" in question


def test_a_stop_s_answer_goes_once_another_run_starts(browser, tmp_path):  # noqa: F811
    """Stopped, then carried on with What would fix it, Run it: "Stopped."
    stayed under a live Stop the run the whole carry-on through."""
    from fastmdxplora.gui.server import start_dashboard_session

    study = _going(tmp_path / "study", "production")
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    going = [True]

    def app_state(route):
        response = route.fetch()
        body = response.json()
        body.update(process_running=going[0], status="running" if going[0] else "stopped")
        route.fulfill(response=response, json=body)

    def stop(route):
        going[0] = False
        route.fulfill(json={"stopped": True, "detail": "Workflow stopped."})

    try:
        page.set_default_timeout(60000)
        page.route("**/api/app-state*", app_state)
        page.route("**/api/explore/stop", stop)
        page.goto(session.url + "#overview", wait_until="domcontentloaded")
        page.wait_for_selector("#health-stop:not([hidden])")
        page.click("#health-stop")
        page.click("#health-stop-ask .stop-run-yes")
        page.wait_for_function(
            "() => document.querySelector('#health-stop-ask').textContent === 'Stopped.'")
        page.wait_for_selector("#health-stop", state="hidden")
        going[0] = True                            # the study carried on
        page.wait_for_selector("#health-stop:not([hidden])")
        page.wait_for_function(
            "() => !document.querySelector('#health-stop-ask').textContent")
    finally:
        page.close()
        session.server.shutdown()


def test_a_stop_s_card_is_named_alike_wherever_it_is_led_to(browser, tmp_path):  # noqa: F811
    """The card of a run stopped as asked is "To carry it on". The status
    strip's link to it and the Problems list's heading for the same stop
    still said "What would fix it"."""
    from fastmdxplora.gui.server import start_dashboard_session

    study = _going(tmp_path / "study", "nvt")
    _runtime_running(study).stop()
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.add_init_script("localStorage.setItem('fmx.panelCollapsed', '0');")
    page = context.new_page()
    errors: list = []
    try:
        page.set_default_timeout(60000)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(session.url + "#overview", wait_until="domcontentloaded")
        page.wait_for_selector("#fixes-card:not([hidden]) #fixes-list li")
        card = page.text_content("#fixes-card .card-title")
        page.wait_for_selector("#status-fix", state="visible")
        link = page.text_content("#status-fix")
        if page.locator("#side-expand").is_visible():
            page.locator("#side-expand").click()
        page.locator("#problems-tab").click()
        page.wait_for_selector("#problems-body .problem-item[data-problem-id] h4")
        headings = page.eval_on_selector_all(
            "#problems-body .problem-item", "items => items.map(i => [i.dataset.problemId, "
            "i.querySelector('h4').textContent])")
    finally:
        context.close()
        session.server.shutdown()
    assert errors == []
    assert card == "To carry it on"
    assert link == "To carry it on"
    assert headings == [["health:stopped", "To carry it on"]], headings


def test_a_stop_refused_is_said_at_once_until_it_can_be_stopped(browser, tmp_path):  # noqa: F811
    """A run in the GUI's own process, outside production, cannot be stopped
    from it. The page asked "Stop the run?" and then refused, and the status
    bar's answer went after four seconds; under the Overview's Stop it stayed
    into production, where Stop works, and after the run. It is said at
    once, kept while it holds, and goes once the run can be stopped."""
    from fastmdxplora.gui.server import start_dashboard_session

    own_stop_refused = (
        "This run is in this GUI's own process; stopping it here would end the GUI too. "
        "Stop it where it was started (Ctrl+C at its terminal, or interrupt the notebook's "
        "cell).")

    study = _going(tmp_path / "study", "nvt")
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    refused = [own_stop_refused]
    asked: list = []
    errors: list = []

    def app_state(route):
        response = route.fetch()
        body = response.json()
        body.update(process_running=True, status="running", stop_refused=refused[0])
        route.fulfill(response=response, json=body)

    try:
        page.set_default_timeout(60000)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/api/app-state*", app_state)
        page.route("**/api/explore/stop", lambda route: (asked.append(1), route.fulfill(
            json={"stopped": True, "detail": "Stopping."})))
        page.goto(session.url + "#analysis", wait_until="domcontentloaded")
        page.wait_for_selector("#sb-stop:not([hidden])")
        page.wait_for_timeout(1500)
        page.click("#sb-stop")
        page.wait_for_selector("#sb-stop-ask .stop-run-refused")
        questioned = page.locator("#sb-stop-ask .stop-run-yes").count()
        page.wait_for_timeout(6000)
        kept = (page.evaluate("() => document.querySelector('#sb-stop-ask .stop-run-refused')"
                              ".firstChild.textContent")
                if page.is_visible("#sb-stop-ask") else "")
        # It can be put away, by a button said as one, and the keyboard
        # goes back to the Stop that asked.
        close = page.locator("#sb-stop-ask .stop-run-close")
        named = (close.get_attribute("aria-label"), "ghost-btn" in close.get_attribute("class"))
        # On a line of its own: neither it nor its ring covers the words.
        apart = page.evaluate("""() => {
            const said = document.querySelector('#sb-stop-ask .stop-run-refused');
            const range = document.createRange();
            range.selectNodeContents(said.firstChild);
            const words = [...range.getClientRects()];
            const close = said.querySelector('.stop-run-close').getBoundingClientRect();
            return words.every(r => r.bottom <= close.top + 0.5);
        }""")
        close.focus()
        page.keyboard.press("Enter")
        closed = page.is_hidden("#sb-stop-ask")
        focused = page.evaluate("() => document.activeElement.id || document.activeElement.tagName")
        # Asked again: the first card's timer does not take the second away.
        page.click("#sb-stop")
        page.wait_for_selector("#sb-stop-ask .stop-run-refused")
        page.wait_for_timeout(7000)
        kept_again = page.is_visible("#sb-stop-ask .stop-run-refused")
        # Stop pressed again over a card still up: its timer goes with it,
        # and the new card has the whole of its own.
        page.click("#sb-stop")
        page.wait_for_timeout(7000)
        kept_over = page.is_visible("#sb-stop-ask .stop-run-refused")
        refused[0] = None   # production reached: Stop works here now
        page.evaluate("() => document.getElementById('refresh-now').click()")
        page.wait_for_selector("#sb-stop-ask .stop-run-refused", state="detached")
        # Once it can be stopped, Stop asks first again.
        page.click("#sb-stop")
        page.wait_for_selector("#sb-stop-ask .stop-run-yes")
    finally:
        page.close()
        session.server.shutdown()
    assert errors == []
    assert questioned == 0 and asked == []
    assert kept == own_stop_refused and closed
    assert named == ("Close the note", True)
    assert focused == "sb-stop", focused
    assert apart
    assert kept_again and kept_over


def test_a_run_asked_to_stop_is_said_stopping_and_offers_no_stop(browser, tmp_path):  # noqa: F811
    """A run of the GUI's own process asked to stop goes on to its next
    frame: the page said "Stopped.", went on offering Stop, and a second
    press of a parallel study's killed its workers. It says "Stopping..." in
    the status bar and on the Overview, and offers no Stop, until the run
    has stopped."""
    from fastmdxplora.gui.server import start_dashboard_session

    study = _going(tmp_path / "study", "production")
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    state = {"running": True, "stopping": False}
    errors: list = []

    def app_state(route):
        response = route.fetch()
        body = response.json()
        body.update(process_running=state["running"], stopping=state["stopping"],
                    status="running" if state["running"] else "stopped")
        route.fulfill(response=response, json=body)

    def stop(route):
        # Said by the answer: the app state, read again only half a minute
        # later in a quiet phase, says nothing of it yet.
        route.fulfill(json={"stopped": True, "detail": "Stopping: production ends at its "
                            "next frame with a checkpoint there.",
                            "state": {"process_running": True, "stopping": True}})

    try:
        page.set_default_timeout(60000)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/api/app-state*", app_state)
        page.route("**/api/explore/stop", stop)
        page.goto(session.url + "#overview", wait_until="domcontentloaded")
        page.wait_for_selector("#sb-stop:not([hidden])")
        page.click("#sb-stop")
        page.click("#sb-stop-ask .stop-run-yes")
        page.wait_for_selector("#sb-stop-ask .stop-run-stopping")
        page.wait_for_selector("#health-stop-ask .stop-run-stopping", state="attached")
        offered_at_once = page.evaluate(
            "() => [...document.querySelectorAll('[data-stop-run]')].filter(b => !b.hidden).length")
        state["stopping"] = True
        page.wait_for_timeout(5000)
        during = page.evaluate("""() => ({
            bar: document.querySelector('#sb-stop-ask').textContent,
            overview: document.querySelector('#health-stop-ask').textContent,
            offered: [...document.querySelectorAll('[data-stop-run]')].filter(b => !b.hidden).length,
            palette: window.FastMDXStop.running(),
        })""")
        state.update(running=False, stopping=False)
        page.evaluate("() => document.getElementById('refresh-now').click()")
        page.wait_for_selector(".stop-run-stopping", state="detached")
    finally:
        page.close()
        session.server.shutdown()
    assert errors == []
    assert offered_at_once == 0
    assert during == {"bar": "Stopping…", "overview": "Stopping…", "offered": 0,
                      "palette": False}, during


def test_a_study_of_runs_in_production_is_asked_as_production(browser, tmp_path):  # noqa: F811
    """A study of runs has no stage of its own: Stop asked it as one that
    had not reached production, with its runs in production, and a stop
    that keeps their work was said to leave nothing to carry on."""
    from fastmdxplora.gui.server import start_dashboard_session

    study = _going(tmp_path / "study", "production")
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    errors: list = []

    def app_state(route):
        response = route.fetch()
        body = response.json()
        body.update(process_running=True, status="running", runs=[
            {"run_id": "a", "state": "running", "stage": "npt"},
            {"run_id": "b", "state": "running", "stage": "production"}])
        route.fulfill(response=response, json=body)

    def status(route):
        response = route.fetch()
        body = response.json()
        (body.get("status") or {}).pop("stage", None)   # a study of runs has none of its own
        route.fulfill(response=response, json=body)

    try:
        page.set_default_timeout(60000)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/api/app-state*", app_state)
        page.route("**/api/status*", status)
        page.route("**/api/stream*", lambda route: route.abort())  # read as polled
        page.goto(session.url + "#overview", wait_until="domcontentloaded")
        page.wait_for_selector("#sb-stop:not([hidden])")
        page.wait_for_timeout(1500)
        page.click("#sb-stop")
        page.wait_for_selector("#sb-stop-ask .stop-run-yes")
        question = page.text_content("#sb-stop-ask")
        page.click("#sb-stop-ask .stop-run-no")
    finally:
        page.close()
        session.server.shutdown()
    assert errors == []
    assert "ends at its next frame with a checkpoint" in question, question
    assert "not reached production" not in question


def test_a_study_whose_process_goes_on_is_said_running(browser, tmp_path):  # noqa: F811
    """A study of runs, one stopped and another going on to its frame: its
    status said stopped, and the page said Stopped with a run still going."""
    from fastmdxplora.gui.server import start_dashboard_session

    study = _going(tmp_path / "study", "production")
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    errors: list = []

    def app_state(route):
        response = route.fetch()
        body = response.json()
        body.update(process_running=True, status="running")
        route.fulfill(response=response, json=body)

    def status(route):
        response = route.fetch()
        body = response.json()
        body.setdefault("status", {})["status"] = "stopped"
        route.fulfill(response=response, json=body)

    try:
        page.set_default_timeout(60000)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/api/app-state*", app_state)
        page.route("**/api/status*", status)
        page.route("**/api/stream*", lambda route: route.abort())  # read as polled
        page.goto(session.url + "#overview", wait_until="domcontentloaded")
        page.wait_for_selector("#sb-stop:not([hidden])")
        page.wait_for_timeout(1500)
        said = page.text_content("#topbar-status-text")
        run = page.get_attribute("#status-run", "data-run")
    finally:
        page.close()
        session.server.shutdown()
    assert errors == []
    assert (said, run) == ("Running", "running"), (said, run)


#: A state read asked before Stop's Yes, its answer held until a moment
#: after Stop's own answer has been taken.
_LATE_READ = """(() => {
  const real = window.fetch;
  let answered; const stopAnswered = new Promise((done) => { answered = done; });
  window.__holdNextRead = false;
  window.fetch = function (url) {
    const asked = real.apply(this, arguments);
    const at = String(url);
    if (at.includes('/api/explore/stop')) {
      asked.then(() => setTimeout(answered, 300));
    } else if (window.__holdNextRead && at.includes('/api/app-state')) {
      window.__holdNextRead = false;
      return asked.then((res) => stopAnswered.then(() => res));
    }
    return asked;
  };
})();"""


def test_a_read_asked_before_stop_and_answered_after_it_puts_no_stop_back(browser, tmp_path):  # noqa: F811
    """A state read asked before Yes and answered after Stop's answer said
    the run was not stopping: Stop came back, Cmd+K's with it, until the
    next read, half a minute later in a quiet phase."""
    from fastmdxplora.gui.server import start_dashboard_session

    study = _going(tmp_path / "study", "production")
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    state = {"stopping": False}
    errors: list = []

    def app_state(route):
        response = route.fetch()
        body = response.json()
        body.update(process_running=True, stopping=state["stopping"], status="running")
        route.fulfill(response=response, json=body)

    def stop(route):
        # Answered as the server answers, its state read numbered after any
        # read asked before it.
        read = route.fetch(url=session.url.split("#")[0].rstrip("/") + "/api/app-state",
                           method="GET").json().get("read")
        state["stopping"] = True
        route.fulfill(json={"stopped": True, "detail": "Stopping: production ends at its "
                            "next frame.", "state": {"process_running": True, "stopping": True,
                                                     "read": read}})

    try:
        page.set_default_timeout(60000)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.add_init_script(_LATE_READ)
        page.route("**/api/app-state*", app_state)
        page.route("**/api/explore/stop", stop)
        page.route("**/api/stream*", lambda route: route.abort())   # a quiet phase
        page.goto(session.url + "#overview", wait_until="domcontentloaded")
        page.wait_for_selector("#sb-stop:not([hidden])")
        page.wait_for_timeout(1500)
        page.click("#sb-stop")
        page.evaluate("() => { window.__holdNextRead = true; "
                      "document.getElementById('refresh-now').click(); }")
        page.wait_for_timeout(300)
        page.click("#sb-stop-ask .stop-run-yes")
        page.wait_for_selector("#sb-stop-ask .stop-run-stopping")
        page.wait_for_timeout(1500)   # the late read has landed
        after = page.evaluate("""() => ({
            offered: [...document.querySelectorAll('[data-stop-run]')].filter(b => !b.hidden).length,
            palette: window.FastMDXStop.running(),
            said: document.querySelector('#sb-stop-ask').textContent,
        })""")
    finally:
        page.close()
        session.server.shutdown()
    assert errors == []
    assert after == {"offered": 0, "palette": False, "said": "Stopping…"}, after


def test_a_stop_refused_from_cmd_k_puts_the_keyboard_on_its_note(browser, tmp_path):  # noqa: F811
    """Cmd+K's Stop, refused, says so under the Overview's Stop; the keyboard
    stayed in the status bar, and Tab never reached the note's Close."""
    from fastmdxplora.gui.server import start_dashboard_session

    study = _going(tmp_path / "study", "nvt")
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    errors: list = []

    def app_state(route):
        response = route.fetch()
        body = response.json()
        body.update(process_running=True, status="running", stop_refused="Not here.")
        route.fulfill(response=response, json=body)

    try:
        page.set_default_timeout(60000)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/api/app-state*", app_state)
        page.goto(session.url + "#overview", wait_until="domcontentloaded")
        page.wait_for_selector("#sb-stop:not([hidden])")
        page.wait_for_timeout(1500)
        page.keyboard.press("Control+k")
        page.keyboard.type("stop")
        page.wait_for_timeout(500)
        page.keyboard.press("Enter")
        page.wait_for_selector("#health-stop-ask .stop-run-refused")
        page.wait_for_timeout(300)
        focused = page.evaluate("() => document.activeElement.matches('#health-stop-ask .stop-run-close')")
    finally:
        page.close()
        session.server.shutdown()
    assert errors == []
    assert focused
