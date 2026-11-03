"""The Agent's mode is shown by its name alone, its description on hover and
in Settings (user, 11-03: "Can you make Agent mode just mention the name and
not assisted, autonomous .... the details should be in the description in
settings and on hover").

Where the mode is shown (under the composer, the gear's menu, the Settings
page, the status bar, the choice itself), it reads "Seeker" or "Ranger";
"Seeker: built-in, assisted" was said in each. What it is and does is said
under the choice in the Agent's settings and under its name on the Settings
page, and on hover wherever the name is.
"""

from __future__ import annotations

import pytest

from tests.test_the_workspace_says_its_studies import _study


@pytest.fixture(scope="module")
def browser():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        launched = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        yield launched
        launched.close()


@pytest.fixture
def session(tmp_path):
    from fastmdxplora.gui.server import start_dashboard_session

    study = _study(tmp_path / "ubiquitin", system="1UBQ")
    started = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    yield started
    started.server.shutdown()


SHOWN = """() => {
    const of = (id) => { const e = document.getElementById(id); return [e.textContent.trim(), e.title]; };
    const select = document.getElementById('agent-mode');
    return {
        footer: of('agent-footer-mode'),
        footerButton: document.getElementById('agent-settings-open').title,
        menu: of('settings-mode'),
        page: of('settings-page-mode'),
        pageSaid: (document.getElementById('settings-page-mode-said') || {textContent: ''}).textContent.trim(),
        bar: of('sb-agent'),
        choices: [...select.options].map((o) => [o.text, o.title]),
        note: document.getElementById('agent-mode-note').textContent.trim(),
    };
}"""


def test_the_mode_is_its_name_and_its_description_is_on_hover(browser, session):
    page = browser.new_context(viewport={"width": 1440, "height": 900}).new_page()
    page.set_default_timeout(60000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(session.url + "#settings", wait_until="domcontentloaded")
    page.wait_for_selector("body:not(.state-loading)")
    page.wait_for_function("() => !document.getElementById('sb-agent').hidden")
    seeker = page.evaluate(SHOWN)
    page.evaluate("() => window.FastMDXAgent.openSettings()")
    page.select_option("#agent-mode", "autonomous")
    page.keyboard.press("Escape")
    page.wait_for_function("() => document.getElementById('settings-page-mode').textContent.startsWith('Ranger')")
    ranger = page.evaluate(SHOWN)
    page.context.close()
    assert errors == []
    hover = "Seeker: built-in, assisted. I show you each one first. Nothing runs until you say so."
    assert seeker["footer"] == ["Seeker", hover]
    assert seeker["footerButton"] == hover + "\nOpen the Agent's settings"
    assert seeker["menu"] == ["Seeker", hover] and seeker["page"] == ["Seeker", hover]
    assert seeker["pageSaid"] == "Built-in, assisted. I show you each one first. Nothing runs until you say so."
    assert seeker["bar"][0] == "Agent · Seeker"
    assert seeker["bar"][1].startswith("The Agent's mode: " + hover)
    assert [c[0] for c in seeker["choices"]] == ["Seeker", "Ranger", "Unvalidated"]
    assert seeker["choices"][1][1].startswith("Ranger: built-in, autonomous. I run it")
    assert seeker["note"] == "Built-in, assisted. I show you each one first. Nothing runs until you say so."
    assert ranger["footer"][0] == ranger["menu"][0] == ranger["page"][0] == "Ranger"
    assert ranger["bar"][0] == "Agent · Ranger"
    assert ranger["pageSaid"].startswith("Built-in, autonomous. I run it without showing it to you first")
    assert ranger["note"] == ranger["pageSaid"]
    # Nowhere is the name said with its kind beside it.
    for shown in (seeker, ranger):
        for where in ("footer", "menu", "page", "bar"):
            assert "built-in" not in shown[where][0], (where, shown[where])
