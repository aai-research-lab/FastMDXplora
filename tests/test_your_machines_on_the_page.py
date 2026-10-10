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


def _as_if_ready(page):
    """The builder's Run on this machine held enabled, as the builder says
    once the study could run here: held, since the builder still loading
    sets it again, and Plan the send follows it."""
    page.evaluate("""() => { const b = document.getElementById('run-start-button');
      b.disabled = false;
      Object.defineProperty(b, 'disabled', {configurable: true,
        get() { return false; }, set() {}}); }""")


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
        _as_if_ready(page)
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
    assert gone and said.startswith("The study changed after the plan was made.")
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


def test_a_study_changed_without_a_sound_sends_nothing(session) -> None:
    """Second review: a system removed, Reset, or a draft loaded changes the
    form without an event to hear, and Send sent the study as planned."""
    from playwright.sync_api import sync_playwright

    served, machine = session
    with sync_playwright() as pw:
        browser, page, errors = _page(pw, served.url + "#run")
        _config_checked(page, machine.study)
        page.wait_for_selector("#run-remote-plan:not([disabled])")
        page.click("#run-remote-plan")
        page.wait_for_selector(".remote-plan-title")
        page.evaluate("() => { document.getElementById('run-config-path').value = '/elsewhere.yml'; }")
        page.click("#run-remote-planned .primary-btn")
        said = page.text_content("#run-remote-note")
        gone = page.is_hidden("#run-remote-planned")
        browser.close()
    assert gone and said.startswith("The study changed after the plan was made.")
    assert not any(c.startswith("mkdir") for c in machine.commands)
    assert errors == []


def test_a_plan_of_a_study_changed_while_it_was_made_is_not_shown(session) -> None:
    from playwright.sync_api import sync_playwright

    served, machine = session
    with sync_playwright() as pw:
        browser, page, errors = _page(pw, served.url + "#run")
        _config_checked(page, machine.study)
        page.wait_for_selector("#run-remote-plan:not([disabled])")
        held = []
        page.route("**/api/remote/plan", lambda route: held.append(route))
        page.click("#run-remote-plan")
        while not held:
            page.wait_for_timeout(100)
        page.evaluate("() => { document.getElementById('run-config-path').value = '/elsewhere.yml'; }")
        held[0].continue_()
        page.wait_for_function("() => document.getElementById('run-remote-note')"
                               ".textContent.startsWith('The study changed')")
        shown = page.is_visible("#run-remote-planned")
        browser.close()
    assert not shown
    assert errors == []


def test_a_send_s_answer_is_kept_for_the_page_come_back_to(session) -> None:
    """Second review: a send answered while the person was on another page
    was said nowhere, and Plan the send could be pressed while it was out."""
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
        while not held:
            page.wait_for_timeout(100)
        planning = page.is_disabled("#run-remote-plan")
        page.evaluate("() => window.FastMDXDashboard.navigate('studies')")
        held[0].fulfill(status=200, content_type="application/json",
                        body=json.dumps({"ok": False, "error": "Refused there."}))
        page.wait_for_timeout(500)
        page.evaluate("() => window.FastMDXDashboard.navigate('run')")
        page.wait_for_timeout(800)
        said = page.text_content("#run-remote-note")
        again = page.is_enabled("#run-remote-plan")
        browser.close()
    assert planning
    assert said == "Refused there." and again
    assert errors == []


def test_a_plan_let_go_is_let_go_there_and_a_change_is_said_on_return(session) -> None:
    """Third review: a plan taken away while the person was on another page
    vanished unsaid, Not now left its config behind, and the results folder
    was named only as the page knew it."""
    from playwright.sync_api import sync_playwright

    served, machine = session
    with sync_playwright() as pw:
        browser, page, errors = _page(pw, served.url + "#run")
        forgotten = []
        page.on("request", lambda r: forgotten.append(r.post_data)
                if r.url.endswith("/api/remote/forget") else None)
        _config_checked(page, machine.study)
        page.wait_for_selector("#run-remote-plan:not([disabled])")
        page.click("#run-remote-plan")
        page.wait_for_selector(".remote-plan-title")
        results = page.text_content("#run-remote-planned .remote-plan-facts")
        page.click("#run-remote-planned .ghost-btn")
        not_now = page.text_content("#run-remote-note")
        page.click("#run-remote-plan")
        page.wait_for_selector(".remote-plan-title")
        page.evaluate("() => { document.getElementById('run-config-path').value = '/elsewhere.yml'; }")
        page.evaluate("() => window.FastMDXDashboard.navigate('studies')")
        page.wait_for_timeout(300)
        page.evaluate("() => window.FastMDXDashboard.navigate('run')")
        page.wait_for_function("() => document.getElementById('run-remote-note')"
                               ".textContent.startsWith('The study changed')")
        shown = page.is_visible("#run-remote-planned")
        page.wait_for_timeout(300)
        browser.close()
    assert str((machine.study.parent / "study").resolve()) in results
    assert not_now == "Not sent."
    assert len(forgotten) == 2 and all('"plan"' in body for body in forgotten)
    assert not shown
    assert not any(c.startswith("mkdir") for c in machine.commands)
    assert errors == []


_PLANNED = {"ok": True, "plan": "t1", "kept_s": 600, "config": "auto.yml", "machine": "box",
            "job": "auto", "runs_in": "/opt/fastmdx", "scheduler": "a detached process",
            "folder": "~/fmdx/auto", "results": "auto", "results_path": "/w/auto",
            "travels": [], "fetched_there": ["1UBQ"], "room": [], "notes": [],
            "script": "#!/bin/sh\n"}


def test_a_plan_let_go_empties_the_name_it_wrote_and_is_let_go_first(session) -> None:
    """First review of 1796-1797: Not now left the results name written for
    the plan in the form, and a plan asked at once could race its forget."""
    import json

    from playwright.sync_api import sync_playwright

    served, machine = session
    with sync_playwright() as pw:
        browser, page, errors = _page(pw, served.url + "#run")
        page.wait_for_selector("#run-remote:not([hidden])")
        order = []
        held = []

        def planned(route):
            order.append("plan")
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps(_PLANNED))

        def forgotten(route):
            order.append("forget")
            held.append(route)

        page.route("**/api/remote/plan", planned)
        page.route("**/api/remote/forget", forgotten)
        _as_if_ready(page)
        blank = page.input_value("#run-output")
        page.wait_for_selector("#run-remote-plan:not([disabled])")
        page.click("#run-remote-plan")
        page.wait_for_selector(".remote-plan-title")
        named = page.input_value("#run-output")
        page.click("#run-remote-planned .ghost-btn")
        emptied = page.input_value("#run-output")
        # The form changed, and the builder asks again whether it could run.
        _as_if_ready(page)
        page.wait_for_selector("#run-remote-plan:not([disabled])")
        page.click("#run-remote-plan")
        page.wait_for_timeout(500)
        before = list(order)
        held[0].fulfill(status=200, content_type="application/json", body='{"ok": true}')
        page.wait_for_selector(".remote-plan-title")
        browser.close()
    assert blank == "" and named == "auto" and emptied == ""
    assert before == ["plan", "forget"] and order == ["plan", "forget", "plan"]
    assert errors == []


def test_a_plan_refused_gives_the_focus_back_and_a_send_is_said_once(session) -> None:
    import json

    from playwright.sync_api import sync_playwright

    served, machine = session
    with sync_playwright() as pw:
        browser, page, errors = _page(pw, served.url + "#run")
        _config_checked(page, machine.study)
        page.wait_for_selector("#run-remote-plan:not([disabled])")
        refusing = [True]

        def planned(route):
            if refusing[0]:
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"ok": False, "error": "Not this time."}))
            else:
                route.continue_()

        page.route("**/api/remote/plan", planned)
        page.focus("#run-remote-plan")
        page.keyboard.press("Enter")
        page.wait_for_function("() => document.getElementById('run-remote-note')"
                               ".textContent === 'Not this time.'")
        focused = page.evaluate("() => document.activeElement.id")
        refusing[0] = False
        page.keyboard.press("Enter")
        page.wait_for_selector(".remote-plan-title")
        page.click("#run-remote-planned .primary-btn")
        page.wait_for_function(
            "() => document.getElementById('run-remote-note').textContent.startsWith('Sent')")
        page.wait_for_timeout(300)
        live = page.evaluate("""() => [...document.querySelectorAll('.sr-only[role=status]')]
          .map(n => n.textContent).filter(t => t.startsWith('Sent'))""")
        note = page.evaluate("""() => { const n = document.getElementById('run-remote-note');
          return [document.activeElement === n, n.getAttribute('role')]; }""")
        travels._until_finished(machine, "study")
        browser.close()
    assert focused == "run-remote-plan"
    assert live == [] and note == [True, "status"]
    assert errors == []


def test_a_form_filled_after_a_fetch_takes_the_plan_away_at_once(session) -> None:
    """Second review of 1796-1798: a starter or a draft fills the form after
    its fetch, later than the click that asked for it, and the plan stayed
    until Send was pressed."""
    from playwright.sync_api import sync_playwright

    served, machine = session
    with sync_playwright() as pw:
        browser, page, errors = _page(pw, served.url + "#run")
        _config_checked(page, machine.study)
        page.wait_for_selector("#run-remote-plan:not([disabled])")
        page.click("#run-remote-plan")
        page.wait_for_selector(".remote-plan-title")
        page.evaluate("""() => setTimeout(() => {
          document.getElementById('run-config-path').value = '/elsewhere.yml'; }, 200)""")
        page.wait_for_selector("#run-remote-planned", state="hidden", timeout=5000)
        said = page.text_content("#run-remote-note")
        browser.close()
    assert said.startswith("The study changed after the plan was made.")
    assert errors == []


def test_a_send_refused_empties_the_name_its_plan_wrote(session) -> None:
    import json

    from playwright.sync_api import sync_playwright

    served, machine = session
    with sync_playwright() as pw:
        browser, page, errors = _page(pw, served.url + "#run")
        page.wait_for_selector("#run-remote:not([hidden])")
        page.route("**/api/remote/plan", lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(_PLANNED)))
        page.route("**/api/remote/send", lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(
                {"ok": False, "error": "Refused there.", "sent_nothing": True})))
        _as_if_ready(page)
        page.wait_for_selector("#run-remote-plan:not([disabled])")
        page.click("#run-remote-plan")
        page.wait_for_selector(".remote-plan-title")
        named = page.input_value("#run-output")
        page.click("#run-remote-planned .primary-btn")
        page.wait_for_function("() => document.getElementById('run-remote-note')"
                               ".textContent === 'Refused there.'")
        emptied = page.input_value("#run-output")
        browser.close()
    assert named == "auto" and emptied == ""
    assert errors == []


def test_a_form_edited_while_a_send_is_out_takes_nothing_away(session) -> None:
    """Round 3 of 1796-1799: typing while a send was out said the study had
    changed and took the card away, though the send went on; the answer
    then took the focus from the field being typed in."""
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
        while not held:
            page.wait_for_timeout(100)
        page.click("#run-config-path")
        page.keyboard.press("End")
        page.keyboard.type("x")
        page.wait_for_timeout(1200)
        during = page.text_content("#run-remote-note")
        card = page.is_visible("#run-remote-planned")
        held[0].fulfill(status=200, content_type="application/json",
                        body=json.dumps({"ok": False, "error": "Refused there."}))
        page.wait_for_function("() => document.getElementById('run-remote-note')"
                               ".textContent === 'Refused there.'")
        typing = page.evaluate("() => document.activeElement.id")
        browser.close()
    assert during.startswith("Sending to box") and card
    assert typing == "run-config-path"
    assert errors == []


def test_a_quiet_change_gives_the_focus_back_and_the_label_is_left_alone(session) -> None:
    from playwright.sync_api import sync_playwright

    served, machine = session
    with sync_playwright() as pw:
        browser, page, errors = _page(pw, served.url + "#run")
        _config_checked(page, machine.study)
        page.wait_for_selector("#run-remote-plan:not([disabled])")
        page.evaluate("""() => { window.__rewritten = 0;
          new MutationObserver(() => { window.__rewritten += 1; }).observe(
            document.getElementById('run-remote-label'),
            {childList: true, characterData: true, subtree: true}); }""")
        page.wait_for_timeout(1500)
        rewritten = page.evaluate("() => window.__rewritten")
        page.click("#run-remote-plan")
        page.wait_for_selector(".remote-plan-title")
        page.focus("#run-remote-planned .primary-btn")
        page.evaluate("() => { document.getElementById('run-config-path').value = '/x.yml'; }")
        page.wait_for_selector("#run-remote-planned", state="hidden", timeout=5000)
        focused = page.evaluate("() => document.activeElement.id")
        browser.close()
    assert rewritten == 0
    assert focused == "run-remote-plan"
    assert errors == []


def test_a_plan_kept_past_its_time_is_let_go(session) -> None:
    import json

    from playwright.sync_api import sync_playwright

    served, machine = session
    with sync_playwright() as pw:
        browser, page, errors = _page(pw, served.url + "#run")
        page.wait_for_selector("#run-remote:not([hidden])")
        forgotten = []
        page.route("**/api/remote/plan", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({**_PLANNED, "kept_s": 1})))
        page.route("**/api/remote/forget", lambda route: (
            forgotten.append(route.request.post_data),
            route.fulfill(status=200, content_type="application/json", body='{"ok": true}')))
        _as_if_ready(page)
        page.wait_for_selector("#run-remote-plan:not([disabled])")
        page.click("#run-remote-plan")
        page.wait_for_selector(".remote-plan-title")
        named = page.input_value("#run-output")
        page.wait_for_function("() => document.querySelector('#run-remote-planned')"
                               ".textContent.includes('no longer kept')")
        page.wait_for_timeout(1000)
        emptied = page.input_value("#run-output")
        said = page.text_content("#run-remote-note")
        browser.close()
    assert named == "auto" and emptied == ""
    assert len(forgotten) == 1 and '"t1"' in forgotten[0]
    assert not said.startswith("The study changed")
    assert errors == []


def test_a_plan_no_longer_kept_says_so_once_and_an_edit_after_says_nothing(session) -> None:
    """Round 1 of the next round: past its ten minutes the note under the
    card still said the plan was below, and an edit then said the study had
    changed after a plan already let go."""
    import json

    from playwright.sync_api import sync_playwright

    served, machine = session
    with sync_playwright() as pw:
        browser, page, errors = _page(pw, served.url + "#run")
        page.wait_for_selector("#run-remote:not([hidden])")
        page.route("**/api/remote/plan", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({**_PLANNED, "kept_s": 1})))
        page.route("**/api/remote/forget", lambda route: route.fulfill(
            status=200, content_type="application/json", body='{"ok": true}'))
        _as_if_ready(page)
        page.wait_for_selector("#run-remote-plan:not([disabled])")
        page.click("#run-remote-plan")
        page.wait_for_selector(".remote-plan-title")
        page.wait_for_function("() => document.querySelector('#run-remote-planned')"
                               ".textContent.includes('no longer kept')")
        expired = page.text_content("#run-remote-note")
        closed = page.text_content("#run-remote-planned .ghost-btn")
        page.evaluate("""() => { const box = document.getElementById('run-output');
          box.value = 'edited';
          box.dispatchEvent(new Event('input', {bubbles: true})); }""")
        page.wait_for_timeout(700)
        after = page.text_content("#run-remote-note")
        browser.close()
    assert expired == after == "Not sent: the plan is no longer kept. Plan the send again."
    assert closed == "Close"
    assert errors == []


def test_a_quiet_change_leaving_the_study_not_ready_holds_plan_the_send(session) -> None:
    """Round 1 of the next round: the plan taken away by a quiet change gave
    the focus to Plan the send, left enabled for a study the builder said
    could not run."""
    from playwright.sync_api import sync_playwright

    served, machine = session
    with sync_playwright() as pw:
        browser, page, errors = _page(pw, served.url + "#run")
        _config_checked(page, machine.study)
        page.wait_for_selector("#run-remote-plan:not([disabled])")
        page.click("#run-remote-plan")
        page.wait_for_selector(".remote-plan-title")
        page.focus("#run-remote-planned .primary-btn")
        page.evaluate("() => { document.getElementById('run-config-path').value = ''; }")
        page.wait_for_selector("#run-remote-planned", state="hidden", timeout=5000)
        held = page.is_disabled("#run-remote-plan")
        focused = page.evaluate("() => document.activeElement.id")
        said = page.text_content("#run-remote-note")
        browser.close()
    assert held
    assert focused == "run-remote-note"
    assert said.startswith("The study changed")
    assert errors == []


def test_a_window_whose_studies_go_in_home_says_why_before_the_click(session) -> None:
    """Round 1 of the next round: Plan the send was offered for a study
    built in the form, and only once pressed said nothing could be sent."""
    from playwright.sync_api import sync_playwright

    served, machine = session
    why = ("Studies built here would be saved in /h, your home folder. For example "
           "`fastmdx gui --output /home/adekunle_aina_lab_workstation_account/"
           "molecular_dynamics/first`.")
    with sync_playwright() as pw:
        browser, page, errors = _page(pw, served.url + "#run", width=390, height=844)
        page.wait_for_selector("#run-remote:not([hidden])")

        def machines(route):
            answer = route.fetch()
            route.fulfill(response=answer, json={**answer.json(), "not_built_here": why})

        page.route("**/api/remote/machines", machines)
        page.evaluate("() => window.FastMDXRemote.refresh('run')")
        _as_if_ready(page)
        page.wait_for_selector("#run-remote-why:not([hidden])")
        built = (page.is_disabled("#run-remote-plan"), page.text_content("#run-remote-why"),
                 page.get_attribute("#run-remote-plan", "title") or "",
                 page.get_attribute("#run-remote-plan", "aria-describedby"),
                 page.text_content("#run-remote-why code"))
        wide = page.evaluate("() => document.documentElement.scrollWidth > innerWidth")
        _config_checked(page, machine.study)
        page.wait_for_selector("#run-remote-plan:not([disabled])")
        from_a_file = page.is_hidden("#run-remote-why")
        browser.close()
    assert built == (True, why.replace("`", ""), "", "run-remote-why",
                     "fastmdx gui --output /home/adekunle_aina_lab_workstation_account/"
                     "molecular_dynamics/first")
    assert not wide
    assert from_a_file
    assert errors == []


def test_the_results_name_written_in_the_form_is_the_bare_name(session) -> None:
    """Round 1 of the next round: the empty Results box was filled with the
    results folder's full path."""
    import json

    from playwright.sync_api import sync_playwright

    served, machine = session
    with sync_playwright() as pw:
        browser, page, errors = _page(pw, served.url + "#run")
        page.wait_for_selector("#run-remote:not([hidden])")
        page.route("**/api/remote/plan", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({**_PLANNED, "results": "/w/auto", "results_name": "auto"})))
        page.route("**/api/remote/forget", lambda route: route.fulfill(
            status=200, content_type="application/json", body='{"ok": true}'))
        _as_if_ready(page)
        page.wait_for_selector("#run-remote-plan:not([disabled])")
        page.click("#run-remote-plan")
        page.wait_for_selector(".remote-plan-title")
        named = page.input_value("#run-output")
        browser.close()
    assert named == "auto"
    assert errors == []
