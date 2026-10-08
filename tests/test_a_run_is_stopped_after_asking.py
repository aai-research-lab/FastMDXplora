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
    assert "wrote no checkpoint" in remedy.fix
    assert "Production already written is kept" not in remedy.fix


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


@pytest.mark.parametrize("where", ["sidebar-stop", "health-stop", "run-stop"])
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
        page.goto(session.url + ("#run" if where == "run-stop" else "#overview"),
                  wait_until="domcontentloaded")
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
        page.wait_for_selector("#sidebar-stop:not([hidden])")
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
