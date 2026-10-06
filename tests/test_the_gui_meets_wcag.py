"""The GUI holds to WCAG 2.1 AA where an audit found it did not.

An accessibility audit of every page in every scheme (axe-core 4.13, its
WCAG 2.0, 2.1 and 2.2 A and AA rules) found three things. The muted text of
every scheme read at 3.6 to 4.5 to 1 on its own grounds, under the 4.5 the
standard asks of text (now held by `test_every_scheme_can_be_read`). The
information beside the viewer was a `tablist` of plain buttons, so a screen
reader was told of tabs and found none, and nothing said which was chosen.
And the preview and the viewer were named with `aria-label` on elements with
no role, where a label is not read.
"""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

import pytest

from tests import viewer_hooks as hooks

TEMPLATE = (Path(__file__).resolve().parents[1] / "src" / "fastmdxplora" / "gui"
            / "templates" / "dashboard.html")

#: Elements a label may name without a role: controls, landmarks, lists,
#: tables and pictures, whose own roles take a name.
NAMEABLE = {"button", "a", "input", "select", "textarea", "aside", "nav", "section",
            "main", "header", "footer", "form", "dialog", "svg", "iframe", "img",
            "fieldset", "details", "summary", "output", "progress", "meter", "table",
            "ol", "ul"}


class _Elements(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.found: list[tuple[str, dict[str, str]]] = []

    def handle_starttag(self, tag, attrs):
        self.found.append((tag, {k: v or "" for k, v in attrs}))


def _elements() -> list[tuple[str, dict[str, str]]]:
    parsed = _Elements()
    parsed.feed(TEMPLATE.read_text(encoding="utf-8"))
    return parsed.found


def test_a_label_is_on_something_that_takes_one():
    unread = [(tag, attrs.get("id") or attrs.get("class")) for tag, attrs in _elements()
              if "aria-label" in attrs and "role" not in attrs and tag not in NAMEABLE]
    assert unread == []


def test_every_tab_is_a_tab_of_its_own_panel():
    elements = _elements()
    ids = {attrs.get("id"): (tag, attrs) for tag, attrs in elements if attrs.get("id")}
    tabs = [attrs for _, attrs in elements if "info-tab" in attrs.get("class", "").split()]
    assert len(tabs) == 4
    assert all(attrs.get("role") == "tab" for attrs in tabs)
    assert [attrs["aria-selected"] for attrs in tabs] == ["true", "false", "false", "false"]
    assert [attrs["tabindex"] for attrs in tabs] == ["0", "-1", "-1", "-1"]
    for attrs in tabs:
        _, panel = ids[attrs["aria-controls"]]
        assert panel.get("role") == "tabpanel"
        assert panel.get("aria-labelledby") == attrs["id"]


def test_the_tabs_are_used_from_the_keyboard(tmp_path):
    pytest.importorskip("playwright.sync_api")
    pytest.importorskip("mdtraj")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_the_drawing_scripts_run_in_a_browser import _write_study

    study = _write_study(tmp_path / "study")
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#viewer", wait_until="domcontentloaded")
            page.wait_for_function("() => window.FastMDXDashboard")
            hooks.tool(page, "side-info")
            page.focus("#info-tab-structure")

            def chosen():
                return page.evaluate("""() => [
                    document.activeElement.id,
                    [...document.querySelectorAll('.info-tab')]
                        .filter(t => t.getAttribute('aria-selected') === 'true').map(t => t.id),
                    [...document.querySelectorAll('.info-pane')]
                        .filter(p => !p.hidden).map(p => p.id)]""")

            seen = []
            for key in ("ArrowRight", "ArrowRight", "End", "ArrowRight", "ArrowLeft", "Home"):
                page.keyboard.press(key)
                seen.append(chosen())
            browser.close()
    finally:
        session.server.shutdown()
    names = ["structure", "simulation", "selection", "ligand"]
    expected = [names[i] for i in (1, 2, 3, 0, 3, 0)]
    assert seen == [[f"info-tab-{n}", [f"info-tab-{n}"], [f"info-pane-{n}"]] for n in expected]
    assert errors == []
