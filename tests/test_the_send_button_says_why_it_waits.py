"""The Agent's Send says why it waits, on its button as beside the box.

While the page reads the study open and its conversation, the note beside
the box said so, and the button's hover text said the page was waiting for
a run to start, with no run anywhere.
"""

from __future__ import annotations

import tempfile
import time

import pytest

from tests._agent_page import ready

pytest.importorskip("playwright.sync_api")

SEND_READS = "Send waits until this page has read the study open and its conversation."


def test_while_the_page_reads_its_study_send_says_so(tmp_path, monkeypatch) -> None:
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui import agent_panel
    from fastmdxplora.gui.server import start_dashboard_session

    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", tempfile.mkdtemp())
    read = agent_panel.read_conversation

    def slow(*args, **kwargs):
        # The study's conversation read slowly, as on a loaded machine.
        time.sleep(4)
        return read(*args, **kwargs)

    monkeypatch.setattr(agent_panel, "read_conversation", slow)
    study = tmp_path / "study"
    (study / "simulation").mkdir(parents=True)
    (study / "exploration.yml").write_text("systems: []\n", encoding="utf-8")
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#agent", wait_until="domcontentloaded")
            page.wait_for_function("() => window.FastMDXAgentPanel"
                                   " && window.FastMDXAgentPanel.waits === 'read'")
            page.fill("#agent-request", "How long will it take?")
            waiting = page.get_attribute("#agent-propose", "title")
            ready(page)
            page.dispatch_event("#agent-request", "input")
            offered = page.get_attribute("#agent-propose", "title")
            browser.close()
    finally:
        session.server.shutdown()
    assert waiting == SEND_READS, waiting
    assert offered == "Send (Enter). Shift+Enter for a new line.", offered
    assert errors == []
