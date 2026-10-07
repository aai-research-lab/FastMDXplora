"""A run or a stop is confirmed with buttons, and a run is said where it was asked.

Toured on `c2d547d` (10-07): "Run the study above? Say yes." was to be
answered by typing; "run it" said "Starting the run." under the message
while "Started" and the Running button went to the study's card two messages
up; restored, the same reply read "Did: run.". Now a confirmation is a card
with its two answers as buttons (typing still works), and a run is a line
under the message that started it, with a way to watch it and to stop it,
read again as when it started.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")

CONFIG = {"ok": True, "cycles": 1, "yaml": "systems:\n- system: 1UAO\n",
          "config": {"systems": [{"system": "1UAO"}], "simulation": {"duration_ns": 2}},
          "plan": [{"label": "System", "value": "1UAO", "default": False},
                   {"label": "Production", "value": "2 ns, 2 fs steps", "default": False}],
          "attempts": []}


@pytest.fixture()
def ui():
    from types import SimpleNamespace

    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    root = Path(tempfile.mkdtemp()) / "workspace"
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0,
                                      home_mode=True)
    replies: list[dict] = []
    launches: list[dict] = []
    stops: list[int] = []

    def propose(route):
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps(replies.pop(0)))

    def launch(route):
        launches.append(json.loads(route.request.post_data or "{}"))
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True}))

    def stop(route):
        stops.append(1)
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True}))

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.set_default_timeout(30000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/agent/propose*", propose)
            page.route("**/api/agent/run", launch)
            page.route("**/api/explore/stop", stop)
            page.goto(session.url + "#agent", wait_until="domcontentloaded")
            page.wait_for_selector("#agent-request", state="visible")
            yield SimpleNamespace(page=page, replies=replies, launches=launches, stops=stops,
                                  errors=errors, url=session.url)
            browser.close()
    finally:
        session.server.shutdown()
        session.server.server_close()


def _send(page, text):
    page.fill("#agent-request", text)
    page.keyboard.press("Enter")


def test_a_run_not_plainly_asked_for_is_confirmed_with_a_button(ui) -> None:
    page = ui.page
    ui.replies += [CONFIG, {"ok": False, "action": "run", "where": "", "confirm": True}]
    _send(page, "chignolin for 2 ns")
    page.wait_for_selector("#agent-thread .agent-study:not([hidden])")
    _send(page, "go on then")
    page.wait_for_selector("#agent-thread .agent-confirm")
    question = page.text_content("#agent-thread .agent-confirm-q")
    facts = page.text_content("#agent-thread .agent-confirm-facts")
    state = page.get_attribute("#agent-thread .agent-msg-agent >> nth=-1", "data-state")
    assert ui.launches == []
    page.click("#agent-thread .agent-confirm-btn[data-answer='yes']")
    page.wait_for_selector("#agent-thread .agent-running")
    answered = page.locator("#agent-thread .agent-msg-user").last.text_content()
    line = page.locator("#agent-thread .agent-msg-agent").last.inner_text()
    spent = page.eval_on_selector_all("#agent-thread .agent-confirm-btn", "b => b.every(x => x.disabled)")
    card_note = page.text_content("#agent-thread .agent-study [data-role=note]")
    assert question == "Run version 1 on this machine?"
    assert "1UAO" in facts and "2 ns, 2 fs steps" in facts
    assert state == "waiting"
    assert answered.startswith("yes") and len(ui.launches) == 1
    assert "Running version 1, started " in line and "Watch on the Overview" in line
    assert spent and not card_note.strip()
    assert ui.errors == []


def test_a_plain_run_is_said_under_its_message_and_kept(ui) -> None:
    page = ui.page
    ui.replies += [CONFIG, {"ok": False, "action": "run", "where": "", "confirm": False}]
    _send(page, "chignolin for 2 ns")
    page.wait_for_selector("#agent-thread .agent-study:not([hidden])")
    _send(page, "run it")
    page.wait_for_selector("#agent-thread .agent-running")
    last = page.locator("#agent-thread .agent-msg-agent").last.inner_text()
    in_card = page.locator("#agent-thread .agent-study .agent-running").count()
    run_label = page.text_content("#agent-thread [data-role=run]")
    assert "Running version 1" in last and in_card == 0 and run_label == "Running"
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("#agent-thread .agent-running")
    again = page.locator("#agent-thread .agent-msg-agent").last.inner_text()
    assert "Started version 1 at " in again and "Did: run" not in again
    assert "Stop" not in again.replace("Watch on the Overview", "")
    assert ui.errors == []


def test_stop_asks_first_and_keep_running_keeps_it(ui) -> None:
    page = ui.page
    ui.replies += [CONFIG, {"ok": False, "action": "run", "where": "", "confirm": False}]
    _send(page, "chignolin for 2 ns")
    page.wait_for_selector("#agent-thread .agent-study:not([hidden])")
    _send(page, "run it")
    page.wait_for_selector("#agent-thread .agent-running button:has-text('Stop')")
    page.click("#agent-thread .agent-running button:has-text('Stop')")
    page.wait_for_selector("#agent-thread .agent-confirm")
    asked = page.text_content("#agent-thread .agent-confirm-q")
    buttons = page.locator("#agent-thread .agent-confirm-btn").all_text_contents()
    page.click("#agent-thread .agent-confirm-btn[data-answer='no']")
    page.wait_for_selector("#agent-thread .agent-attempt:has-text('Not stopped.')")
    assert asked == "Stop the run?" and buttons == ["Stop it", "Keep running"]
    assert ui.stops == []
    # Asked again, and stopped this time, by typing.
    page.click("#agent-thread .agent-running button:has-text('Stop')")
    page.wait_for_selector("#agent-thread .agent-confirm >> nth=1")
    _send(page, "yes")
    page.wait_for_selector("#agent-thread .agent-attempt:has-text('Stopped the run.')")
    assert ui.stops == [1]
    assert ui.errors == []


def test_a_waiting_confirmation_is_kept_waiting(ui) -> None:
    import urllib.request

    page = ui.page
    entries = [{"role": "user", "text": "chignolin"},
               dict(CONFIG, role="agent", kind="config", version=1),
               {"role": "user", "text": "go on then"},
               {"role": "agent", "kind": "question", "confirm": "run",
                "text": "Run version 1 on this machine?", "facts": "1UAO"}]
    request = urllib.request.Request(
        ui.url + "/api/agent/conversation", data=json.dumps({"entries": entries}).encode(),
        headers={"Content-Type": "application/json", "Origin": ui.url}, method="POST")
    urllib.request.urlopen(request, timeout=10).read()
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("#agent-thread .agent-confirm")
    pressable = page.eval_on_selector_all("#agent-thread .agent-confirm-btn", "b => b.map(x => !x.disabled)")
    page.click("#agent-thread .agent-confirm-btn[data-answer='yes']")
    page.wait_for_selector("#agent-thread .agent-running")
    assert pressable == [True, True] and len(ui.launches) == 1
    assert ui.errors == []


def test_a_press_on_the_card_answers_the_wait(ui) -> None:
    """Found by the review (10-07): pressing the card's Run left a run
    waiting for its yes, so the next message went to it and read "Not
    run."; a run refused was kept as "Did: run"."""
    page = ui.page
    ui.replies += [CONFIG, {"ok": False, "action": "run", "where": "", "confirm": True},
                   {"ok": False, "answer": "About an hour.", "cites": []}]
    _send(page, "chignolin for 2 ns")
    page.wait_for_selector("#agent-thread .agent-study:not([hidden])")
    _send(page, "go on then")
    page.wait_for_selector("#agent-thread .agent-confirm")
    page.click("#agent-thread [data-role=run]")
    page.wait_for_selector("#agent-thread .agent-running")
    spent = page.eval_on_selector_all("#agent-thread .agent-confirm-btn", "b => b.every(x => x.disabled)")
    _send(page, "how long will it take?")
    page.wait_for_selector("#agent-thread .agent-answer:has-text('About an hour.')")
    thread = page.text_content("#agent-thread")
    assert spent and "Not run." not in thread and len(ui.launches) == 1
    assert ui.errors == []


def test_a_refused_run_is_kept_as_refused(tmp_path) -> None:
    from types import SimpleNamespace

    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(tmp_path / "workspace"), host="127.0.0.1",
                                      port=0, home_mode=True)
    replies = [CONFIG, {"ok": False, "action": "run", "where": "", "confirm": False}]
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.set_default_timeout(30000)
            page.route("**/api/agent/propose*", lambda route: route.fulfill(
                status=200, content_type="application/json", body=json.dumps(replies.pop(0))))
            page.route("**/api/agent/run", lambda route: route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps({"ok": False, "error": "The folder is in use."})))
            page.goto(session.url + "#agent", wait_until="domcontentloaded")
            ui = SimpleNamespace(page=page)
            _send(ui.page, "chignolin for 2 ns")
            page.wait_for_selector("#agent-thread .agent-study:not([hidden])")
            _send(ui.page, "run it")
            page.wait_for_selector("#agent-thread .agent-attempt:has-text('The folder is in use.')")
            page.wait_for_timeout(500)
            page.reload(wait_until="domcontentloaded")
            page.wait_for_selector("#agent-thread .agent-study:not([hidden])")
            page.wait_for_timeout(500)
            thread = page.text_content("#agent-thread")
            browser.close()
    finally:
        session.server.shutdown()
    assert "The folder is in use." in thread and "Did: run" not in thread
    assert "Started version" not in thread


def test_a_run_ended_can_run_again_and_one_going_is_not_run_twice(ui) -> None:
    """Found by the review (10-07): after a run ended its version still said
    "Running" and "run it" answered that it was running; after a reload while
    one ran, its Run started it again. The server's word decides."""
    page = ui.page
    said = {"status": "running", "process_running": True}

    def state(route):
        body = route.fetch().json()
        body.update(said)
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    page.route("**/api/app-state", state)
    ui.replies += [CONFIG, {"ok": False, "action": "run", "where": "", "confirm": False},
                   {"ok": False, "action": "run", "where": "", "confirm": False}]
    _send(page, "chignolin for 2 ns")
    page.wait_for_selector("#agent-thread .agent-study:not([hidden])")
    said.update(status="idle", process_running=False)
    page.wait_for_timeout(500)
    _send(page, "run it")
    page.wait_for_selector("#agent-thread .agent-running .agent-running-stop")
    # Asked again at once rather than at the page's next poll.
    poll = "() => document.getElementById('refresh-now').click()"
    said.update(status="running", process_running=True)
    page.evaluate(poll)
    page.wait_for_timeout(1500)
    said.update(status="completed", process_running=False)
    page.evaluate(poll)
    page.wait_for_function(
        "() => document.querySelector('#agent-thread [data-role=run]').textContent === 'Run again'",
        timeout=15000)
    stops = page.locator("#agent-thread .agent-running-stop").count()
    _send(page, "run it")
    page.wait_for_function("() => document.querySelectorAll('#agent-thread .agent-running').length === 2")
    assert stops == 0 and len(ui.launches) == 2

    # Read again while a run goes on: not started twice.
    said.update(status="running", process_running=True)
    ui.replies += [{"ok": False, "action": "run", "where": "", "confirm": False}]
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("#agent-thread .agent-study:not([hidden])")
    page.wait_for_timeout(1500)
    _send(page, "run it")
    page.wait_for_selector("#agent-thread .agent-attempt:has-text('A study is already running here.')")
    assert len(ui.launches) == 2
    assert ui.errors == []
