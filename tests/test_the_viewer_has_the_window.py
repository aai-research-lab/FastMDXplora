"""The Viewer has the window, in one shape, and every page its centre.

Asked for (10-05): the band behind each page's title looked like a strip
of another colour over the column's own ground, since it was the page's
reading width; the Sequence and the playback under the molecule could not
be closed as the settings' sections can; the molecule's frame took every
pixel of width it was given, whatever its shape; and the sidebar and the
log stayed beside the molecule. Now the band spans the column; the two
fold and stay as left; the frame is 4 wide to 3 high, as large as the
window allows with the sequence and the playback in view, and centred
with its settings; opening the Viewer closes both columns, which come
back as they were on another page; pointing at a closed column's button
shows it for as long as the pointer stays, and a click keeps it.
"""

from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_the_drawing_scripts_run_in_a_browser import _write_study  # noqa: E402


@pytest.fixture(scope="module")
def session(tmp_path_factory):
    from fastmdxplora.gui.server import start_dashboard_session

    root = _write_study(tmp_path_factory.mktemp("window") / "study")
    started = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    yield started
    started.server.shutdown()


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        launched = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        yield launched
        launched.close()


def _open(browser, session, page="overview", width=1440, height=900, stored=None):
    context = browser.new_context(viewport={"width": width, "height": height})
    kept = {"panelCollapsed": "0", "sidebarCollapsed": "0", **(stored or {})}
    context.add_init_script(
        "(() => { if (sessionStorage.getItem('kept')) return; sessionStorage.setItem('kept', '1');"
        + "".join(f"localStorage.setItem('fmx.{k}', '{v}');" for k, v in kept.items()) + "})()")
    tab = context.new_page()
    tab.set_default_timeout(60000)
    tab.goto(session.url, wait_until="domcontentloaded")
    tab.wait_for_function("() => window.FastMDXDashboard && window.FastMDXDashboard.navigate")
    _go(tab, page)
    return tab


def _go(tab, page):
    tab.evaluate(f"() => window.FastMDXDashboard.navigate('{page}')")
    tab.wait_for_timeout(400)


def _closed(tab):
    return tab.evaluate("""() => [document.body.classList.contains('sidebar-collapsed'),
                                  document.body.classList.contains('panel-collapsed')]""")


def _settled(tab):
    """The Viewer's frame once the sequence and the playback are shown and
    it has been sized for them."""
    tab.wait_for_selector("#sequence-strip:not([hidden])")
    tab.wait_for_selector("#trajectory-row:not([hidden])")
    tab.wait_for_timeout(800)
    return tab.evaluate("""() => {
        const r = (n) => n.getBoundingClientRect();
        const frame = r(document.getElementById('viewer-canvas-frame'));
        const fold = r(document.getElementById('viewer-under-fold'));
        const side = r(document.querySelector('.viewer-side'));
        const header = r(document.querySelector('.page[data-page="viewer"] .page-header'));
        const layout = r(document.querySelector('.viewer-layout'));
        const main = r(document.querySelector('.main'));
        return {width: frame.width, height: frame.height, bottom: fold.bottom,
                left: frame.left - main.left, right: main.right - side.right,
                headerLeft: header.left, layoutLeft: layout.left, inner: innerHeight};
    }""")


def test_the_title_band_spans_the_column(browser, session) -> None:
    tab = _open(browser, session, stored={"panelCollapsed": "1"})
    # Once the page has loaded and its colours have stopped changing.
    tab.wait_for_selector("body:not(.state-loading)")
    tab.wait_for_timeout(1000)
    band = tab.evaluate("""() => {
        const header = document.querySelector('.page[data-page="overview"] .page-header');
        const main = document.querySelector('.main').getBoundingClientRect();
        const r = header.getBoundingClientRect();
        return {y: Math.round(r.top + 8), inside: Math.round(r.left + 4),
                outside: Math.round(main.left + 4), shell: r.left - main.left};
    }""")
    from io import BytesIO

    from PIL import Image

    picture = Image.open(BytesIO(tab.screenshot())).convert("RGB")
    tab.context.close()
    assert band["shell"] > 100   # the reading width leaves the column's sides
    outside = picture.getpixel((band["outside"], band["y"]))
    inside = picture.getpixel((band["inside"], band["y"]))
    # One colour, give or take the rounding of a loaded machine's render;
    # the column's own ground beside the old band was 10 or more away.
    assert max(abs(a - b) for a, b in zip(outside, inside)) <= 3, (outside, inside)


def test_the_viewer_closes_both_columns_and_gives_them_back(browser, session) -> None:
    tab = _open(browser, session)
    assert _closed(tab) == [False, False]
    _go(tab, "viewer")
    on_the_viewer = _closed(tab)
    _go(tab, "analysis")
    after = _closed(tab)
    tab.context.close()
    assert on_the_viewer == [True, True]
    assert after == [False, False]


def test_a_closed_column_is_shown_while_its_button_is_pointed_at(browser, session) -> None:
    tab = _open(browser, session, page="viewer")
    for button, column, which in (("#sidebar-expand", ".sidebar", "sidebar"),
                                  ("#side-expand", "#side-panel", "panel")):
        # The column shown covers the button, so the pointer is moved, not
        # a hover that waits for the button to take it.
        spot = tab.locator(button).bounding_box()
        tab.mouse.move(spot["x"] + spot["width"] / 2, spot["y"] + spot["height"] / 2)
        tab.wait_for_timeout(100)
        shown = tab.evaluate(f"""() => [document.body.classList.contains('{which}-peek'),
            document.querySelector('{column}').getBoundingClientRect().width]""")
        # Onto the column itself, and it stays.
        box = tab.locator(column).bounding_box()
        tab.mouse.move(box["x"] + box["width"] / 2, box["y"] + 200)
        tab.wait_for_timeout(500)
        stays = tab.evaluate(f"() => document.body.classList.contains('{which}-peek')")
        # Away, and it goes; the column is still closed.
        tab.mouse.move(720, 450)
        tab.wait_for_timeout(500)
        gone = tab.evaluate(f"() => document.body.classList.contains('{which}-peek')")
        assert shown[0] and shown[1] > 150, which
        assert stays and not gone, which
        assert _closed(tab) == [True, True]
    tab.context.close()


def test_a_click_keeps_a_column_on_the_viewer(browser, session) -> None:
    tab = _open(browser, session, page="viewer")
    # Pointed at, the column shows and the button moves to its edge, where
    # it is clicked.
    spot = tab.locator("#sidebar-expand").bounding_box()
    tab.mouse.move(spot["x"] + 4, spot["y"] + spot["height"] / 2)
    tab.wait_for_timeout(100)
    title = tab.get_attribute("#sidebar-expand", "title")
    tab.click("#sidebar-expand")
    tab.mouse.move(720, 450)
    tab.wait_for_timeout(400)
    kept = _closed(tab)
    _go(tab, "overview")
    _go(tab, "viewer")
    again = _closed(tab)
    tab.reload()
    tab.wait_for_function("() => window.FastMDXDashboard && window.FastMDXDashboard.navigate")
    _go(tab, "viewer")
    reloaded = _closed(tab)
    tab.click("#sidebar-collapse")
    _go(tab, "overview")
    elsewhere = _closed(tab)
    tab.context.close()
    assert title == "Keep the sidebar open"
    assert kept == again == reloaded == [False, True]
    # Closed on the Viewer, it is closed there only.
    assert elsewhere == [False, False]


@pytest.mark.parametrize("width,height", [(1440, 900), (1100, 760), (1920, 1080)])
def test_the_molecule_keeps_its_shape_in_view(browser, session, width, height) -> None:
    tab = _open(browser, session, page="viewer", width=width, height=height)
    found = _settled(tab)
    tab.context.close()
    assert abs(found["width"] / found["height"] - 4 / 3) < 0.01
    assert found["bottom"] <= found["inner"]
    # Centred, with the header over it.
    assert abs(found["left"] - found["right"]) <= 2
    assert abs(found["headerLeft"] - found["layoutLeft"]) <= 1


def test_the_sequence_and_the_playback_fold_and_stay_folded(browser, session) -> None:
    tab = _open(browser, session, page="viewer")
    before = _settled(tab)
    # The sequence starts closed, the playback open.
    started = tab.evaluate("""() => [document.getElementById('sequence-strip').open,
        document.getElementById('viewer-under-fold').open]""")
    tab.click("#sequence-strip > summary")
    tab.wait_for_timeout(800)
    opened = tab.evaluate("""() => [document.getElementById('sequence-strip').open,
        document.getElementById('viewer-canvas-frame').getBoundingClientRect().height]""")
    tab.click("#sequence-strip > summary")
    tab.click("#viewer-under-fold > summary")
    tab.wait_for_timeout(800)
    folded = tab.evaluate("""() => [document.getElementById('sequence-strip').open,
        document.getElementById('viewer-under-fold').open,
        document.getElementById('viewer-canvas-frame').getBoundingClientRect().height]""")
    tab.click("#sequence-strip > summary")
    tab.wait_for_timeout(300)
    tab.reload()
    tab.wait_for_function("() => window.FastMDXDashboard && window.FastMDXDashboard.navigate")
    _go(tab, "viewer")
    kept = tab.evaluate("""() => [document.getElementById('sequence-strip').open,
        document.getElementById('viewer-under-fold').open]""")
    tab.context.close()
    assert started == [False, True]
    assert opened[0] and opened[1] < before["height"] - 50
    assert folded[:2] == [False, False]
    # The room they gave back goes to the molecule.
    assert folded[2] > before["height"] + 50
    # As each was left: the sequence opened again, the playback closed.
    assert kept == [True, False]


def test_the_sequence_folds_with_the_sections_own_marker(browser, session) -> None:
    tab = _open(browser, session, page="viewer")
    _settled(tab)
    found = tab.evaluate("""() => {
        const look = (el) => { const s = getComputedStyle(el);
            return [s.display, s.listStylePosition, s.fontSize]; };
        return [look(document.querySelector('#sequence-strip > summary')),
                look(document.querySelector('.viewer-side > .side-section > summary'))];
    }""")
    tab.context.close()
    assert found[0] == found[1] == ["list-item", "inside", found[1][2]]
