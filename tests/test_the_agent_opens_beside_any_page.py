"""The Agent opens beside whatever page is open.

It was a page of its own, so asking it about the RMSD on the Analysis page
took the Analysis page away. ⌘J (Ctrl+J), or the button beside Agent in the
sidebar, opens its conversation in the side panel's place; the page stays.
It is the Agent page's own conversation, moved there and back.
"""

from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_the_drawing_scripts_run_in_a_browser import _write_study  # noqa: E402


@pytest.fixture(scope="module")
def session(tmp_path_factory):
    from fastmdxplora.gui.server import start_dashboard_session

    study = _write_study(tmp_path_factory.mktemp("beside") / "study")
    started = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    yield started
    started.server.shutdown()


@pytest.fixture
def no_model(tmp_path, monkeypatch):
    from fastmdxplora.agent import models

    monkeypatch.setattr(models, "model_path", lambda: tmp_path / "model.json")


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        launched = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        yield launched
        launched.close()


def _open(browser, session, where):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.add_init_script("try { localStorage.setItem('fmx.panelCollapsed', '0'); } catch (e) {}")
    tab = context.new_page()
    tab.set_default_timeout(60000)
    tab.goto(session.url + where, wait_until="domcontentloaded")
    tab.wait_for_function("() => window.FastMDXAgentBeside && "
                          "document.body.classList.contains('state-ready')")
    return tab


def _where(tab):
    return tab.evaluate("""() => {
        const drawer = document.getElementById('agent-drawer');
        const main = document.querySelector('.main').getBoundingClientRect();
        return {page: document.documentElement.dataset.page, beside: !drawer.hidden,
                thread: drawer.contains(document.getElementById('agent-thread')),
                log: getComputedStyle(document.getElementById('side-panel')).visibility,
                overlap: main.right > drawer.getBoundingClientRect().left + 1 && !drawer.hidden};
    }""")


def test_the_keys_open_it_beside_the_page_and_close_it(browser, session) -> None:
    tab = _open(browser, session, "#analysis")
    tab.keyboard.press("Control+j")
    opened = _where(tab)
    focused = tab.evaluate("() => document.activeElement.id")
    tab.keyboard.press("Control+j")
    closed = _where(tab)
    tab.context.close()
    assert opened == {"page": "analysis", "beside": True, "thread": True,
                      "log": "hidden", "overlap": False}
    assert focused == "agent-request"
    assert closed["beside"] is False and closed["thread"] is False and closed["log"] == "visible"


def test_one_conversation_on_the_page_and_beside_it(browser, session, no_model) -> None:
    tab = _open(browser, session, "#report")
    tab.click("#agent-beside-open")
    tab.fill("#agent-request", "Summarise what this study found.")
    tab.click("#agent-propose")
    tab.wait_for_selector("#agent-drawer .agent-msg-user")
    # With no AI model set, the Agent's settings open to choose one, as the
    # reply comes (its note and the dialog in one task): looked for before
    # it, the dialog opened after the look and covered the page (CI).
    tab.wait_for_selector("#agent-drawer :is(.agent-attempt, .agent-answer)")
    if tab.evaluate("() => !document.getElementById('agent-settings').hidden"):
        tab.click("#agent-settings-close")
    tab.click("#agent-drawer-page")
    tab.wait_for_function("() => document.documentElement.dataset.page === 'agent'")
    on_the_page = tab.evaluate("""() => [
        document.querySelector('.page[data-page="agent"]').contains(document.getElementById('agent-thread')),
        document.querySelectorAll('#agent-thread .agent-msg-user').length,
        document.getElementById('agent-drawer').hidden]""")
    # From the Agent page, the keys go back to the page before it, beside.
    tab.keyboard.press("Control+j")
    tab.wait_for_timeout(200)
    back = _where(tab)
    tab.context.close()
    assert on_the_page == [True, 1, True]
    assert back["page"] == "report" and back["beside"] and back["thread"]


def test_a_dialog_keeps_the_keyboard_and_closing_keeps_the_focus(browser, session) -> None:
    tab = _open(browser, session, "#overview")
    tab.evaluate("() => window.FastMDXDialog.open('prefs-dialog')")
    tab.keyboard.press("Control+j")
    behind = tab.evaluate("() => !document.getElementById('agent-drawer').hidden")
    tab.evaluate("() => window.FastMDXDialog.close('prefs-dialog')")
    tab.keyboard.press("Control+j")
    tab.wait_for_function("() => document.activeElement.id === 'agent-request'")
    # Escape in the drawer closes it, and the focus goes back to its button.
    tab.keyboard.press("Escape")
    said = tab.evaluate("() => [document.getElementById('agent-drawer').hidden, document.activeElement.id]")
    tab.context.close()
    assert not behind
    assert said == [True, "agent-beside-open"]


def test_in_autonomous_mode_a_suggestion_waits_to_be_read(browser, session) -> None:
    tab = _open(browser, session, "#agent")
    tab.evaluate("""() => { const m = document.getElementById('agent-mode');
        m.value = 'autonomous'; m.dispatchEvent(new Event('change')); }""")
    # A fresh thread: the one begun in another test is kept with the study.
    tab.click("#agent-new")
    tab.wait_for_function("() => !document.getElementById('agent-start').hidden")
    starter = tab.locator(".agent-starter", has_text="Ubiquitin in water")
    starter.click()
    said = tab.evaluate("""() => [document.getElementById('agent-request').value,
        document.querySelectorAll('#agent-thread .agent-msg-user').length]""")
    tab.evaluate("""() => { const m = document.getElementById('agent-mode');
        m.value = 'assisted'; m.dispatchEvent(new Event('change')); }""")
    tab.context.close()
    assert "1UBQ" in said[0] and said[1] == 0


def test_new_study_opens_the_builder_with_the_agent_beside_it(browser, session) -> None:
    """A study can be described to the Agent or set field by field: New
    study opens both, the builder keeping the keyboard."""
    tab = _open(browser, session, "#overview")
    tab.click('.sidebar-start a[data-view-link="run"]')
    tab.wait_for_function("() => !document.getElementById('agent-drawer').hidden")
    opened = _where(tab)
    focused = tab.evaluate("() => document.activeElement.id")
    placeholder = tab.get_attribute("#agent-request", "placeholder")
    tab.click('.sidebar-start a[data-view-link="run"]')
    tab.wait_for_timeout(100)
    again = _where(tab)
    tab.keyboard.press("Control+j")
    closed = tab.get_attribute("#agent-request", "placeholder")
    tab.context.close()
    assert opened["page"] == "run" and opened["beside"] and opened["thread"]
    assert opened["overlap"] is False and focused != "agent-request"
    # Pressed again, it stays open, and the page's own prompt is put back
    # once it closes.
    assert again["beside"] and placeholder == "Ask about this study, or describe one."
    assert closed != placeholder
