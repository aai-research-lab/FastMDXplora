"""The Agent page on a phone, its settings, and Useful or Wrong on each reply.

Toured on `c2d547d` (10-07): at 390 px the sidebar's lists of conversations
filled the bar across the top, above the study, and the bar said the
product's name in a serif; the Agent's settings carried eight em dashes,
an API key field the browser styled and choices in monospace. User, 10-07:
"FX logo on the phone should just be the line icon for FastMDXplora (white
on black ground)". And each reply is marked Useful or Wrong, kept with the
conversation for the Agent's evaluation (stage 3, decided 10-07).
"""

from __future__ import annotations

import json
import re
import tempfile
import urllib.request
from pathlib import Path

import pytest

GUI = Path(__file__).resolve().parents[1] / "src" / "fastmdxplora" / "gui"


def test_the_agent_s_settings_say_nothing_with_a_dash() -> None:
    page = (GUI / "templates" / "dashboard.html").read_text(encoding="utf-8")
    dialog = page[page.index('<div id="agent-settings"'):page.index('<div id="palette"')]
    dialog = re.sub(r"<!--.*?-->", "", dialog, flags=re.S)
    assert "—" not in dialog and "–" not in dialog
    script = (GUI / "static" / "agent-panel.js").read_text(encoding="utf-8")
    code = re.sub(r"/\*.*?\*/", "", script, flags=re.S)
    assert "\\u2014" not in code and "—" not in code
    css = (GUI / "static" / "dashboard.css").read_text(encoding="utf-8")
    assert '#agent-settings .builder-field input[type="password"]' in css


def test_the_page_carries_the_mark_for_a_phone(tmp_path) -> None:
    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(tmp_path / "study"), host="127.0.0.1", port=0)
    try:
        page = urllib.request.urlopen(session.url + "/", timeout=10).read().decode()
    finally:
        session.server.shutdown()
    assert '<img class="brand-mark-tile" src="/static/fastmdx-mark.svg" alt="">' in page
    assert 'id="phone-convs"' in page and "<!--__FASTMDX_PHONE_MARK__-->" not in page


pytest.importorskip("playwright.sync_api")


def _page(pw, url, width, hash_="#agent"):
    browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
    page = browser.new_page(viewport={"width": width, "height": 844})
    page.set_default_timeout(30000)
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(url + hash_, wait_until="domcontentloaded")
    page.wait_for_selector("#agent-request", state="visible")
    return browser, page, errors


def _kept(url, entries) -> None:
    request = urllib.request.Request(
        url + "/api/agent/conversation", data=json.dumps({"entries": entries}).encode(),
        headers={"Content-Type": "application/json", "Origin": url}, method="POST")
    urllib.request.urlopen(request, timeout=10).read()


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", tempfile.mkdtemp())
    from fastmdxplora.gui.server import start_dashboard_session

    study = tmp_path / "workspace" / "study"
    (study / "analysis").mkdir(parents=True)
    started = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    _kept(started.url, [{"role": "user", "text": "earlier"},
                        {"role": "agent", "kind": "answer", "text": "An earlier answer.",
                         "cites": []}])
    yield started
    started.server.shutdown()


def test_on_a_phone_the_bar_holds_the_mark_and_the_conversations_behind_an_icon(session) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page, errors = _page(pw, session.url, 390)
        mark = page.is_visible(".sidebar .brand-mark-tile")
        name = page.is_visible(".sidebar .brand-product")
        lists = page.is_visible("#sidebar-study-convs") or page.is_visible("#sidebar-chats")
        bar = page.eval_on_selector(".sidebar", "s => s.getBoundingClientRect().height")
        page.click("#phone-convs")
        page.wait_for_selector("#agent-conv-list:not([hidden])")
        listed = page.text_content("#agent-conv-list")
        browser.close()
    assert mark and not name and not lists
    assert bar < 140
    assert "earlier" in listed
    assert errors == []


def test_at_a_desk_the_sidebar_is_as_it_was(session) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page, errors = _page(pw, session.url, 1400)
        assert not page.is_visible(".brand-mark-tile") and not page.is_visible("#phone-convs")
        assert page.is_visible(".sidebar .brand-product")
        browser.close()
    assert errors == []


def test_a_reply_is_marked_useful_or_wrong_and_kept(session, monkeypatch) -> None:
    from playwright.sync_api import sync_playwright

    replies = [{"ok": False, "answer": "It has 76 residues.", "cites": [], "attempts": [],
                "usage": {"calls": 1, "input_tokens": 93, "cache_read_tokens": 17806,
                          "cache_write_tokens": 0, "output_tokens": 498}}]
    with sync_playwright() as pw:
        browser, page, errors = _page(pw, session.url, 1400)
        page.route("**/api/agent/propose*", lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(replies.pop(0))))
        page.fill("#agent-request", "How many residues?")
        page.keyboard.press("Enter")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('76 residues')")
        meta = page.locator("#agent-thread .agent-meta").last
        used = meta.locator(".agent-usage").text_content()
        meta.locator("[data-feedback=wrong]").click()
        page.wait_for_timeout(600)
        kept = json.loads(urllib.request.urlopen(session.url + "/api/agent/conversation",
                                                 timeout=10).read())["entries"]
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#agent-thread .agent-meta >> nth=1")
        pressed = page.locator("#agent-thread .agent-meta").last.locator(
            "[data-feedback=wrong]").get_attribute("aria-pressed")
        browser.close()
    assert used == "17,899 tokens in (17,806 from the cache) · 498 out"
    assert kept[-1]["feedback"] == "wrong" and kept[-1]["text"] == "It has 76 residues."
    assert pressed == "true"
    assert errors == []
