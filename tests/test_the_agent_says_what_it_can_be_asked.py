"""The Agent's page says what it can be asked before anything has been.

It opened on an empty thread reading "Nothing yet. Say what you want to
run.", with no word of what the Agent can do, whether a model was set, or
that it could be asked about the study already open. It now opens on
questions about the open study and studies to start, a note of what the
chosen mode does, and, where no model is set, the way to set one. A
suggestion is sent as it is pressed: written into the box to be changed
first, a question asked by a press read as not asked.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_the_drawing_scripts_run_in_a_browser import _write_study  # noqa: E402


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        launched = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        yield launched
        launched.close()


@pytest.fixture
def no_model(tmp_path, monkeypatch):
    from fastmdxplora.agent import models

    monkeypatch.setattr(models, "model_path", lambda: tmp_path / "model.json")
    return tmp_path / "model.json"


def _agent(browser, where):
    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(where), host="127.0.0.1", port=0)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.goto(session.url + "#agent", wait_until="domcontentloaded")
    page.wait_for_function("() => !document.getElementById('agent-start').hidden",
                           timeout=30000)
    page.wait_for_timeout(500)
    return session, page


def test_a_study_open_is_a_study_to_ask_about(browser, tmp_path, no_model) -> None:
    session, page = _agent(browser, _write_study(tmp_path / "study"))
    try:
        assert page.locator("#agent-start-study").is_visible()
        starters = page.locator(".agent-starter:visible")
        assert starters.count() == 6
    finally:
        page.close()
        session.server.shutdown()


def test_with_no_study_only_studies_to_start(browser, tmp_path, no_model) -> None:
    (tmp_path / "empty").mkdir()
    session, page = _agent(browser, tmp_path / "empty")
    try:
        assert page.locator("#agent-start-study").is_hidden()
        assert page.locator(".agent-starter:visible").count() == 3
    finally:
        page.close()
        session.server.shutdown()


def test_a_suggestion_is_sent_as_it_is_pressed(browser, tmp_path, no_model) -> None:
    session, page = _agent(browser, _write_study(tmp_path / "study"))
    try:
        starter = page.locator(".agent-starter", has_text="Ubiquitin in water")
        prompt = starter.get_attribute("data-prompt")
        starter.click()
        page.wait_for_selector("#agent-thread .agent-msg-user")
        said = page.locator("#agent-thread .agent-msg-user").first.inner_text()
        assert prompt in said and "1UBQ" in said
        assert page.locator("#agent-request").input_value() == ""
    finally:
        page.close()
        session.server.shutdown()


def test_without_a_model_it_says_how_to_set_one(browser, tmp_path, no_model) -> None:
    session, page = _agent(browser, _write_study(tmp_path / "study"))
    try:
        assert page.locator("#agent-start-engine").is_visible()
        page.locator("#agent-start-settings").click()
        assert page.locator("#agent-settings").is_visible()
    finally:
        page.close()
        session.server.shutdown()


def test_with_a_model_that_line_is_gone(browser, tmp_path, no_model) -> None:
    no_model.write_text(json.dumps({"provider": "openai", "model": "m"}), encoding="utf-8")
    session, page = _agent(browser, _write_study(tmp_path / "study"))
    try:
        page.wait_for_function("() => document.getElementById('agent-start-engine').hidden",
                               timeout=10000)
        note = page.locator("#agent-start-note").text_content()
        assert note.endswith("Nothing runs until you say so.")
    finally:
        page.close()
        session.server.shutdown()


def test_a_conversation_hides_it(browser, tmp_path, no_model) -> None:
    session, page = _agent(browser, _write_study(tmp_path / "study"))
    try:
        page.evaluate("() => { const d = document.createElement('div');"
                      " d.className = 'agent-msg'; document.getElementById('agent-thread')"
                      ".appendChild(d); }")
        page.wait_for_function("() => document.getElementById('agent-start').hidden")
    finally:
        page.close()
        session.server.shutdown()


def test_no_dash_in_what_it_says() -> None:
    from pathlib import Path

    import fastmdxplora.gui as gui

    root = Path(gui.__file__).parent
    page = (root / "templates" / "dashboard.html").read_text(encoding="utf-8")
    block = page[page.index('id="agent-start"'):page.index('id="agent-thread"')]
    script = (root / "static" / "agent-panel.js").read_text(encoding="utf-8")
    notes = script[script.index("var MODE_NOTES"):script.index("};", script.index("var MODE_NOTES"))]
    for text in (block, notes):
        assert "—" not in text and "\\u2014" not in text and "&mdash;" not in text
