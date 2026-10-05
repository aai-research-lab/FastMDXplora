"""Until a scheme is chosen, the page follows the system's, as it changes.

The first visit took the scheme the system asked for and stored it as if
chosen, so a system that went dark at sunset left the page light for good.
Only a scheme chosen on the page is kept now.
"""

from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def session(tmp_path_factory):
    from fastmdxplora.gui.server import start_dashboard_session

    started = start_dashboard_session(output=str(tmp_path_factory.mktemp("scheme")),
                                      host="127.0.0.1", port=0)
    yield started
    started.server.shutdown()


def _page(browser, session, scheme):
    context = browser.new_context(color_scheme=scheme)
    page = context.new_page()
    page.set_default_timeout(60000)
    page.goto(session.url, wait_until="domcontentloaded")
    page.wait_for_function("() => document.documentElement.dataset.theme")
    return page


def _theme(page) -> str:
    return page.evaluate("() => document.documentElement.dataset.theme")


def test_it_follows_the_system_until_one_is_chosen(session) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = _page(browser, session, "light")
        assert _theme(page) == "light"
        page.emulate_media(color_scheme="dark")
        page.wait_for_function("() => document.documentElement.dataset.theme === 'dark'")
        assert page.evaluate("() => localStorage.getItem('fmx.theme')") is None

        # In Settings, which is closed; the button is what is being tested.
        page.evaluate("() => document.querySelector(\".seg-btn[data-theme='light']\").click()")
        assert page.evaluate("() => localStorage.getItem('fmx.theme')") == "light"
        page.emulate_media(color_scheme="light")
        page.emulate_media(color_scheme="dark")
        page.wait_for_timeout(300)
        assert _theme(page) == "light", "a chosen scheme is kept"

        # System chosen again: the page follows the computer once more.
        page.evaluate("() => document.querySelector(\".seg-btn[data-theme='system']\").click()")
        page.wait_for_function("() => document.documentElement.dataset.theme === 'dark'")
        assert page.evaluate("() => localStorage.getItem('fmx.theme')") == "system"
        browser.close()
