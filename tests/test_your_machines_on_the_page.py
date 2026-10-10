"""Your machines on the page: Run on a machine, and Remote jobs.

`fastmdx remote` could send a study to a machine and bring it back only at
a terminal; the GUI had the routes (`gui/remote_routes.py`) and no page.
The Config Builder now offers **Plan the send** beside **Run on this
machine** where a machine has been recorded: the plan is shown (what
travels, each size, where it runs, the job script) and nothing goes until
**Send** is pressed. All studies lists the jobs sent from the workspace,
each asked how it is doing, fetched once its sizes are said, or stopped
once asked. The machine is this computer standing in, as in
`test_a_study_travels_and_comes_back`.
"""

from __future__ import annotations

import os

import pytest

from tests import test_a_study_travels_and_comes_back as travels
from tests.test_a_study_travels_and_comes_back import RELEASE

pytest.importorskip("playwright.sync_api")

machine = travels.machine
machine_path = travels.machine_path

SHOTS = os.environ.get("FMDX_SHOTS")


@pytest.fixture
def session(machine, monkeypatch):
    from fastmdxplora.gui.server import start_dashboard_session
    from fastmdxplora.remote import api
    from fastmdxplora.remote import send as sending

    monkeypatch.setattr(api, "_link", lambda name, transport: machine.transport())
    monkeypatch.setattr(api, "this_code", lambda: RELEASE)
    monkeypatch.setattr(sending, "this_code", lambda: RELEASE)
    real = sending.run_here
    monkeypatch.setattr(sending, "run_here", lambda command, runner=None, **more:
                        real(command, runner=machine.local, **more))
    session = start_dashboard_session(output=str(machine.study.parent), host="127.0.0.1",
                                      port=0)
    try:
        yield session, machine
    finally:
        session.server.shutdown()


def _page(pw, url, width=1400, height=900):
    browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
    page = browser.new_page(viewport={"width": width, "height": height})
    page.set_default_timeout(60000)
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(url, wait_until="domcontentloaded")
    return browser, page, errors


def _shot(page, name):
    if SHOTS:
        page.screenshot(path=os.path.join(SHOTS, name), full_page=False)


def _config_checked(page, path):
    page.evaluate("() => window.FastMDXRun.setStart('config')")
    page.fill("#run-config-path", str(path))
    page.click("#run-check-config")
    page.wait_for_function("() => !document.getElementById('run-as-is').disabled")


def test_a_study_is_planned_shown_sent_followed_and_fetched(session) -> None:
    from playwright.sync_api import sync_playwright

    served, machine = session
    with sync_playwright() as pw:
        browser, page, errors = _page(pw, served.url + "#run")
        _config_checked(page, machine.study)
        page.wait_for_selector("#run-remote:not([hidden]) #run-remote-plan:not([disabled])")
        machines = page.eval_on_selector_all("#run-remote-machine option",
                                             "os => os.map(o => o.value)")
        page.click("#run-remote-plan")
        page.wait_for_selector(".remote-plan-title")
        page.locator("#run-remote").scroll_into_view_if_needed()
        _shot(page, "plan.png")
        planned = page.text_content("#run-remote-planned")
        sent_before = any(c.startswith("mkdir") for c in machine.commands)
        page.click("#run-remote-planned .primary-btn")
        page.wait_for_function(
            "() => document.getElementById('run-remote-note').textContent.startsWith('Sent')")
        said = page.text_content("#run-remote-note")
        travels._until_finished(machine, "study")
        page.click("#run-remote-note .builder-linkish")
        page.wait_for_selector("#remote-jobs:not([hidden]) .remote-job")
        page.click(".remote-job .ghost-btn >> text=Ask how it is doing")
        page.wait_for_selector(".remote-job-state[data-state='done']")
        _shot(page, "jobs.png")
        page.click(".remote-job .ghost-btn >> text=Fetch the results")
        page.wait_for_selector(".remote-job-said .primary-btn")
        asked = page.text_content(".remote-job-said")
        _shot(page, "fetch.png")
        page.click(".remote-job-said .primary-btn")
        page.wait_for_function("() => [...document.querySelectorAll('.remote-job-said')]"
                               ".some(n => n.textContent.startsWith('Fetched into'))")
        browser.close()
    assert machines == ["box"]
    assert "Send to box?" in planned and "top.pdb (5 bytes)" in planned
    assert "The job script" in planned
    assert not sent_before
    assert said.startswith("Sent to box as job study.")
    assert asked.startswith("Bring ") and "into study?" in asked
    assert (machine.study.parent / "study" / "manifest.json").is_file()
    assert errors == []


def test_nothing_is_sent_without_send_and_nothing_shown_without_a_machine(
        session, monkeypatch) -> None:
    from playwright.sync_api import sync_playwright

    from fastmdxplora.remote import api

    served, machine = session
    with sync_playwright() as pw:
        browser, page, errors = _page(pw, served.url + "#run")
        _config_checked(page, machine.study)
        page.wait_for_selector("#run-remote-plan:not([disabled])")
        page.click("#run-remote-plan")
        page.wait_for_selector(".remote-plan-title")
        page.click("#run-remote-planned .ghost-btn")
        gone = page.is_hidden("#run-remote-planned")
        monkeypatch.setattr(api, "machines", lambda: [])
        page.evaluate("() => window.FastMDXRemote.refresh('run')")
        page.wait_for_selector("#run-remote", state="hidden")
        browser.close()
    assert gone
    assert not any(c.startswith("mkdir") for c in machine.commands)
    assert errors == []


def test_a_running_job_is_stopped_once_asked(session) -> None:
    from playwright.sync_api import sync_playwright

    from fastmdxplora.remote.jobs import load_job
    from tests.test_remote_from_every_interface import _group_killed

    served, machine = session
    machine.env["FAKE_SLEEP"] = "30"
    with sync_playwright() as pw:
        browser, page, errors = _page(pw, served.url + "#run")
        try:
            _config_checked(page, machine.study)
            page.wait_for_selector("#run-remote-plan:not([disabled])")
            page.click("#run-remote-plan")
            page.wait_for_selector(".remote-plan-title")
            page.click("#run-remote-planned .primary-btn")
            page.wait_for_function("() => document.getElementById('run-remote-note')"
                                   ".textContent.startsWith('Sent')")
            page.evaluate("() => window.FastMDXDashboard.navigate('studies')")
            page.wait_for_selector(".remote-job .ghost-btn >> text=Stop it")
            page.click(".remote-job .ghost-btn >> text=Stop it")
            asked = page.text_content(".remote-job-said")
            page.click(".remote-job-said .danger-btn")
            page.wait_for_selector(".remote-job-state[data-state='abandoned']")
            browser.close()
        finally:
            _group_killed(load_job("study").handle)
    assert asked.startswith("Stop study on box?")
    assert errors == []


def test_a_phone_sees_the_plan_within_its_width(session) -> None:
    from playwright.sync_api import sync_playwright

    served, machine = session
    with sync_playwright() as pw:
        browser, page, errors = _page(pw, served.url + "#run", width=390, height=844)
        _config_checked(page, machine.study)
        page.wait_for_selector("#run-remote-plan:not([disabled])")
        page.click("#run-remote-plan")
        page.wait_for_selector(".remote-plan-title")
        page.locator("#run-remote").scroll_into_view_if_needed()
        _shot(page, "plan-phone.png")
        wide = page.evaluate("""() => {
          const host = document.getElementById('run-remote');
          return [...host.querySelectorAll('*')].filter(
            n => n.getBoundingClientRect().right > innerWidth + 1
                 && !n.closest('pre') && !n.closest('details:not([open])')).length; }""")
        browser.close()
    assert wide == 0
    assert errors == []


def test_the_study_built_is_what_is_planned(session) -> None:
    """Away from a config file, the plan is of the study the builder holds,
    as Run on this machine would run it, and only once it could run here."""
    import json

    from playwright.sync_api import sync_playwright

    served, machine = session
    with sync_playwright() as pw:
        browser, page, errors = _page(pw, served.url + "#run")
        page.wait_for_selector("#run-remote:not([hidden])")
        waits = page.is_disabled("#run-remote-plan")
        asked = []

        def planned(route):
            asked.append(json.loads(route.request.post_data))
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"ok": False, "error": "Not this time."}))

        page.route("**/api/remote/plan", planned)
        # As the builder says once the study could run here.
        page.evaluate("() => document.getElementById('run-start-button').disabled = false")
        page.wait_for_selector("#run-remote-plan:not([disabled])")
        page.click("#run-remote-plan")
        page.wait_for_function("() => document.getElementById('run-remote-note')"
                               ".textContent === 'Not this time.'")
        built = page.evaluate("() => window.FastMDXRun.currentState()")
        browser.close()
    assert waits
    assert asked == [{"state": built, "machine": "box"}]
    assert errors == []


def test_a_plan_is_taken_away_once_the_study_changes(session) -> None:
    """First review: a plan stayed beside a form changed after it, and
    Send sent the study as it had been."""
    from playwright.sync_api import sync_playwright

    served, machine = session
    with sync_playwright() as pw:
        browser, page, errors = _page(pw, served.url + "#run")
        _config_checked(page, machine.study)
        page.wait_for_selector("#run-remote-plan:not([disabled])")
        page.click("#run-remote-plan")
        page.wait_for_selector(".remote-plan-title")
        focused = page.evaluate("() => document.activeElement.className")
        page.fill("#run-config-path", str(machine.study) + " ")
        gone = page.is_hidden("#run-remote-planned")
        said = page.text_content("#run-remote-note")
        browser.close()
    assert focused == "remote-plan-title"
    assert gone and said.startswith("The study changed after the plan was shown.")
    assert not any(c.startswith("mkdir") for c in machine.commands)
    assert errors == []


def test_what_a_send_answers_is_said_though_the_page_was_drawn_again(session) -> None:
    """First review: the page drawn again while a send was out lost what
    it answered, a refusal included."""
    import json

    from playwright.sync_api import sync_playwright

    served, machine = session
    with sync_playwright() as pw:
        browser, page, errors = _page(pw, served.url + "#run")
        _config_checked(page, machine.study)
        page.wait_for_selector("#run-remote-plan:not([disabled])")
        page.click("#run-remote-plan")
        page.wait_for_selector(".remote-plan-title")
        held = []
        page.route("**/api/remote/send", lambda route: held.append(route))
        page.click("#run-remote-planned .primary-btn")
        page.wait_for_function("() => document.getElementById('run-remote-note')"
                               ".textContent.startsWith('Sending')")
        page.evaluate("() => window.FastMDXDashboard.navigate('run')")
        page.wait_for_timeout(800)
        while not held:
            page.wait_for_timeout(100)
        held[0].fulfill(status=200, content_type="application/json",
                        body=json.dumps({"ok": False, "error": "Refused there."}))
        page.wait_for_function("() => document.getElementById('run-remote-note')"
                               ".textContent === 'Refused there.'")
        browser.close()
    assert errors == []


def test_machines_that_cannot_be_read_are_said(session, monkeypatch) -> None:
    from playwright.sync_api import sync_playwright

    from fastmdxplora.remote import api

    served, machine = session

    def unreadable():
        raise OSError("the jobs record is not readable")

    monkeypatch.setattr(api, "jobs", lambda under=None: unreadable())
    with sync_playwright() as pw:
        browser, page, errors = _page(pw, served.url + "#run")
        page.wait_for_selector("#run-remote:not([hidden])")
        said = page.text_content("#run-remote")
        page.evaluate("() => window.FastMDXDashboard.navigate('studies')")
        page.wait_for_selector("#remote-jobs:not([hidden])")
        listed = page.text_content("#remote-jobs")
        browser.close()
    assert said.startswith("Your machines could not be read:")
    assert "They could not be read:" in listed
    assert errors == []


def test_a_fetch_out_holds_its_row_and_says_its_answer(session) -> None:
    """Second review: asking about a job while it was being fetched, or
    leaving the page and coming back, lost what the fetch answered, and
    the page threw."""
    from playwright.sync_api import sync_playwright

    served, machine = session
    with sync_playwright() as pw:
        browser, page, errors = _page(pw, served.url + "#run")
        _config_checked(page, machine.study)
        page.wait_for_selector("#run-remote-plan:not([disabled])")
        page.click("#run-remote-plan")
        page.wait_for_selector(".remote-plan-title")
        page.click("#run-remote-planned .primary-btn")
        page.wait_for_function("() => document.getElementById('run-remote-note')"
                               ".textContent.startsWith('Sent')")
        travels._until_finished(machine, "study")
        page.evaluate("() => window.FastMDXDashboard.navigate('studies')")
        page.click(".remote-job .ghost-btn >> text=Fetch the results")
        page.wait_for_selector(".remote-job-said .primary-btn")
        held = []
        page.route("**/api/remote/fetch", lambda route: held.append(route))
        page.click(".remote-job-said .primary-btn")
        while not held:
            page.wait_for_timeout(100)
        asking = page.is_disabled(".remote-job .ghost-btn >> text=Ask how it is doing")
        page.evaluate("() => window.FastMDXDashboard.navigate('run')")
        page.evaluate("() => window.FastMDXDashboard.navigate('studies')")
        page.wait_for_timeout(500)
        meanwhile = page.text_content(".remote-job-said")
        held[0].continue_()
        page.wait_for_function("() => [...document.querySelectorAll('.remote-job-said')]"
                               ".some(n => n.textContent.startsWith('Fetched into'))")
        fetched = page.text_content(".remote-job")
        browser.close()
    assert asking
    assert meanwhile.startswith("Fetching ")
    assert "(fetched)" in fetched
    assert errors == []


def test_a_config_file_is_sent_from_beside_it(session) -> None:
    from playwright.sync_api import sync_playwright

    served, machine = session
    with sync_playwright() as pw:
        browser, page, errors = _page(pw, served.url + "#run")
        page.wait_for_selector("#run-remote:not([hidden])")
        beside_run = page.evaluate(
            "() => document.getElementById('run-actions-card').contains("
            "document.getElementById('run-remote'))")
        _config_checked(page, machine.study)
        page.wait_for_function(
            "() => document.getElementById('run-config-field').contains("
            "document.getElementById('run-remote'))")
        label = page.text_content("#run-remote-label")
        browser.close()
    assert beside_run
    assert label == "Or send this config file to one of your machines"
    assert errors == []
