"""The sidebar as a study is begun and found from it.

New study and the Agent first, kept at the top, then the workspace's newest
studies, All studies, and the study on screen: its system large, where it
stands, and its folder, shortened in the middle to fit and copied on a
click. Folded, the sidebar is a strip of icons under the lab's mark; it
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
    assert said == {"width": 56, "icons": 8, "words": 0, "card": False, "mark": True}
    assert page == "analysis" and not peeked


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
