"""When a run the Agent started ends, what it found, from its records; and the
message box's words follow the situation.

Decided 10-07: "A summary when a run ends", a suggestion only, written from
the study's records so it costs no tokens (`records_answer` already answers
what a study found, whether it ran long enough and what would strengthen
it). And (user, 10-07): "Describe a study, ask a question, or tell me what to
do." should be adaptive, depending on the situation.
"""

from __future__ import annotations

import json
import tempfile
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from fastmdxplora.gui import records_answer
from fastmdxplora.gui.agent_panel import run_summary_endpoint
from tests.test_the_agent_answers_from_the_records import SUPPORTED, _study


def _ended(root: Path, status: str = "completed") -> Path:
    _study(root)
    (root / "manifest.json").write_text(json.dumps({"phases": [
        {"name": "simulation", "started_at": "2026-10-07T10:00:00+00:00",
         "finished_at": "2026-10-07T12:07:00+00:00"}]}), encoding="utf-8")
    (root / "simulation").mkdir(exist_ok=True)
    (root / "simulation" / "live_status.json").write_text(json.dumps({"status": status}),
                                                          encoding="utf-8")
    return root


def test_an_ended_run_is_said_from_its_records(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(records_answer, "_supports", lambda base: SUPPORTED)
    root = _ended(tmp_path / "study")
    said = run_summary_endpoint({"study": str(root)}, SimpleNamespace())
    assert said["ok"] and said["ended"] and said["status"] == "completed"
    assert said["head"].startswith("The run ended: completed")
    assert "0.1123 ± 0.0021 nm" in said["found"]
    assert not said["found"].startswith("*From")
    assert said["long_enough"] and said["strengthen"]
    assert "AI model" not in json.dumps(said)


def test_a_failed_run_says_so(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(records_answer, "_supports", lambda base: SUPPORTED)
    root = _ended(tmp_path / "study", "failed")
    said = run_summary_endpoint({"study": str(root)}, SimpleNamespace())
    assert said["head"].startswith("The run failed")


def test_a_run_still_going_is_not_summarised(tmp_path) -> None:
    root = _ended(tmp_path / "study")
    going = SimpleNamespace(running_root=root, process=SimpleNamespace(poll=lambda: None))
    assert run_summary_endpoint({"study": str(root)}, going) == {"ok": True, "ended": False}


def test_a_run_its_record_says_is_going_is_not_summarised(tmp_path) -> None:
    """Found by the review (10-07): a run this server does not hold (after a
    restart, or on another machine) was summarised while its record said it
    was running."""
    root = _ended(tmp_path / "study", "running")
    assert run_summary_endpoint({"study": str(root)}, SimpleNamespace()) == {
        "ok": True, "ended": False}


def test_a_study_of_several_runs_is_said_once(tmp_path, monkeypatch) -> None:
    """Found by the review (10-07): its one paragraph was said under all
    three heads."""
    monkeypatch.setattr(records_answer, "_several_runs", lambda base: "Three runs.")
    root = _ended(tmp_path / "study")
    (root / "batch_manifest.json").write_text("{}", encoding="utf-8")
    said = run_summary_endpoint({"study": str(root)}, SimpleNamespace())
    assert said["found"] == "Three runs." and said["long_enough"] == said["strengthen"] == ""


def test_no_study_no_summary(tmp_path) -> None:
    assert not run_summary_endpoint({"study": str(tmp_path / "nothing")}, None)["ok"]
    assert not run_summary_endpoint({}, None)["ok"]

    def outside(path):
        raise ValueError("outside the workspace")

    root = _ended(tmp_path / "study")
    assert not run_summary_endpoint({"study": str(root)}, None, path_for=outside)["ok"]


# ---- In a browser ----------------------------------------------------------

pytest.importorskip("playwright.sync_api")


def _page(pw, url):
    browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.set_default_timeout(30000)
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(url + "#agent", wait_until="domcontentloaded")
    return browser, page, errors


def _kept(url, entries) -> None:
    request = urllib.request.Request(
        url + "/api/agent/conversation", data=json.dumps({"entries": entries}).encode(),
        headers={"Content-Type": "application/json", "Origin": url}, method="POST")
    urllib.request.urlopen(request, timeout=10).read()


def test_a_run_that_ended_while_the_page_was_closed_is_summarised_once(tmp_path, monkeypatch):
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", tempfile.mkdtemp())
    monkeypatch.setattr(records_answer, "_supports", lambda base: SUPPORTED)
    ran = _ended(tmp_path / "workspace" / "ran")
    session = start_dashboard_session(output=str(tmp_path / "workspace" / "other"),
                                      host="127.0.0.1", port=0)
    _kept(session.url, [{"role": "user", "text": "run it"},
                        {"role": "agent", "kind": "action", "action": "run", "where": "",
                         "version": 1, "started": "2026-10-07T10:00:00Z", "output": str(ran)}])
    try:
        with sync_playwright() as pw:
            browser, page, errors = _page(pw, session.url)
            page.wait_for_selector("#agent-thread .agent-summary")
            head = page.text_content("#agent-thread .agent-summary-head")
            rows = page.locator("#agent-thread .agent-summary-rows dt").all_text_contents()
            found = page.text_content("#agent-thread .agent-summary-rows dd")
            page.reload(wait_until="domcontentloaded")
            page.wait_for_selector("#agent-thread .agent-summary")
            time.sleep(1.5)
            count = page.locator("#agent-thread .agent-summary").count()
            browser.close()
    finally:
        session.server.shutdown()
    assert head.startswith("The run ended: completed in 2h 7m")
    assert "from the study’s records" in head and "AI model" not in head
    assert rows == ["What it found", "Long enough?", "To strengthen it"]
    assert "0.1123 ± 0.0021 nm" in found
    assert count == 1
    assert errors == []


def test_the_box_s_words_follow_the_situation(tmp_path, monkeypatch):
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", tempfile.mkdtemp())
    session = start_dashboard_session(output=str(tmp_path / "workspace" / "new"),
                                      host="127.0.0.1", port=0)
    replies = [
        {"ok": True, "cycles": 1, "yaml": "x: 1\n",
         "config": {"systems": [{"system": "1UAO"}]}, "plan": [], "attempts": []},
        {"ok": False, "question": "Which one?\n\nCandidates: 2VB1; 2LZM.",
         "asked": "Which one?", "choices": ["2VB1", "2LZM"]},
        {"ok": False, "action": "run", "where": "", "confirm": True},
    ]
    try:
        with sync_playwright() as pw:
            browser, page, errors = _page(pw, session.url)
            page.route("**/api/agent/propose*", lambda route: route.fulfill(
                status=200, content_type="application/json", body=json.dumps(replies.pop(0))))
            page.wait_for_selector("#agent-request", state="visible")
            seen = [page.get_attribute("#agent-request", "placeholder")]
            for typed, waited in (("chignolin", ".agent-study:not([hidden])"),
                                  ("which lysozyme?", ".agent-choice"),
                                  ("run it", ".agent-confirm")):
                page.fill("#agent-request", typed)
                page.keyboard.press("Enter")
                page.wait_for_selector("#agent-thread " + waited)
                page.wait_for_timeout(200)
                seen.append(page.get_attribute("#agent-request", "placeholder"))
            browser.close()
    finally:
        session.server.shutdown()
    assert seen == ["Describe a study, or ask about one in this folder",
                    "Change something, or say run it",
                    "Pick one above, or type your answer",
                    "Answer above, or type yes or no"]
    assert errors == []


def test_a_run_that_ended_before_it_was_seen_running_is_summarised(tmp_path, monkeypatch):
    """Found by the review (10-07): the summary was asked only on a change
    seen from running, so a run that ended between two polls had none. And
    a study whose record says nothing of how it ended is not said as one
    that could not finish. And by the second review (10-07): such a run
    stayed "Running" with Stop, and the next "run it" was told a study was
    running."""
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", tempfile.mkdtemp())
    monkeypatch.setattr(records_answer, "_supports", lambda base: SUPPORTED)
    ran = _ended(tmp_path / "workspace" / "ran")
    (ran / "simulation" / "live_status.json").unlink()
    session = start_dashboard_session(output=str(tmp_path / "workspace" / "here"),
                                      host="127.0.0.1", port=0)
    replies = [{"ok": True, "cycles": 1, "yaml": "x: 1\n",
                "config": {"systems": [{"system": "1UAO"}]}, "plan": [], "attempts": []},
               {"ok": False, "action": "run", "where": "", "confirm": False}]
    try:
        with sync_playwright() as pw:
            browser, page, errors = _page(pw, session.url)
            page.route("**/api/agent/propose*", lambda route: route.fulfill(
                status=200, content_type="application/json", body=json.dumps(replies.pop(0))))
            page.route("**/api/agent/run", lambda route: route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps({"ok": True, "output": str(ran)})))
            page.route("**/api/agent/conversation/attach", lambda route: route.fulfill(
                status=200, content_type="application/json", body=json.dumps({"ok": False})))
            page.wait_for_selector("#agent-request", state="visible")
            for typed, waited in (("chignolin", ".agent-study:not([hidden])"),
                                  ("run it", ".agent-running")):
                page.fill("#agent-request", typed)
                page.keyboard.press("Enter")
                page.wait_for_selector("#agent-thread " + waited)
            page.evaluate("() => document.getElementById('refresh-now').click()")
            page.wait_for_selector("#agent-thread .agent-summary")
            state = page.get_attribute("#agent-thread .agent-msg-agent >> nth=-1", "data-state")
            head = page.text_content("#agent-thread .agent-summary-head")
            line = page.text_content("#agent-thread .agent-running-said")
            stop = page.locator("#agent-thread .agent-running-stop").count()
            button = page.eval_on_selector(
                "#agent-thread .agent-study:not([hidden]) [data-role=run]",
                "b => [b.textContent, b.disabled]")
            browser.close()
    finally:
        session.server.shutdown()
    assert head.startswith("The run ended after 2h 7m")
    assert state == "done"
    assert line.startswith("Ran version 1") and stop == 0
    assert button == ["Run again", False]
    assert errors == []
