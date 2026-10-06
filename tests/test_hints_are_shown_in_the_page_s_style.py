"""A hint is shown in the page's style: rounded, in the scheme's colours.

Every hint in the GUI is an element's title, which the browser drew in its
own square box in the system's colours, whatever the scheme. While the
pointer is on such an element, or the keyboard has brought focus to it, its
title is now taken off, so the browser shows nothing, and shown in a
rounded box of the page's own, described to a screen reader as the
element's description; it goes back as the pointer or focus leaves.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_the_drawing_scripts_run_in_a_browser import _write_study  # noqa: E402

SHOWN = "() => { const tip = document.getElementById('fmx-tooltip'); return !!tip && !tip.hidden; }"
SEEN = """(id) => {
    const tip = document.getElementById('fmx-tooltip');
    const el = document.getElementById(id);
    const style = tip && getComputedStyle(tip);
    const probe = document.createElement('span');
    probe.style.color = 'var(--background-elevated)';
    document.body.appendChild(probe);
    const ground = getComputedStyle(probe).color;
    probe.remove();
    const box = tip && tip.getBoundingClientRect(), at = el.getBoundingClientRect();
    return {
        shown: !!tip && !tip.hidden && style.display !== 'none',
        text: tip ? tip.textContent : '',
        radius: style ? style.borderTopLeftRadius : '',
        ground: style ? style.backgroundColor : '', elevated: ground,
        title: el.getAttribute('title'), described: el.getAttribute('aria-describedby'),
        role: tip ? tip.getAttribute('role') : '',
        inside: !!box && box.left >= 0 && box.right <= innerWidth
            && box.top >= 0 && box.bottom <= innerHeight,
        below: !!box && box.top >= at.bottom,
    };
}"""


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    study = _write_study(tmp_path_factory.mktemp("hints") / "study")
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        tab = browser.new_page(viewport={"width": 1440, "height": 900})
        tab.set_default_timeout(60000)
        tab.goto(session.url + "#overview", wait_until="domcontentloaded")
        tab.wait_for_function("() => document.body.classList.contains('state-ready')")
        yield tab
        browser.close()
    session.server.shutdown()


def test_a_hint_is_a_rounded_box_in_the_scheme_s_colours(page) -> None:
    page.mouse.move(1000, 500)
    page.hover("#sidebar-collapse")
    page.wait_for_function(SHOWN)
    seen = page.evaluate(SEEN, "sidebar-collapse")
    assert seen["shown"] and seen["text"] == "Collapse sidebar" and seen["role"] == "tooltip"
    assert seen["radius"] == "8px" and seen["ground"] == seen["elevated"]
    # The browser has no title to make its own box from, and a screen reader
    # is told the hint.
    assert seen["title"] is None and seen["described"] == "fmx-tooltip"
    assert seen["inside"] and seen["below"]

    page.mouse.move(1000, 500)
    gone = page.evaluate(SEEN, "sidebar-collapse")
    assert not gone["shown"]
    assert gone["title"] == "Collapse sidebar" and gone["described"] is None


def test_a_title_the_page_changes_is_shown_as_changed(page) -> None:
    page.mouse.move(1000, 500)
    page.hover("#sidebar-collapse")
    page.wait_for_function(SHOWN)
    page.evaluate("() => { document.getElementById('sidebar-collapse').title = 'Fold the sidebar'; }")
    page.wait_for_function("() => document.getElementById('fmx-tooltip').textContent "
                           "=== 'Fold the sidebar'")
    assert page.evaluate("() => document.getElementById('sidebar-collapse').getAttribute('title')") \
        is None
    page.mouse.move(1000, 500)
    assert page.evaluate("() => document.getElementById('sidebar-collapse').title") == "Fold the sidebar"
    page.evaluate("() => { document.getElementById('sidebar-collapse').title = 'Collapse sidebar'; }")


def test_a_press_puts_it_away_and_the_title_stays_off_till_the_pointer_leaves(page) -> None:
    page.mouse.move(1000, 500)
    page.hover("#sidebar-collapse")
    page.wait_for_function(SHOWN)
    page.mouse.down()
    seen = page.evaluate(SEEN, "sidebar-collapse")
    # Let go elsewhere, so the press is not a click that folds the sidebar.
    page.mouse.move(1000, 500)
    page.mouse.up()
    assert not seen["shown"] and seen["title"] is None
    assert page.evaluate(SEEN, "sidebar-collapse")["title"] == "Collapse sidebar"
    assert page.evaluate("() => !document.body.classList.contains('sidebar-collapsed')")


def test_the_keyboard_brings_it_at_once(page) -> None:
    page.mouse.move(1000, 500)
    # Focus comes to it afresh: a press before left it there.
    page.evaluate("() => document.activeElement?.blur()")
    page.keyboard.press("Shift")
    page.focus("#sidebar-collapse")
    seen = page.evaluate(SEEN, "sidebar-collapse")
    assert seen["shown"] and seen["text"] == "Collapse sidebar", seen
    assert seen["described"] == "fmx-tooltip", seen
    page.keyboard.press("Escape")
    after = page.evaluate(SEEN, "sidebar-collapse")
    assert not after["shown"], after
    page.evaluate("() => document.activeElement.blur()")
    assert page.evaluate(SEEN, "sidebar-collapse")["title"] == "Collapse sidebar"


def test_a_hint_inside_another_is_shown_alone(page) -> None:
    # The study's card switches study; the platform inside it says what it is.
    page.mouse.move(1000, 500)
    page.evaluate("""() => {
        const outer = document.createElement('div');
        outer.id = 'outer-hint'; outer.title = 'Switch study';
        outer.style.cssText = 'position:fixed;left:600px;top:300px;width:200px;height:80px';
        const inner = document.createElement('span');
        inner.id = 'inner-hint'; inner.title = 'Platform';
        inner.style.cssText = 'position:absolute;left:120px;top:30px;width:60px;height:20px';
        outer.appendChild(inner); document.body.appendChild(outer); }""")
    try:
        page.mouse.move(620, 310)
        page.wait_for_function(SHOWN)
        page.mouse.move(640, 320)
        page.mouse.move(740, 340)
        page.wait_for_function("() => document.getElementById('fmx-tooltip').textContent "
                               "=== 'Platform'")
        titles = page.evaluate("""() => [document.getElementById('outer-hint').getAttribute('title'),
            document.getElementById('inner-hint').getAttribute('title')]""")
        # The outer one's title is back, the inner one's off: one box, the page's.
        assert titles == ["Switch study", None]
        page.mouse.move(620, 310)
        page.wait_for_function("() => document.getElementById('fmx-tooltip').textContent "
                               "=== 'Switch study'")
    finally:
        page.mouse.move(1000, 500)
        page.evaluate("() => document.getElementById('outer-hint').remove()")


def test_an_element_built_again_under_the_pointer_takes_its_hint_with_it(page) -> None:
    page.mouse.move(1000, 500)
    page.evaluate("""() => { const el = document.createElement('button');
        el.id = 'rebuilt'; el.title = 'Old'; el.textContent = 'x';
        el.style.cssText = 'position:fixed;left:600px;top:300px;width:80px;height:30px';
        document.body.appendChild(el); }""")
    page.mouse.move(620, 310)
    page.wait_for_function(SHOWN)
    page.evaluate("() => document.getElementById('rebuilt').remove()")
    page.wait_for_function("() => document.getElementById('fmx-tooltip').hidden")
    page.mouse.move(1000, 500)


def test_the_standalone_dashboard_shows_them_too(page, tmp_path) -> None:
    from fastmdxplora.gui.report_dashboard import build_dashboard

    (tmp_path / "manifest.json").write_text(json.dumps({"system": "1L2Y", "phases": []}),
                                            encoding="utf-8")
    build_dashboard(orchestrator=SimpleNamespace(output_dir=tmp_path, system="1L2Y"),
                    output_dir=tmp_path / "report", title="A study")
    tab = page.context.browser.new_page(viewport={"width": 1280, "height": 800})
    try:
        tab.goto((tmp_path / "report" / "dashboard.html").as_uri())
        hinted = tab.evaluate("""() => { const el = [...document.querySelectorAll('[title]')]
            .find(e => e.getBoundingClientRect().width > 0); el.id = el.id || 'hinted';
            return el.id; }""")
        tab.hover(f"#{hinted}")
        tab.wait_for_function(SHOWN)
        seen = tab.evaluate(SEEN, hinted)
    finally:
        tab.close()
    assert seen["shown"] and seen["radius"] == "8px"
