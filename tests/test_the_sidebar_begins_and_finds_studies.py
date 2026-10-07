"""The sidebar as a study is begun and found from it.

New study and the Agent first, kept at the top, then the workspace's newest
studies, All studies, and the study on screen: its system large, where it
stands, and its folder, shortened in the middle to fit and copied on a
click. Folded, the sidebar is a strip of icons under FastMDXplora's mark; it
was nothing and a tab at the window's edge.
"""

from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_the_drawing_scripts_run_in_a_browser import _write_study  # noqa: E402
from tests.test_the_workspace_says_its_studies import _study  # noqa: E402

FOLDER = "fastmdxplora_output_20260919_021313_trialanine_long_name"


@pytest.fixture(scope="module")
def session(tmp_path_factory):
    from fastmdxplora.gui.server import start_dashboard_session

    workspace = tmp_path_factory.mktemp("sidebar")
    _study(workspace / "ubiquitin", system="1UBQ", started="2026-09-01T10:00:00+00:00")
    _study(workspace / "chignolin", system="5AWL", started="2026-09-02T10:00:00+00:00")
    open_study = _write_study(workspace / FOLDER)
    started = start_dashboard_session(output=str(open_study), host="127.0.0.1", port=0)
    yield started
    started.server.shutdown()


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        launched = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        yield launched
        launched.close()


def _open(browser, session, where="#overview", **context):
    tab = browser.new_context(viewport={"width": 1440, "height": 900},
                              permissions=["clipboard-read", "clipboard-write"], **context).new_page()
    tab.set_default_timeout(60000)
    tab.mouse.move(720, 450)
    tab.goto(session.url + where, wait_until="domcontentloaded")
    tab.wait_for_function("() => document.body.classList.contains('state-ready') && "
                          "!document.getElementById('study-folder').hidden && "
                          "document.querySelectorAll('#sidebar-recent-list .sidebar-recent-item').length")
    return tab


def test_recent_lists_the_newest_and_marks_the_open_one(browser, session) -> None:
    tab = _open(browser, session)
    said = tab.evaluate("""() => [...document.querySelectorAll('#sidebar-recent-list .sidebar-recent-item')]
        .map(b => [b.querySelector('.sidebar-recent-name').textContent,
                   b.getAttribute('aria-current') === 'true'])""")
    tab.context.close()
    assert len(said) == 3 and [here for _, here in said].count(True) == 1
    assert {name for name, _ in said} >= {"1UBQ", "5AWL"}


def test_recent_stays_folded_once_folded(browser, session) -> None:
    tab = _open(browser, session)
    tab.click("#sidebar-recent > summary")
    tab.wait_for_function("() => localStorage.getItem('fmx.recentOpen') === '0'")
    tab.reload(wait_until="domcontentloaded")
    tab.wait_for_function("() => document.body.classList.contains('state-ready')")
    folded = tab.evaluate("() => !document.getElementById('sidebar-recent').open")
    tab.click("#sidebar-recent > summary")
    tab.context.close()
    assert folded


def test_the_folder_is_shortened_in_the_middle_and_copied(browser, session) -> None:
    tab = _open(browser, session)
    said = tab.evaluate("""() => {
        const name = document.getElementById('study-folder-name');
        return {shown: name.textContent, fits: name.scrollWidth <= name.clientWidth + 1,
                system: document.getElementById('topbar-run-title').textContent,
                size: parseFloat(getComputedStyle(document.getElementById('topbar-run-title')).fontSize)};
    }""")
    tab.click("#study-folder")
    copied = tab.evaluate("() => navigator.clipboard.readText()")
    tab.context.close()
    assert "…" in said["shown"] and said["fits"], said
    head, tail = said["shown"].split("…")
    assert FOLDER.startswith(head) and FOLDER.endswith(tail) and len(head) > 5 and len(tail) > 5
    assert said["size"] >= 16
    assert copied.rstrip("/").endswith(FOLDER)


def test_folded_it_is_a_strip_of_icons_that_still_take_you_there(browser, session) -> None:
    tab = _open(browser, session)
    tab.click("#sidebar-collapse")
    tab.mouse.move(720, 450)
    tab.wait_for_timeout(300)
    said = tab.evaluate("""() => {
        const sidebar = document.querySelector('.sidebar');
        const shown = (el) => !!el && getComputedStyle(el).display !== 'none'
            && el.getBoundingClientRect().width > 0;
        return {width: Math.round(sidebar.getBoundingClientRect().width),
                icons: [...sidebar.querySelectorAll('.nav-link')].filter(a => shown(a.querySelector('svg'))).length,
                words: [...sidebar.querySelectorAll('.nav-link > span')].filter(shown).length,
                card: shown(document.getElementById('study-card')),
                mark: shown(document.querySelector('#sidebar-expand .strip-mark'))};
    }""")
    tab.click('.sidebar-pages .nav-link[data-view-link="analysis"]')
    page = tab.evaluate("() => document.documentElement.dataset.page")
    # Pointing at an icon of the strip uses it; it does not open the sidebar.
    box = tab.locator('.sidebar-pages .nav-link[data-view-link="report"]').bounding_box()
    tab.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    tab.wait_for_timeout(400)
    peeked = tab.evaluate("() => document.body.classList.contains('sidebar-peek')")
    tab.click("#sidebar-expand")
    tab.context.close()
    # Recent and Chats among them, as icons.
    assert said == {"width": 56, "icons": 10, "words": 0, "card": False, "mark": True}
    assert page == "analysis" and not peeked


def test_folded_recent_shows_its_studies_beside_the_strip(browser, session) -> None:
    """Folded, Recent was gone with the rest of the sidebar's words. It is
    an icon of the strip, and a click shows its studies beside it."""
    tab = _open(browser, session)
    tab.click("#sidebar-recent > summary")              # folded where shown
    tab.click("#sidebar-collapse")
    tab.mouse.move(720, 450)
    tab.click("#sidebar-recent > summary")
    tab.wait_for_selector("#sidebar-recent.flyout .sidebar-recent-item")
    said = tab.evaluate("""() => {
        const list = document.getElementById('sidebar-recent-list').getBoundingClientRect();
        const head = document.querySelector('#sidebar-recent > summary').getBoundingClientRect();
        return {beside: list.left >= 56, level: Math.abs(list.top - head.top) <= 1,
                shown: document.querySelectorAll('#sidebar-recent-list .sidebar-recent-item').length,
                peeked: document.body.classList.contains('sidebar-peek'),
                expanded: document.querySelector('#sidebar-recent > summary')
                    .getAttribute('aria-expanded')};
    }""")
    tab.keyboard.press("Escape")
    gone = tab.evaluate("() => !document.getElementById('sidebar-recent').classList.contains('flyout')")
    tab.click("#sidebar-recent > summary")
    tab.click('#sidebar-recent-list .sidebar-recent-item:not([aria-current="true"])')
    tab.wait_for_function("() => !document.getElementById('sidebar-recent').classList.contains('flyout')")
    tab.click("#sidebar-expand")
    kept = tab.evaluate("() => [document.getElementById('sidebar-recent').open, "
                        "localStorage.getItem('fmx.recentOpen')]")
    tab.click("#sidebar-recent > summary")              # shown again for the others
    tab.context.close()
    assert said == {"beside": True, "level": True, "shown": 3, "peeked": False, "expanded": "true"}
    assert gone
    # The fold kept for the sidebar shown is as it was left.
    assert kept == [False, "0"]


def test_folded_chats_show_beside_the_strip_with_a_new_one(browser, session) -> None:
    """The strip had no Chats: folded, a chat was out of reach but through
    the Agent's own list. It is an icon there, as Recent is, its list beside
    the strip and kept inside the window, a new chat its first row."""
    tab = _open(browser, session)
    tab.click("#sidebar-collapse")
    tab.mouse.move(720, 450)
    tab.click("#sidebar-chats > summary")
    tab.wait_for_selector("#sidebar-chats.flyout .sidebar-conv-new")
    said = tab.evaluate("""() => {
        const list = document.getElementById('sidebar-chats-list').getBoundingClientRect();
        return {beside: list.left >= 56, inside: list.bottom <= window.innerHeight,
                first: document.querySelector('#sidebar-chats-list > *').textContent,
                peeked: document.body.classList.contains('sidebar-peek'),
                expanded: document.querySelector('#sidebar-chats > summary')
                    .getAttribute('aria-expanded')};
    }""")
    tab.click("#sidebar-chats-list .sidebar-conv-new")
    tab.wait_for_function("() => !document.getElementById('agent-drawer').hidden")
    gone = tab.evaluate("() => !document.getElementById('sidebar-chats').classList.contains('flyout')")
    tab.click("#sidebar-expand")
    hidden = tab.evaluate("""() => getComputedStyle(
        document.querySelector('#sidebar-chats-list .sidebar-conv-new')).display""")
    tab.context.close()
    assert said == {"beside": True, "inside": True, "first": "New chat", "peeked": False,
                    "expanded": "true"}
    assert gone
    # With the sidebar shown, its head's + is the way; the row is not shown.
    assert hidden == "none"


def test_the_mark_pointed_at_and_clicked_where_it_is_keeps_the_sidebar(browser, session) -> None:
    """Pointed at, the mark shows the sidebar over the page; it moved to the
    sidebar's edge, and a click where it had been landed on the product's
    name, so the sidebar was never kept."""
    tab = _open(browser, session)
    tab.click("#sidebar-collapse")
    tab.mouse.move(720, 450)
    tab.wait_for_timeout(300)
    box = tab.locator("#sidebar-expand").bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    tab.mouse.move(x, y)
    tab.wait_for_timeout(200)
    under = tab.evaluate(f"() => document.elementFromPoint({x}, {y}).closest('#sidebar-expand') !== null")
    tab.mouse.click(x, y)
    tab.mouse.move(720, 450)
    tab.wait_for_timeout(400)
    kept = tab.evaluate("() => !document.body.classList.contains('sidebar-collapsed')")
    first = tab.evaluate("() => document.querySelector('.app-shell').firstElementChild.id")
    tab.context.close()
    assert under and kept
    assert first == "sidebar-expand"


def test_shown_over_the_page_it_has_one_button_and_a_click_keeps_it(browser, session) -> None:
    """Pointed at, the folded sidebar showed both its keep button and its
    own fold button, the opposite way; and only the small button kept it.
    The side panel the same: a click on it, where nothing of its own is,
    keeps it."""
    tab = _open(browser, session, "#viewer")
    tab.wait_for_function("() => document.body.classList.contains('sidebar-collapsed')")
    tab.mouse.move(720, 450)
    said = {}
    for which, button, column in (("sidebar", "#sidebar-expand", ".sidebar"),
                                  ("panel", "#side-expand", "#side-panel")):
        box = tab.locator(button).bounding_box()
        tab.mouse.move(box["x"] + box["width"] / 2 + 1, box["y"] + box["height"] / 2)
        tab.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        tab.wait_for_function(f"() => document.body.classList.contains('{which}-peek')")
        if which == "sidebar":
            said["folds"] = tab.evaluate(
                "() => getComputedStyle(document.getElementById('sidebar-collapse')).display")
        # Onto the column, to a place in it with nothing of its own.
        x, y = tab.evaluate(f"""() => {{
            const r = document.querySelector('{column}').getBoundingClientRect();
            const x = r.left + r.width / 2;
            for (let y = r.bottom - 90; y > r.top + 60; y -= 10) {{
                const at = document.elementFromPoint(x, y);
                if (at && !at.closest('a, button, input, select, textarea, label, summary, details, [tabindex]'))
                    return [x, y];
            }}
            return [x, r.top + 60];
        }}""")
        tab.mouse.move(x, y)
        empty = tab.evaluate(f"""() => !document.elementFromPoint({x}, {y})
            .closest('a, button, input, select, textarea, label, summary, details')""")
        tab.mouse.click(x, y)
        tab.mouse.move(720, 450)
        tab.wait_for_timeout(400)
        said[which] = [empty, tab.evaluate(
            f"() => !document.body.classList.contains('{which}' === 'sidebar' ? 'sidebar-collapsed' : 'panel-collapsed')")]
    tab.context.close()
    assert said == {"folds": "none", "sidebar": [True, True], "panel": [True, True]}


@pytest.mark.parametrize("width,height", [(1440, 900), (1280, 800)])
def test_the_window_does_not_scroll_past_its_columns(browser, session, width, height) -> None:
    """A screen-reader legend at the foot of the Viewer's settings sat
    outside its column, and the whole window scrolled on past it, 200
    pixels of nothing but the ground."""
    context = browser.new_context(viewport={"width": width, "height": height})
    tab = context.new_page()
    tab.set_default_timeout(60000)
    tab.goto(session.url + "#overview", wait_until="domcontentloaded")
    tab.wait_for_function("() => document.body.classList.contains('state-ready')")
    tall = {}
    for page in ("overview", "viewer", "analysis", "report", "files", "run", "agent", "studies"):
        tab.evaluate(f"() => window.FastMDXDashboard.navigate('{page}')")
        tab.wait_for_timeout(400)
        tall[page] = tab.evaluate("() => document.scrollingElement.scrollHeight - innerHeight")
    context.close()
    assert all(over <= 0 for over in tall.values()), tall
