"""A question's candidates are buttons, and the next message is not joined to it.

Toured on `c2d547d` (10-07): "Which lysozyme?" came with its candidates as
one line of text ("Candidates: 2VB1 hen egg-white, X-ray 0.65 A, 2007;
..."), and whatever was typed next was joined to the question before it was
sent: "Has it run long enough?" after the lysozyme question reached the AI
model as "which lysozyme should I simulate?\\nHas it run long enough?".
"""

from __future__ import annotations

import json
import re
import tempfile
import urllib.request

import pytest

pytest.importorskip("playwright.sync_api")

CHOICES = ["2VB1 hen egg-white, X-ray 0.65 Å, 2007", "2LZM T4 phage, X-ray 1.7 Å, 1987",
           "the one in my folder"]


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", tempfile.mkdtemp())
    import fastmdxplora.agent as agent_mod
    from fastmdxplora.agent.turns import ToolCall, Turn, Usage
    from fastmdxplora.gui.server import start_dashboard_session

    asked: list[str] = []
    given: list[list[str]] = []

    def complete(prompt: str) -> str:
        raise AssertionError("asked in text")

    def turn(system, messages, tools, **_streamed):
        last = [m for m in messages if m["role"] == "user"][-1]["text"]
        asked.append(last)
        given.append([str(m.get("text") or "") for m in messages])
        if len(asked) == 1:
            return Turn("", (ToolCall("c1", "ask_person", {
                "question": "Which lysozyme? The PDB holds several.", "choices": CHOICES}),),
                Usage(calls=1))
        return Turn("Noted.", (), Usage(calls=1))

    complete.turn = turn
    monkeypatch.setattr(agent_mod, "completion_for", lambda *a, **k: complete)
    started = start_dashboard_session(output=str(tmp_path / "study"), host="127.0.0.1", port=0)
    started.asked = asked
    started.given = given
    yield started
    started.server.shutdown()


def _page(pw, url):
    browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.set_default_timeout(60000)
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(url + "#agent", wait_until="domcontentloaded")
    return browser, page, errors


def _wanted(text: str) -> str:
    return re.search(r"## The study wanted\n(.*)\Z", text, re.S).group(1).strip()


def test_a_candidate_pressed_answers_with_its_identifier(session) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page, errors = _page(pw, session.url)
        page.fill("#agent-request", "Simulate lysozyme")
        page.keyboard.press("Enter")
        page.wait_for_selector("#agent-thread .agent-choice")
        question = page.text_content("#agent-thread .agent-question")
        names = page.locator("#agent-thread .agent-choice-name").all_text_contents()
        facts = page.locator("#agent-thread .agent-choice-facts").all_text_contents()
        page.click("#agent-thread .agent-choice:has-text('2LZM')")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('Noted.')")
        spent = page.eval_on_selector_all("#agent-thread .agent-choice", "b => b.map(x => x.disabled)")
        said = page.locator("#agent-thread .agent-msg-user").last.text_content()
        browser.close()
    assert question.strip() == "Which lysozyme? The PDB holds several."
    assert "Candidates:" not in question
    assert names == ["2VB1", "2LZM", "the one in my folder"]
    assert facts == ["hen egg-white, X-ray 0.65 Å, 2007", "T4 phage, X-ray 1.7 Å, 1987"]
    assert said.startswith("2LZM")
    assert _wanted(session.asked[-1]) == "2LZM"
    assert spent == [True, True, True]
    assert errors == []


def test_a_message_after_a_question_is_sent_as_it_is(session) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page, errors = _page(pw, session.url)
        page.fill("#agent-request", "Simulate lysozyme")
        page.keyboard.press("Enter")
        page.wait_for_selector("#agent-thread .agent-choice")
        page.fill("#agent-request", "Has it run long enough?")
        page.keyboard.press("Enter")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('Noted.')")
        spent = page.eval_on_selector_all("#agent-thread .agent-choice", "b => b.every(x => x.disabled)")
        browser.close()
    assert _wanted(session.asked[-1]) == "Has it run long enough?"
    # The question is in the conversation the Agent is given.
    assert any("Which lysozyme?" in text for text in session.given[-1][:-1])
    assert spent
    assert errors == []


def test_a_kept_question_shows_its_candidates_again(tmp_path, monkeypatch) -> None:
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", tempfile.mkdtemp())
    session = start_dashboard_session(output=str(tmp_path / "study"), host="127.0.0.1", port=0)
    entries = [{"role": "user", "text": "Simulate lysozyme"},
               {"role": "agent", "kind": "question",
                "text": "Which lysozyme?\n\nCandidates: " + "; ".join(CHOICES) + ".",
                "asked": "Which lysozyme?", "choices": CHOICES}]
    request = urllib.request.Request(
        session.url + "/api/agent/conversation", data=json.dumps({"entries": entries}).encode(),
        headers={"Content-Type": "application/json", "Origin": session.url}, method="POST")
    urllib.request.urlopen(request, timeout=10).read()
    try:
        with sync_playwright() as pw:
            browser, page, errors = _page(pw, session.url)
            page.wait_for_selector("#agent-thread .agent-choice")
            pressable = page.eval_on_selector_all("#agent-thread .agent-choice",
                                                  "b => b.map(x => !x.disabled)")
            state = page.get_attribute("#agent-thread .agent-msg-agent", "data-state")
            browser.close()
    finally:
        session.server.shutdown()
    assert pressable == [True, True, True] and state == "waiting"
    assert errors == []


def test_a_version_used_again_spends_the_candidates(tmp_path, monkeypatch) -> None:
    """Found by the review (10-07): Use this version left a question's
    candidates pressable under it."""
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.plan import plan_of
    from fastmdxplora.gui.server import start_dashboard_session

    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", tempfile.mkdtemp())
    session = start_dashboard_session(output=str(tmp_path / "study"), host="127.0.0.1", port=0)
    config = {"systems": [{"system": "1UBQ"}]}
    version = {"role": "agent", "kind": "config", "yaml": "x: 1\n", "config": config,
               "cycles": 1, "attempts": [], "plan": plan_of(config)}
    entries = [{"role": "user", "text": "ubiquitin"}, version,
               {"role": "user", "text": "and at 330 K"}, dict(version),
               {"role": "user", "text": "which lysozyme?"},
               {"role": "agent", "kind": "question", "text": "Which?", "asked": "Which?",
                "choices": CHOICES}]
    request = urllib.request.Request(
        session.url + "/api/agent/conversation", data=json.dumps({"entries": entries}).encode(),
        headers={"Content-Type": "application/json", "Origin": session.url}, method="POST")
    urllib.request.urlopen(request, timeout=10).read()
    try:
        with sync_playwright() as pw:
            browser, page, errors = _page(pw, session.url)
            page.wait_for_selector("#agent-thread .agent-choice")
            page.click("#agent-thread .agent-study.is-replaced [data-role=use]")
            page.wait_for_selector("#agent-thread .agent-study:not([hidden]) >> nth=2")
            spent = page.eval_on_selector_all("#agent-thread .agent-choice",
                                              "b => b.every(x => x.disabled)")
            browser.close()
    finally:
        session.server.shutdown()
    assert spent
    assert errors == []
