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


def test_stop_waits_for_the_run_s_status_before_cmd_k_can_ask(browser, tmp_path):  # noqa: F811
    from fastmdxplora.gui.server import start_dashboard_session

    study = _going(tmp_path / "study", "production")
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    stopped: list = []
    try:
        _with_a_run(page, stopped)
        page.add_init_script("""(() => {
            const fetch = window.fetch;
            const status = new Promise(resolve => { window.__releaseStopStatus = resolve; });
            window.__stopStatusHeld = 0;
            window.fetch = (url, ...options) => {
                if (new URL(url, location.href).pathname === '/api/status') {
                    window.__stopStatusHeld += 1;
                    return status.then(() => fetch(url, ...options));
                }
                return fetch(url, ...options);
            };
        })();""")
        page.goto(session.url + "#analysis", wait_until="domcontentloaded")
        page.wait_for_function("() => window.__stopStatusHeld > 0 && "
                               "window.FastMDXDashboard.state.appState.process_running")
        assert page.evaluate("() => window.FastMDXStop.running()") is False
        assert page.locator("#sb-stop").is_hidden()
        page.keyboard.press("Control+k")
        page.fill("#palette-input", "stop")
        assert page.locator("#palette-list .palette-item").filter(
            has_text="Stop the run").count() == 0
        assert stopped == []
        page.keyboard.press("Escape")
        page.locator("#palette").wait_for(state="hidden")

        page.evaluate("() => window.__releaseStopStatus()")
        page.wait_for_function("() => window.FastMDXStop.running()")
        page.keyboard.press("Control+k")
        page.fill("#palette-input", "stop")
        page.keyboard.press("Enter")
        page.wait_for_selector("#health-stop-ask .stop-run-yes")
        question = page.text_content("#health-stop-ask")
        assert page.evaluate("() => document.activeElement.textContent") == "Not now"
        assert "checkpoint there" in question
        page.keyboard.press("Enter")
        page.locator("#health-stop-ask").wait_for(state="hidden")
        assert stopped == []
    finally:
        page.close()
        session.server.shutdown()


def test_a_run_without_telemetry_can_still_be_stopped(browser, tmp_path):  # noqa: F811
    from fastmdxplora.gui.server import start_dashboard_session

    study = _going(tmp_path / "study", "production")
    (study / "simulation" / "live_status.json").unlink()
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    stopped: list = []
    try:
        _with_a_run(page, stopped)
        page.goto(session.url + "#analysis", wait_until="domcontentloaded")
        page.wait_for_function("() => window.FastMDXStop && window.FastMDXStop.running()")
        assert page.evaluate("() => window.FastMDXDashboard.state.status") == {}
        page.click("#sb-stop")
        page.wait_for_selector("#sb-stop-ask .stop-run-yes")
        assert page.evaluate("() => document.activeElement.textContent") == "Not now"
        assert stopped == []
        page.click("#sb-stop-ask .stop-run-yes")
        page.wait_for_function(
            "() => document.getElementById('sb-stop-ask').textContent === 'Stopped.'")
        assert stopped == ["POST"]
    finally:
        page.close()
        session.server.shutdown()


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
