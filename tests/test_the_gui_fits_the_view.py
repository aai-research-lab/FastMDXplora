"""The GUI fits the view it is in.

User, 10-15: "the fold sidebar button should align with the rest of buttons
below it; enter/send button accent should not come up until the user start
typing in the composer; ... when the middle panel is current active on pages
that have their own right panel, and agent is beside the sidebar should
automatically fold ... the right panel of those pages should not wrap below
unless its a small screen? All in all the Whole GUI that the user is seeing
should be responsive to the current view and adjust unless user insist
otherwise?"

Each page says the least width it needs (`--page-least`: 788px for the
Viewer, 784px for the Config Builder, 480px for the others); frame.js folds
the sidebar to its strip of icons, then the side panel, before a page has
less, and the Viewer's tools and the builder's summary go under the page
only below their least. The Agent beside a page with its own right panel
folds the sidebar whatever the room. A column the person opens, closes or
widens is theirs until the page changes.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "src" / "fastmdxplora" / "gui" / "static"
CSS = (STATIC / "dashboard.css").read_text(encoding="utf-8")
LEAST = {"viewer": 788, "run": 784}
PAGES = ("overview", "viewer", "analysis", "run", "report", "files", "studies")


def test_each_page_wraps_its_right_panel_at_the_least_it_names() -> None:
    """The width frame.js keeps a page at, and the width the page's right
    panel goes under it below, are one number."""
    for page, least in LEAST.items():
        assert re.search(rf'\.page\[data-page="{page}"\] \{{ --page-least: {least}px; '
                         r"--own-right-panel: 1; \}", CSS), page
    assert ".page { --page-least: 480px; --own-right-panel: 0; }" in CSS
    # Written as a range, so no fraction of a pixel falls between the two
    # layouts (a page of 787.28px, the Agent's width being a share of the
    # window).
    viewer = re.findall(r"@container viewerpage \(width ([<>]=?) (\d+)px\)", CSS)
    assert len(viewer) == 4 and set(viewer) == {("<", "788"), (">=", "788")}, viewer
    assert re.findall(r"@container builder \((.*?)\)", CSS) == ["width < 784px"]
    assert not re.search(r"@container [a-z]+ \((max|min)-width", CSS)


@pytest.fixture(scope="module")
def playwright_here():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        yield pw


@pytest.fixture(scope="module")
def browser(playwright_here):
    launched = playwright_here.chromium.launch(args=["--enable-unsafe-swiftshader"])
    yield launched
    launched.close()


@pytest.fixture(scope="module")
def browser_with_scroll_bars(playwright_here):
    """Scroll bars that take room, as on Windows and Linux."""
    launched = playwright_here.chromium.launch(args=["--enable-unsafe-swiftshader"],
                                               ignore_default_args=["--hide-scrollbars"])
    yield launched
    launched.close()


@pytest.fixture(scope="module")
def session(tmp_path_factory):
    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_the_workspace_says_its_studies import _study

    root = tmp_path_factory.mktemp("fits")
    study = _study(root / "workspace" / "ubiquitin", system="1UBQ",
                   started="2026-09-01T10:00:00+00:00")
    started = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    yield started
    started.server.shutdown()


def _page(browser, session, width=1440, scheme=None, page="overview", stored=None):
    context = browser.new_context(viewport={"width": width, "height": 900})
    if scheme:
        context.add_init_script(f"localStorage.setItem('fmx.theme', '{scheme}');")
    for key, value in (stored or {}).items():
        context.add_init_script(f"localStorage.setItem('fmx.{key}', '{value}');")
    tab = context.new_page()
    tab.set_default_timeout(60000)
    tab.goto(session.url + "#" + page, wait_until="domcontentloaded")
    tab.wait_for_function("() => window.FastMDXFrame && window.FastMDXAgentBeside"
                          " && document.documentElement.dataset.page === '" + page + "'")
    # The loading screen lies over the page until the study is read: a seam
    # pressed under it was not pressed, and the drag widened nothing.
    tab.wait_for_function("() => document.body.classList.contains('state-ready')")
    if not set(stored or ()) & {"panelCollapsed", "builderPanelOpen", "viewerPanelOpen"}:
        # The log is open until the study's first status says it finished:
        # the page as it stands once it has.
        tab.wait_for_function("() => document.body.classList.contains('panel-collapsed')")
    tab.wait_for_timeout(300)
    return tab


def _go(tab, page):
    tab.evaluate(f"() => window.FastMDXDashboard.navigate('{page}')")
    tab.wait_for_function(f"() => document.documentElement.dataset.page === '{page}'")
    tab.wait_for_timeout(150)


def _folded(tab):
    return tab.evaluate("() => document.body.classList.contains('sidebar-collapsed')")


@pytest.mark.parametrize("scheme", ["dark", "light"])
def test_the_fold_button_lines_up_with_the_buttons_under_it(browser, session, scheme) -> None:
    tab = _page(browser, session, scheme=scheme)
    shown = tab.evaluate("""() => {
        const box = s => { const b = document.querySelector(s).getBoundingClientRect();
                           return [b.left + b.width / 2, b.right, b.width, b.height]; };
        return {fold: box('#sidebar-collapse'), foldIcon: box('#sidebar-collapse svg'),
                beside: box('#agent-beside-open'), besideIcon: box('#agent-beside-open svg')}; }""")
    tab.click("#sidebar-collapse")
    tab.mouse.move(700, 500)
    tab.wait_for_timeout(300)
    strip = tab.evaluate("""() => {
        const middle = e => { const b = e.getBoundingClientRect(); return b.left + b.width / 2; };
        return [middle(document.querySelector('#sidebar-expand .strip-mark')
                       || document.querySelector('#sidebar-expand')),
                [...document.querySelectorAll('.sidebar .nav-link svg')]
                  .filter(e => e.getBoundingClientRect().width).map(middle)]; }""")
    tab.context.close()
    # Unfolded: the same right edge, size and icon column as the Agent's
    # button beside it in the row under it.
    assert shown["fold"][1:] == shown["beside"][1:], shown
    assert abs(shown["foldIcon"][0] - shown["besideIcon"][0]) < 0.5, shown
    # Folded: the mark that unfolds it over the strip's icons.
    assert strip[1] and all(abs(x - strip[0]) < 1 for x in strip[1]), strip


def test_send_waits_for_something_to_send(browser, session) -> None:
    tab = _page(browser, session, page="agent")
    said = []

    def look():
        said.append(tab.evaluate("""() => { const b = document.getElementById('agent-propose');
            const s = getComputedStyle(b);
            const accent = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim();
            const probe = document.createElement('i'); probe.style.color = accent;
            document.body.appendChild(probe); const fill = getComputedStyle(probe).color; probe.remove();
            return [b.classList.contains('is-blank'), s.backgroundColor === fill,
                    b.getAttribute('aria-disabled'), b.getAttribute('aria-label'), b.tabIndex >= 0,
                    b.title]; }"""))

    look()
    tab.focus("#agent-request")
    tab.keyboard.type("h")
    look()
    tab.keyboard.press("Backspace")
    look()
    tab.keyboard.type("   ")
    look()
    # Put in the box by the page, not typed: Send readies all the same.
    tab.evaluate("() => { document.getElementById('agent-request').value = 'What ran?'; }")
    look()
    tab.evaluate("() => { document.getElementById('agent-request').value = ''; }")
    look()
    # Clicked with nothing written, it sends nothing.
    tab.click("#agent-propose", force=True)
    tab.wait_for_timeout(300)
    sent = tab.evaluate("() => document.querySelectorAll('#agent-thread .agent-msg-user').length")
    tab.context.close()
    # Quiet, it says why it waits (CM, 10-16: the title was not asserted).
    quiet = [True, False, "true", "Send", True, "Write a message to send it"]
    ready = [False, True, "false", "Send", True, "Send (Enter). Shift+Enter for a new line."]
    assert said == [quiet, ready, quiet, quiet, ready, quiet]
    assert sent == 0


def test_the_agent_beside_folds_the_sidebar_beside_a_page_with_its_own_right_panel(
        browser, session) -> None:
    tab = _page(browser, session, width=1920, page="run")
    seen = {"before": _folded(tab)}
    tab.evaluate("() => window.FastMDXAgentBeside.open(true)")
    seen["opened"] = _folded(tab)
    # Opened again by the person while the Agent is beside: theirs.
    tab.click("#sidebar-expand")
    tab.wait_for_timeout(100)
    seen["reopened"] = _folded(tab)
    tab.evaluate("() => window.dispatchEvent(new Event('resize'))")
    tab.wait_for_timeout(100)
    seen["kept"] = _folded(tab)
    tab.evaluate("() => window.FastMDXAgentBeside.close()")
    seen["closed"] = _folded(tab)
    tab.evaluate("() => window.FastMDXAgentBeside.open(true)")
    seen["opened again"] = _folded(tab)
    # A page with no right panel of its own: the sidebar as it was.
    _go(tab, "overview")
    seen["overview"] = _folded(tab)
    _go(tab, "run")
    seen["back"] = _folded(tab)
    tab.evaluate("() => window.FastMDXAgentBeside.close()")
    seen["closed again"] = _folded(tab)
    tab.context.close()
    assert seen == {"before": False, "opened": True, "reopened": False, "kept": False,
                    "closed": False, "opened again": True, "overview": False, "back": True,
                    "closed again": False}


def test_the_agent_folds_the_sidebar_kept_open_on_the_viewer(browser, session) -> None:
    tab = _page(browser, session, width=1920, page="viewer",
                stored={"viewerSidebarOpen": "1"})
    seen = [_folded(tab)]
    tab.evaluate("() => window.FastMDXAgentBeside.open(true)")
    seen.append(_folded(tab))
    tab.evaluate("() => window.FastMDXAgentBeside.close()")
    seen.append(_folded(tab))
    tab.context.close()
    assert seen == [False, True, False]


def test_the_viewer_s_sidebar_reopened_beside_the_agent_is_not_kept(browser, session) -> None:
    """Reopened beside the Agent on the Viewer, the sidebar is open until the
    Agent closes, and not kept open there for the next visit."""
    tab = _page(browser, session, width=1440, page="viewer")
    tab.evaluate("() => window.FastMDXAgentBeside.open(true)")
    tab.click("#sidebar-expand")
    tab.wait_for_timeout(100)
    seen = [_folded(tab)]
    tab.evaluate("() => window.FastMDXAgentBeside.close()")
    seen.append(_folded(tab))
    seen.append(tab.evaluate("() => localStorage.getItem('fmx.viewerSidebarOpen')"))
    tab.context.close()
    assert seen == [False, True, None]


@pytest.mark.parametrize("page", sorted(LEAST))
def test_a_right_panel_stays_beside_down_to_its_page_s_least(browser, session, page) -> None:
    """Both sides of the width named: the page held at its least keeps its
    right panel beside it, a pixel less puts it under."""
    tab = _page(browser, session, width=1920, page=page)
    main, side = {"viewer": (".viewer-canvas-wrap", ".viewer-side"),
                  "run": (".builder-main", ".builder-summary")}[page]
    where = []
    for room in (LEAST[page], LEAST[page] - 1):
        where.append(tab.evaluate(f"""() => {{
            const page = document.querySelector('.page[data-page="{page}"]');
            const s = getComputedStyle(page);
            page.style.boxSizing = 'content-box';
            page.style.width = '{room}px';
            page.style.maxWidth = 'none';
            const a = page.querySelector('{main}').getBoundingClientRect();
            const b = page.querySelector('{side}').getBoundingClientRect();
            const said = [page.clientWidth - parseFloat(s.paddingLeft) - parseFloat(s.paddingRight),
                          b.top >= a.bottom - 2 ? 'under' : 'beside'];
            page.style.width = ''; page.style.maxWidth = ''; page.style.boxSizing = '';
            return said; }}"""))
    tab.context.close()
    assert where == [[LEAST[page], "beside"], [LEAST[page] - 1, "under"]]


def test_the_sidebar_then_the_side_panel_fold_before_the_summary_goes_under(
        browser, session) -> None:
    """The Config Builder with the side panel kept open there, in a window
    too narrow for the three: the sidebar folds first, the panel next, and
    the summary stays beside the form."""
    seen = []
    for width in (1440, 1280):
        tab = _page(browser, session, width=width, page="run",
                    stored={"builderPanelOpen": "1"})
        seen.append(tab.evaluate("""() => {
            const page = document.querySelector('.page[data-page="run"]');
            const a = page.querySelector('.builder-main').getBoundingClientRect();
            const b = page.querySelector('.builder-summary').getBoundingClientRect();
            return [document.body.classList.contains('sidebar-collapsed'),
                    document.body.classList.contains('panel-collapsed'),
                    b.top >= a.bottom - 2 ? 'under' : 'beside']; }"""))
        tab.context.close()
    # At 1440 the sidebar's fold is enough; at 1280 it is not, and the side
    # panel folds, which alone leaves the page its least: the sidebar back.
    assert seen == [[True, False, "beside"], [False, True, "beside"]]


def test_a_column_opened_or_closed_by_hand_refits_the_page(browser, session) -> None:
    """The Config Builder at 1440 with the sidebar open: the side panel
    opened by hand folds the sidebar so the summary stays beside the form;
    closed again, the sidebar comes back."""
    tab = _page(browser, session, width=1440, page="run")

    def shown():
        return tab.evaluate("""() => {
            const page = document.querySelector('.page[data-page="run"]');
            const a = page.querySelector('.builder-main').getBoundingClientRect();
            const b = page.querySelector('.builder-summary').getBoundingClientRect();
            return [document.body.classList.contains('sidebar-collapsed'),
                    document.body.classList.contains('panel-collapsed'),
                    b.top >= a.bottom - 2 ? 'under' : 'beside']; }""")

    seen = [shown()]
    tab.click("#side-expand")
    tab.wait_for_timeout(100)
    seen.append(shown())
    tab.click("#side-collapse")
    tab.wait_for_timeout(100)
    seen.append(shown())
    tab.context.close()
    assert seen == [[False, True, "beside"], [True, False, "beside"], [False, True, "beside"]]


def test_the_side_panel_opened_or_closed_refits_the_page(browser, session) -> None:
    """The Overview at 1024: the log opened (by hand, or as a run starts)
    folds the sidebar to keep the page at its least; closed, the sidebar
    comes back."""
    tab = _page(browser, session, width=1024)
    seen = [_folded(tab)]
    tab.evaluate("() => window.FastMDXFrame.setCollapsed(false, false)")
    seen.append(tab.evaluate(GEOMETRY)["room"] >= 480 and _folded(tab))
    tab.evaluate("() => window.FastMDXFrame.setCollapsed(true, false)")
    seen.append(_folded(tab))
    tab.context.close()
    assert seen == [False, True, False]


PANEL_JS = (STATIC / "agent-panel.js").read_text(encoding="utf-8")
BESIDE_WORDS = re.findall(r'^    \w+: \["[^"]*", "([^"]*)"\],$', PANEL_JS, re.M)


def test_every_prompt_beside_a_page_fits_its_box_at_its_least(browser, session) -> None:
    """Each wording the Agent's box can ask in, beside a page, on one line
    of the box at the column's least (340px, a 1024px window)."""
    assert len(BESIDE_WORDS) == 9, BESIDE_WORDS
    tab = _page(browser, session, width=1024)
    tab.evaluate("() => window.FastMDXAgentBeside.open(true)")
    tab.wait_for_timeout(150)
    clipped = tab.evaluate("""(words) => { const a = document.getElementById('agent-request');
        const was = a.placeholder;
        const out = words.filter(w => { a.placeholder = w; a.style.height = '';
                                          return a.scrollHeight > a.clientHeight + 1; });
        a.placeholder = was; return out; }""", BESIDE_WORDS)
    tab.context.close()
    assert clipped == []


def test_the_prompt_beside_a_page_follows_the_study_and_fits(browser, session) -> None:
    """Opened beside the page, then the study starts running: the box asks
    in the column's words, and they fit."""
    tab = _page(browser, session, width=1024)
    tab.evaluate("() => window.FastMDXAgentBeside.open(true)")
    tab.evaluate("""() => window.FastMDXDashboard.applyAppState(
        {status: 'running', process_running: true})""")
    tab.wait_for_timeout(300)
    beside = tab.evaluate("""() => { const a = document.getElementById('agent-request');
        return [a.placeholder, a.scrollHeight <= a.clientHeight + 1]; }""")
    tab.evaluate("() => window.FastMDXAgentBeside.close()")
    page = tab.evaluate("() => document.getElementById('agent-request').placeholder")
    tab.context.close()
    assert beside == ["Ask how it is going", True]
    # Back on its own page, the box asks in the page's longer words.
    assert page == "Ask how it is going, or tell me to stop it"


def test_a_window_resized_refits_the_page(browser, session) -> None:
    tab = _page(browser, session, width=1440, page="run")
    seen = [_folded(tab)]
    tab.set_viewport_size({"width": 1024, "height": 900})
    tab.wait_for_timeout(300)
    seen.append(_folded(tab))
    tab.set_viewport_size({"width": 1440, "height": 900})
    tab.wait_for_timeout(300)
    seen.append(_folded(tab))
    tab.context.close()
    assert seen == [False, True, False]


def test_a_seam_put_back_by_a_double_click_refits_the_page(browser, session) -> None:
    tab = _page(browser, session, width=1280, stored={"panelCollapsed": "0"})
    handle = tab.evaluate("""() => { const b = document.querySelector('.col-handle[data-handle="panel"]')
        .getBoundingClientRect(); return [b.left + b.width / 2, b.top + 200]; }""")
    tab.mouse.move(*handle)
    tab.mouse.down()
    tab.mouse.move(handle[0] - 200, handle[1], steps=8)
    tab.mouse.up()
    tab.wait_for_timeout(150)
    widened = _folded(tab)
    seam = tab.evaluate("""() => { const b = document.querySelector('.col-handle[data-handle="panel"]')
        .getBoundingClientRect(); return [b.left + b.width / 2, b.top + 200]; }""")
    tab.mouse.dblclick(*seam)
    tab.wait_for_timeout(150)
    put_back = _folded(tab)
    # Its width put back, the log is no longer held: folded to fit as the
    # window narrows (CI, 10-16: no test narrowed the window after it).
    tab.set_viewport_size({"width": 900, "height": 900})
    tab.wait_for_timeout(300)
    _two_frames(tab)
    narrowed = tab.evaluate("() => document.body.classList.contains('panel-collapsed')")
    tab.context.close()
    assert [widened, put_back, narrowed] == [True, False, True]


def test_a_column_opened_by_hand_stays_where_the_page_then_has_less(browser, session) -> None:
    """The person's choice holds over the fit: the log opened by hand at
    1440 stays open as the window narrows to 900, the sidebar folded, though
    the Overview then has less than its 480px."""
    tab = _page(browser, session, width=1440)
    tab.click("#side-expand")
    tab.wait_for_function("() => !document.body.classList.contains('panel-collapsed')")
    tab.set_viewport_size({"width": 900, "height": 900})
    tab.wait_for_function("() => document.body.classList.contains('sidebar-collapsed')")
    tab.wait_for_timeout(300)
    seen = tab.evaluate("""() => { const p = document.querySelector('.page[data-page="overview"]');
        const s = getComputedStyle(p);
        return [document.body.classList.contains('panel-collapsed'),
                document.body.classList.contains('sidebar-collapsed'),
                p.clientWidth - parseFloat(s.paddingLeft) - parseFloat(s.paddingRight) < 480]; }""")
    tab.context.close()
    assert seen == [False, True, True], seen


def test_the_agent_gives_back_the_sidebar_as_the_person_left_it(browser, session) -> None:
    """At 1024 the Config Builder's sidebar is folded to fit; opened by the
    person, the Agent folds it and gives it back open. On the Overview, a
    page with no right panel of its own, the Agent does not fold a sidebar
    the person opened. Closed by the person, reopened beside the Agent, it
    is closed again as the Agent goes."""
    seen = {}
    tab = _page(browser, session, width=1024, page="run")
    tab.click("#sidebar-expand")
    tab.wait_for_timeout(100)
    seen["builder, opened"] = _folded(tab)
    tab.evaluate("() => window.FastMDXAgentBeside.open(true)")
    seen["builder, Agent"] = _folded(tab)
    tab.evaluate("() => window.FastMDXAgentBeside.close()")
    seen["builder, Agent closed"] = _folded(tab)
    _go(tab, "overview")
    tab.evaluate("() => window.FastMDXFrame.setCollapsed(false, false)")
    seen["overview, log open"] = _folded(tab)
    tab.click("#sidebar-expand")
    tab.wait_for_timeout(100)
    tab.evaluate("() => window.FastMDXAgentBeside.open(true)")
    seen["overview, Agent"] = _folded(tab)
    tab.evaluate("() => window.FastMDXAgentBeside.close()")
    seen["overview, Agent closed"] = _folded(tab)
    tab.context.close()
    tab = _page(browser, session, width=1920, page="run")
    tab.click("#sidebar-collapse")
    tab.mouse.move(700, 500)
    tab.evaluate("() => window.FastMDXAgentBeside.open(true)")
    tab.click("#sidebar-expand")
    tab.wait_for_timeout(100)
    seen["closed, reopened beside"] = _folded(tab)
    tab.evaluate("() => window.FastMDXAgentBeside.close()")
    seen["closed, Agent closed"] = _folded(tab)
    tab.context.close()
    assert seen == {"builder, opened": False, "builder, Agent": True,
                    "builder, Agent closed": False, "overview, log open": True,
                    "overview, Agent": False, "overview, Agent closed": False,
                    "closed, reopened beside": False, "closed, Agent closed": True}


def test_a_seam_dragged_refits_the_page(browser, session) -> None:
    """The Overview at 1280 with the log open: its seam dragged 200px wider
    leaves the page under its least, and the sidebar folds as it is let go."""
    tab = _page(browser, session, width=1280, stored={"panelCollapsed": "0"})
    before = [_folded(tab), tab.evaluate(GEOMETRY)["room"]]
    handle = tab.evaluate("""() => { const b = document.querySelector('.col-handle[data-handle="panel"]')
        .getBoundingClientRect(); return [b.left + b.width / 2, b.top + 200]; }""")
    tab.mouse.move(*handle)
    tab.mouse.down()
    tab.mouse.move(handle[0] - 200, handle[1], steps=8)
    tab.mouse.up()
    tab.wait_for_timeout(150)
    after = [_folded(tab), tab.evaluate(GEOMETRY)["room"]]
    tab.context.close()
    assert before[0] is False and before[1] >= 480, before
    assert after[0] is True and after[1] >= 480, after


def test_a_column_dragged_wider_holds_until_the_page_changes(browser, session) -> None:
    """The log's seam dragged 150px wider at 1280: as the window narrows to
    1000 the log keeps its width and the sidebar folds, though the page then
    has less than its least; on another page it is fitted again (CM, 10-16:
    no test narrowed the window after a drag)."""
    tab = _page(browser, session, width=1280, stored={"panelCollapsed": "0"})
    handle = tab.evaluate("""() => { const b = document.querySelector('.col-handle[data-handle="panel"]')
        .getBoundingClientRect(); return [b.left + b.width / 2, b.top + 200]; }""")
    tab.mouse.move(*handle)
    tab.mouse.down()
    tab.mouse.move(handle[0] - 150, handle[1], steps=8)
    tab.mouse.up()
    tab.wait_for_timeout(150)
    width = "() => Math.round(document.querySelector('.side-panel').getBoundingClientRect().width)"
    dragged = tab.evaluate(width)
    tab.set_viewport_size({"width": 1000, "height": 900})
    tab.wait_for_timeout(300)
    _two_frames(tab)
    narrowed = [tab.evaluate("() => document.body.classList.contains('panel-collapsed')"),
                _folded(tab), tab.evaluate(width), tab.evaluate(GEOMETRY)["room"] < 480]
    _go(tab, "analysis")
    _two_frames(tab)
    elsewhere = [tab.evaluate("() => document.body.classList.contains('panel-collapsed')"),
                 tab.evaluate(GEOMETRY)["room"] >= 480]
    tab.context.close()
    assert dragged >= 560, dragged
    assert narrowed == [False, True, dragged, True], (dragged, narrowed)
    assert elsewhere == [True, True], elsewhere


def test_a_long_path_breaks_in_the_form_and_the_summary(browser, session) -> None:
    """A structure path longer than its column, at 1440 (the summary beside)
    and 912 (the form at its least): what the system's file holds, what
    setup will build and the plan break it, nothing past its card or the
    page (CN, 10-16: it ran under the row's button, into the summary, and
    at 900 scrolled the page sideways)."""
    long = "/a/rather/long/path/to/a/structure/file/named/receptor_ligand_complex_prepared.pdb"
    found = {}
    for width in (1440, 912):
        tab = _page(browser, session, width=width, page="run")
        tab.click('#run-start .builder-start-card[data-start="structure"]')
        tab.click("#run-add-system")
        tab.fill('[aria-label="Structure of system 2"]', long)
        tab.keyboard.press("Tab")
        tab.wait_for_function("() => [...document.querySelectorAll('.builder-system-holds')]"
                              ".some(e => e.textContent.includes('receptor_ligand'))"
                              " && !document.getElementById('run-system-preview').hidden"
                              " && [...document.querySelectorAll('.builder-plan-lines dd')]"
                              ".some(e => e.textContent.includes('receptor_ligand'))")
        tab.wait_for_timeout(200)
        found[width] = tab.evaluate("""() => {
            const wider = e => e.scrollWidth > e.clientWidth + 1
                && !['INPUT', 'TEXTAREA', 'SELECT'].includes(e.tagName);
            const holds = [...document.querySelectorAll('.builder-system-holds')]
                .filter(e => e.textContent.includes('receptor_ligand'))
                .map(e => [wider(e), e.getBoundingClientRect().right
                           <= e.closest('.builder-system-row').getBoundingClientRect().right + 1]);
            const preview = document.getElementById('run-system-preview');
            const plan = [...document.querySelectorAll('.builder-plan-lines dd')];
            const main = document.querySelector('.page[data-page="run"] .builder-main')
                .getBoundingClientRect();
            const page = document.querySelector('.page[data-page="run"]');
            return {holds,
                    preview: preview.hidden ? null : [...preview.querySelectorAll('*')].some(wider),
                    plan: plan.filter(e => e.textContent.includes('receptor_ligand'))
                        .map(e => !wider(e) && e.getBoundingClientRect().right <= main.right + 1),
                    page: page.scrollWidth <= page.clientWidth + 1}; }""")
        tab.context.close()
    for width, seen in found.items():
        assert seen["holds"] and all(h == [False, True] for h in seen["holds"]), (width, seen)
        # The preview and the plan shown and checked, not passed over empty
        # (CO, 10-16: the plan's box held, its text ran past).
        assert seen["preview"] is False and seen["plan"] and all(seen["plan"]), (width, seen)
        assert seen["page"], (width, seen)


def test_a_long_path_breaks_in_a_chip_and_a_config_s_verdict(browser, session) -> None:
    """A setting for one system whose value is a long path, in its chip, and
    a config file not found under a long path, in the verdict under it: each
    broken in its place at 912, nothing past the form or the page (CO and
    CP, 10-16: the chip ran under the summary, the verdict across it)."""
    forcefield = "/a/rather/long/path/to/a/force/field/named/custom_forcefield_parameters_for_ligand.xml"
    config = "/a/rather/long/path/to/a/folder/of/studies/named/fastmdxplora_receptor_ligand.yml"
    state = {"start": "structure", "include_phase": ["setup", "simulation"],
             "systems": [{"id": "s1", "system": "1UBQ", "setup": {"forcefield": forcefield}}],
             "phases": {}}
    tab = _page(browser, session, width=912, page="run")
    tab.evaluate("(s) => window.FastMDXRun.applyLoadedState(s, {})", state)
    tab.wait_for_function("() => [...document.querySelectorAll('.builder-chip')]"
                          ".some(e => e.textContent.includes('custom_forcefield'))")
    check = """(said) => {
        const main = document.querySelector('.page[data-page="run"] .builder-main')
            .getBoundingClientRect();
        const page = document.querySelector('.page[data-page="run"]');
        const e = [...document.querySelectorAll('.page[data-page="run"] .builder-main *')]
            .filter(x => x.children.length === 0 && x.textContent.includes(said))[0];
        return [!!e, e && e.scrollWidth <= e.clientWidth + 1,
                e && e.getBoundingClientRect().right <= main.right + 1,
                page.scrollWidth <= page.clientWidth + 1]; }"""
    chip = tab.evaluate(check, "custom_forcefield")
    tab.click('#run-start .builder-start-card[data-start="config"]')
    tab.fill("#run-config-path", config)
    tab.click("#run-check-config")
    tab.wait_for_function("() => document.getElementById('run-config-verdict')"
                          ".textContent.includes('fastmdxplora_receptor_ligand')")
    verdict = tab.evaluate(check, "fastmdxplora_receptor_ligand")
    tab.context.close()
    assert chip == [True, True, True, True], chip
    assert verdict == [True, True, True, True], verdict


def test_a_chip_s_corners_keep_inside_its_padding(browser, session) -> None:
    """A chip of a setting for one system: a pill on one line, and over
    several lines (a long path broken) corners within its padding, so its
    words do not cross its border (CR, 10-16: a pill four lines high)."""
    long = "/a/rather/long/path/to/a/force/field/named/custom_forcefield_parameters_for_ligand.xml"
    state = {"start": "structure", "include_phase": ["setup", "simulation"],
             "systems": [{"id": "s1", "system": "1UBQ", "setup": {"forcefield": long}},
                         {"id": "s2", "system": "1UBQ", "setup": {"forcefield": "amber14"}}],
             "phases": {}}
    tab = _page(browser, session, width=912, page="run")
    tab.evaluate("(s) => window.FastMDXRun.applyLoadedState(s, {})", state)
    tab.wait_for_function("() => document.querySelectorAll('.builder-system-own .builder-chip').length === 2")
    chips = tab.evaluate("""() => [...document.querySelectorAll('.builder-system-own .builder-chip')]
        .map(c => { const s = getComputedStyle(c);
                    return [c.getBoundingClientRect().height, parseFloat(s.borderTopLeftRadius),
                            parseFloat(s.paddingLeft)]; })""")
    tab.context.close()
    (high, radius, pad), (one, one_radius, _) = chips
    assert high > 2 * one and radius * (1 - 0.5 ** 0.5) <= pad, chips
    assert one_radius >= one / 2 - 0.5, chips


@pytest.mark.parametrize("width", [390, 912])
def test_a_long_force_field_path_breaks_in_what_setup_will_build(browser, session,
                                                                width) -> None:
    """A force field set for one system by a long path, in the rows of what
    setup will build: broken in its row, nothing past the card or the page
    (CT, 10-16: the rows' second column 755px in a 376px card, the Solute
    and Padding rows cut, and at 390 the page scrolled 413px sideways)."""
    long = "/a/rather/long/path/to/a/force/field/named/custom_forcefield_parameters_for_ligand.xml"
    tab = _page(browser, session, width=width, page="run")
    tab.click('#run-start .builder-start-card[data-start="structure"]')
    seen = tab.evaluate("""(long) => {
        const card = document.getElementById('run-system-preview');
        card.hidden = false;
        const body = document.getElementById('run-system-preview-body');
        body.textContent = '';
        const rows = document.createElement('dl');
        rows.className = 'system-preview-rows';
        for (const [label, value] of [['Solute', '76 residues in chain A'],
                                      ['Force field', long],
                                      ['Padding', '1.0 nm between the solute and its nearest periodic image']]) {
            const dt = document.createElement('dt'); dt.textContent = label;
            const dd = document.createElement('dd'); dd.textContent = value;
            rows.append(dt, dd);
        }
        body.appendChild(rows);
        const box = card.getBoundingClientRect();
        const page = document.querySelector('.page[data-page="run"]');
        return [...rows.querySelectorAll('dd')].map(d =>
                    d.scrollWidth <= d.clientWidth + 1 && d.getBoundingClientRect().right <= box.right + 1)
               .concat([page.scrollWidth <= page.clientWidth + 1]); }""", long)
    tab.context.close()
    assert seen == [True, True, True, True], seen


@pytest.mark.parametrize("width", [390, 912])
def test_why_there_is_no_plan_yet_stays_in_its_card(browser, session, width) -> None:
    """The plan's one line saying why there is none yet (Setup not ticked)
    across both columns, wrapped in its card, the page not scrolled
    sideways (CR, 10-16: put in the first column at its whole width, 966px,
    it ran across the summary and a phone scrolled 627px)."""
    state = {"start": "structure", "include_phase": ["simulation"],
             "systems": [{"id": "s1", "system": "1UBQ"}], "phases": {}}
    tab = _page(browser, session, width=width, page="run")
    tab.evaluate("(s) => window.FastMDXRun.applyLoadedState(s, {})", state)
    tab.wait_for_function("() => (document.getElementById('run-plan').textContent || '')"
                          ".includes('needs Setup')")
    seen = tab.evaluate("""() => {
        const plan = document.getElementById('run-plan'), line = plan.querySelector('dd');
        const page = document.querySelector('.page[data-page="run"]');
        return [plan.children.length, line.getBoundingClientRect().right
                <= plan.getBoundingClientRect().right + 1, page.scrollWidth <= page.clientWidth + 1]; }""")
    tab.context.close()
    assert seen == [1, True, True], seen


def test_a_peek_left_goes_through_a_fit(browser, session) -> None:
    """The pointer leaves the sidebar shown over the page; a refit in the
    moment before it goes (the window, the Agent) does not keep it there."""
    tab = _page(browser, session, width=1024, page="run")
    tab.hover("#sidebar-expand")
    tab.mouse.move(20, 14)
    tab.wait_for_function("() => document.body.classList.contains('sidebar-peek')")
    tab.mouse.move(700, 500)
    tab.set_viewport_size({"width": 1030, "height": 900})
    tab.wait_for_timeout(800)
    gone = not tab.evaluate("() => document.body.classList.contains('sidebar-peek')")
    tab.context.close()
    assert gone


def test_the_agent_page_s_prompt_has_its_room_after_the_agent_was_beside(browser,
                                                                        session) -> None:
    """On a phone the Agent page's prompt takes two lines and the box grows
    for them; the Agent opened beside another page and closed again, the
    box is sized for them again on the Agent page."""
    tab = _page(browser, session, width=390, page="agent")
    first = tab.evaluate("""() => { const a = document.getElementById('agent-request');
        return a.scrollHeight <= a.clientHeight + 1; }""")
    _go(tab, "overview")
    tab.evaluate("() => window.FastMDXAgentBeside.open(true)")
    tab.wait_for_timeout(100)
    tab.evaluate("() => window.FastMDXAgentBeside.close()")
    _go(tab, "agent")
    tab.wait_for_timeout(200)
    again = tab.evaluate("""() => { const a = document.getElementById('agent-request');
        return a.scrollHeight <= a.clientHeight + 1; }""")
    tab.context.close()
    assert [first, again] == [True, True]


@pytest.mark.parametrize("page", ["run", "viewer"])
def test_the_sidebar_folded_again_beside_the_agent_is_the_agent_s_fold(browser, session,
                                                                      page) -> None:
    """Opened beside the Agent and folded again by the person: the Agent's
    fold, nothing stored, and the sidebar as it was once the Agent closes."""
    stored = {"viewerSidebarOpen": "1"} if page == "viewer" else None
    tab = _page(browser, session, width=1440, page=page, stored=stored)
    seen = [_folded(tab)]
    tab.evaluate("() => window.FastMDXAgentBeside.open(true)")
    tab.click("#sidebar-expand")
    tab.wait_for_timeout(100)
    tab.click("#sidebar-collapse")
    tab.mouse.move(700, 500)
    tab.wait_for_timeout(100)
    seen.append(_folded(tab))
    tab.evaluate("() => window.FastMDXAgentBeside.close()")
    seen.append(_folded(tab))
    kept = tab.evaluate("""() => [localStorage.getItem('fmx.sidebarCollapsed'),
                                  localStorage.getItem('fmx.viewerSidebarOpen')]""")
    tab.context.close()
    assert seen == [False, True, False]
    assert kept == [None, "1" if page == "viewer" else None]


def test_a_seam_double_clicked_is_not_a_width_chosen(browser, session) -> None:
    """The log's seam double-clicked at its own width: the log is still
    folded to fit as the window narrows."""
    tab = _page(browser, session, width=1100, stored={"panelCollapsed": "0"})
    seam = tab.evaluate("""() => { const b = document.querySelector('.col-handle[data-handle="panel"]')
        .getBoundingClientRect(); return [b.left + b.width / 2, b.top + 200]; }""")
    tab.mouse.dblclick(*seam)
    tab.wait_for_timeout(100)
    tab.set_viewport_size({"width": 900, "height": 900})
    tab.wait_for_timeout(300)
    room = tab.evaluate(GEOMETRY)["room"]
    tab.context.close()
    assert room >= 480


def test_a_seam_pressed_without_moving_is_not_a_width_chosen(browser, session) -> None:
    """The log's seam pressed and let go where it was: the log is still
    folded to fit as the window narrows (CH, 10-16: held as if widened at
    the version handed over)."""
    tab = _page(browser, session, width=1100, stored={"panelCollapsed": "0"})
    seam = tab.evaluate("""() => { const b = document.querySelector('.col-handle[data-handle="panel"]')
        .getBoundingClientRect(); return [b.left + b.width / 2, b.top + 200]; }""")
    tab.mouse.move(*seam)
    tab.mouse.down()
    # The hand moves, but not across: no width changes.
    tab.mouse.move(seam[0], seam[1] + 12, steps=3)
    tab.mouse.up()
    tab.wait_for_timeout(100)
    tab.set_viewport_size({"width": 900, "height": 900})
    tab.wait_for_timeout(300)
    _two_frames(tab)
    room = tab.evaluate(GEOMETRY)["room"]
    tab.context.close()
    assert room >= 480


def test_a_seam_double_clicked_keeps_a_column_opened_by_hand(browser, session) -> None:
    """At 1000 the Config Builder's sidebar is folded to fit; opened by the
    person, its seam double-clicked to put its width back, it stays open."""
    tab = _page(browser, session, width=1000, page="run")
    tab.click("#sidebar-expand")
    tab.wait_for_timeout(100)
    opened = _folded(tab)
    seam = tab.evaluate("""() => { const b = document.querySelector('.col-handle[data-handle="sidebar"]')
        .getBoundingClientRect(); return [b.left + b.width / 2, b.top + 300]; }""")
    tab.mouse.dblclick(*seam)
    tab.wait_for_timeout(150)
    after = _folded(tab)
    tab.context.close()
    assert [opened, after] == [False, False]


def test_text_written_beside_a_page_is_sized_again_on_the_agent_page(browser,
                                                                     session) -> None:
    """Three lines in the narrow column beside a page; on the Agent page,
    wider, the box is as high as the text there needs."""
    tab = _page(browser, session, width=1280)
    tab.evaluate("() => window.FastMDXAgentBeside.open()")
    tab.keyboard.type("What does the radius of gyration say about how compact the "
                      "protein stayed over the production run, and when?")
    tab.wait_for_timeout(100)
    beside = tab.evaluate("() => document.getElementById('agent-request').clientHeight")
    tab.evaluate("() => window.FastMDXAgentBeside.close()")
    _go(tab, "agent")
    _two_frames(tab)
    page = tab.evaluate(NEEDS)
    tab.context.close()
    assert page[0] < beside and abs(page[0] - page[1]) <= 2, (beside, page)


def test_text_written_on_the_agent_page_is_sized_again_beside_a_page(browser,
                                                                    session) -> None:
    """Two lines on the Agent page; beside a page, narrower, the box is as
    high as the text there needs, not cut and scrolled (CG and CH, 10-16)."""
    tab = _page(browser, session, width=1280, page="agent")
    tab.focus("#agent-request")
    tab.keyboard.type("What does the radius of gyration say about how compact the "
                      "protein stayed over the production run, and when did it "
                      "stop changing, if it did, in each of the three replicas?")
    tab.wait_for_timeout(100)
    there = tab.evaluate("() => document.getElementById('agent-request').clientHeight")
    _go(tab, "overview")
    tab.evaluate("() => window.FastMDXAgentBeside.open()")
    _two_frames(tab)
    beside = tab.evaluate(NEEDS)
    tab.context.close()
    assert beside[0] > there and abs(beside[0] - beside[1]) <= 2, (there, beside)


NEEDS = """() => { const a = document.getElementById('agent-request');
    const was = a.style.height; a.style.height = 'auto';
    const needs = a.scrollHeight; a.style.height = was;
    return [a.clientHeight, needs]; }"""


def _two_frames(tab):
    tab.evaluate("() => new Promise(done => requestAnimationFrame("
                 "() => requestAnimationFrame(() => done())))")
    tab.wait_for_timeout(50)


def test_a_peek_kept_by_its_button_is_the_column_opened(browser, session) -> None:
    tab = _page(browser, session, width=1024, page="run")
    tab.hover("#sidebar-expand")
    tab.mouse.move(20, 14)
    tab.wait_for_function("() => document.body.classList.contains('sidebar-peek')")
    tab.click("#sidebar-expand")
    tab.wait_for_timeout(100)
    shown = tab.evaluate("""() => [document.body.classList.contains('sidebar-collapsed'),
                                   document.body.classList.contains('sidebar-peek')]""")
    tab.context.close()
    assert shown == [False, False]


def test_a_column_shown_by_pointing_stays_shown_through_a_fit(browser, session) -> None:
    tab = _page(browser, session, width=1024, page="run")
    tab.hover("#sidebar-expand")
    tab.mouse.move(20, 14)
    tab.wait_for_function("() => document.body.classList.contains('sidebar-peek')")
    tab.set_viewport_size({"width": 1010, "height": 900})
    tab.wait_for_timeout(200)
    peeking = tab.evaluate("() => document.body.classList.contains('sidebar-peek')")
    tab.context.close()
    assert peeking


def test_the_agent_is_beside_the_page_from_the_least_window(browser, session) -> None:
    """From 932px the strip, the Agent at 340px and a page at its 480px
    with its margins fit side by side; below, the Agent is over the page."""
    found = {}
    for width in (932, 931):
        tab = _page(browser, session, width=width)
        tab.evaluate("() => window.FastMDXAgentBeside.open(true)")
        tab.wait_for_timeout(150)
        found[width] = tab.evaluate(GEOMETRY)
        tab.context.close()
    beside = found[932]
    assert beside["main"][1] <= beside["drawer"][0] + 1 and beside["room"] >= 480, beside
    over = found[931]
    assert over["drawer"][0] < over["main"][1] - 1, over


def test_a_column_the_person_opens_stays_until_the_page_changes(browser, session) -> None:
    tab = _page(browser, session, width=1024, page="run")
    seen = [_folded(tab)]
    tab.click("#sidebar-expand")
    tab.wait_for_timeout(100)
    tab.set_viewport_size({"width": 1000, "height": 900})
    tab.wait_for_timeout(200)
    seen.append(_folded(tab))
    _go(tab, "viewer")
    _go(tab, "run")
    seen.append(_folded(tab))
    tab.context.close()
    # Folded to fit; opened by the person and kept, a narrower window or
    # not; another page and back, fitted again.
    assert seen == [True, False, True]


GEOMETRY = """() => {
    const shown = e => e && getComputedStyle(e).visibility !== 'hidden'
        && getComputedStyle(e).display !== 'none' && e.getBoundingClientRect().width > 1;
    const box = s => { const e = document.querySelector(s);
        if (!shown(e)) return null; const b = e.getBoundingClientRect(); return [b.left, b.right]; };
    const name = document.documentElement.dataset.page;
    const page = document.querySelector('.page[data-page="' + name + '"]');
    const s = getComputedStyle(page);
    const right = {viewer: ['.viewer-canvas-wrap', '.viewer-side'],
                   run: ['.builder-main', '.builder-summary']}[name];
    let beside = null;
    if (right) {
        const a = page.querySelector(right[0]).getBoundingClientRect();
        const b = page.querySelector(right[1]).getBoundingClientRect();
        beside = b.top < a.bottom - 2;
    }
    return {sidebar: box('.sidebar'), panel: box('.side-panel'), drawer: box('#agent-drawer'),
            main: box('.main'), least: parseFloat(s.getPropertyValue('--page-least')),
            room: page.clientWidth - parseFloat(s.paddingLeft) - parseFloat(s.paddingRight),
            beside: beside};
}"""


@pytest.mark.parametrize("width", [1024, 1280, 1440, 1920])
@pytest.mark.parametrize("agent", [False, True], ids=["alone", "agent-beside"])
def test_every_page_fits_the_window(browser, session, width, agent) -> None:
    tab = _page(browser, session, width=width)
    found = {}
    for page in PAGES:
        _go(tab, page)
        if agent and not tab.evaluate("() => window.FastMDXAgentBeside.isOpen()"):
            tab.evaluate("() => window.FastMDXAgentBeside.open(true)")
            tab.wait_for_timeout(100)
        found[page] = tab.evaluate(GEOMETRY)
    tab.context.close()
    _each_page_fits(found, agent)


@pytest.mark.parametrize("width", [1024, 1280, 1440, 1920])
def test_every_page_fits_the_window_with_the_log_open(browser, session, width) -> None:
    """The same walk with the log kept open (as a run leaves it): the side
    panel at its least where shown, and every page at its least, the
    sidebar folded first, then the log (CM, 10-16: the walk never had the
    side panel open)."""
    tab = _page(browser, session, width=width,
                stored={"panelCollapsed": "0", "builderPanelOpen": "1", "viewerPanelOpen": "1"})
    found = {}
    for page in PAGES:
        _go(tab, page)
        found[page] = tab.evaluate(GEOMETRY)
    tab.context.close()
    assert any(g["panel"] for g in found.values()), found
    _each_page_fits(found, False)


def _each_page_fits(found, agent):
    for page, g in found.items():
        columns = [g[name] for name in ("sidebar", "main", "panel", "drawer") if g[name]]
        columns.sort()
        # Side by side, none over another.
        for one, other in zip(columns, columns[1:]):
            assert one[1] <= other[0] + 1, (page, g)
        # Each column at its least: the sidebar whole (180px) or its strip
        # (56px), the side panel 280px, the Agent 340px, the page its own.
        if g["sidebar"]:
            assert round(g["sidebar"][1] - g["sidebar"][0]) in (56,) or \
                g["sidebar"][1] - g["sidebar"][0] >= 180, (page, g)
        if g["panel"]:
            assert g["panel"][1] - g["panel"][0] >= 280, (page, g)
        if g["drawer"]:
            assert g["drawer"][1] - g["drawer"][0] >= 340, (page, g)
        assert g["room"] >= min(g["least"], 480) - 1, (page, g)
        # A page's own right panel beside it wherever the page has its least.
        if g["beside"] is not None:
            assert g["beside"] == (g["room"] >= g["least"]), (page, g)
    if not agent:
        # Without the Agent, every window here holds each page at its least.
        assert all(g["room"] >= g["least"] - 1 for g in found.values()), found


@pytest.mark.parametrize(("width", "agent", "starter"), [
    (912, False, "A protein in water"), (1280, True, "A protein in water"),
    (1366, False, "A protein and its ligand"), (1400, False, "A protein and its ligand")],
    ids=["912", "1280-agent-beside", "1366-ligand", "1400-ligand"])
def test_the_builder_s_form_fits_beside_its_summary(browser, session, width, agent,
                                                    starter) -> None:
    """At the builder's least the form has 380px beside the summary; laid
    out as on a phone there, nothing in it runs past its edge or is cut, and
    what a system's file holds has a column of its own width (C2, 10-16:
    the analyses' two columns ran into each other and past the form, the
    systems row left "What it holds" 0px wide). Every analysis's settings
    open, in a form of about 670 and 700 (CI, 10-16: the methods of
    clustering ran past the form from 640 to 720)."""
    tab = _page(browser, session, width=width, page="run")
    if agent:
        tab.evaluate("() => window.FastMDXAgentBeside.open(true)")
        tab.wait_for_timeout(300)
    tab.locator("#run-starters button").filter(has_text=starter).first.click()
    tab.wait_for_function("() => [...document.querySelectorAll('.builder-system-holds')]"
                          ".some(e => e.textContent.trim())")
    tab.wait_for_timeout(500)
    for _ in range(4):
        # What opens may hold more to open.
        tab.evaluate("""() => document.querySelectorAll(
            '.builder-main button[aria-expanded="false"]').forEach(b => b.click())""")
        tab.wait_for_timeout(250)
    assert tab.evaluate("() => [...document.querySelectorAll('.chip-toggle')]"
                        ".some(e => e.textContent.trim() === 'kmeans'"
                        " && e.getBoundingClientRect().width)")
    seen = tab.evaluate("""() => {
        const main = document.querySelector('.page[data-page="run"] .builder-main');
        const m = main.getBoundingClientRect(), wrong = [];
        for (const e of main.querySelectorAll('*')) {
            const r = e.getBoundingClientRect(), s = getComputedStyle(e);
            if (!r.width || !r.height || s.visibility === 'hidden') continue;
            let a = e.parentElement, scrolls = false;
            for (; a && a !== main; a = a.parentElement) {
                const o = getComputedStyle(a).overflowX;
                if (o === 'auto' || o === 'scroll') { scrolls = true; break; } }
            if (scrolls) continue;
            const said = (e.textContent || '').trim().slice(0, 30);
            if (r.right > m.right + 1 || r.left < m.left - 1) wrong.push(['past the form', said]);
            else if (e.scrollWidth > e.clientWidth + 1 && ['hidden', 'clip'].includes(s.overflowX)
                     && s.textOverflow !== 'ellipsis') wrong.push(['cut', said]);
        }
        // Nothing over anything else: each control and each run of text, by
        // its own boxes (a line of text each), the start cards' unseen radio
        // buttons, which cover their cards, left out (CM, 10-16).
        const seen = e => { const s = getComputedStyle(e);
            return s.visibility !== 'hidden' && s.opacity !== '0' && e.getClientRects().length; };
        const marks = [...main.querySelectorAll('input, select, textarea, button, *')]
            .filter((e, i, all) => all.indexOf(e) === i && seen(e)
                && (['INPUT', 'SELECT', 'TEXTAREA', 'BUTTON'].includes(e.tagName)
                    || [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())));
        // A control by its box; text by its own lines, where they are shown
        // (an element's box holds its text only while the text fits it).
        // Each line cut to what its clipping boxes show (a description held
        // to three lines, a name hidden but read out).
        const shownOf = (e, r) => {
            let left = r.left, right = r.right, top = r.top, bottom = r.bottom;
            for (let a = e; a && a !== main.parentElement; a = a.parentElement) {
                const s = getComputedStyle(a);
                if (s.clipPath !== 'none') return null;
                if (a !== e && s.overflowX === 'visible' && s.overflowY === 'visible') continue;
                if (a === e && !['INPUT', 'SELECT', 'TEXTAREA', 'BUTTON'].includes(e.tagName)
                    && s.overflowX === 'visible' && s.overflowY === 'visible') continue;
                const c = a.getBoundingClientRect();
                left = Math.max(left, c.left); right = Math.min(right, c.right);
                top = Math.max(top, c.top); bottom = Math.min(bottom, c.bottom);
            }
            return right - left > 0 && bottom - top > 0 ? {left, right, top, bottom} : null;
        };
        const lines = e => {
            if (['INPUT', 'SELECT', 'TEXTAREA', 'BUTTON'].includes(e.tagName))
                return [...e.getClientRects()];
            const out = [];
            for (const n of e.childNodes) {
                if (n.nodeType !== 3 || !n.textContent.trim()) continue;
                const r = document.createRange(); r.selectNodeContents(n);
                out.push(...r.getClientRects());
            }
            return out;
        };
        const boxes = marks.map(e => [e, lines(e).filter(r => r.width && r.height)
            .map(r => shownOf(e, r)).filter(Boolean)]);
        for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) {
            const [a, ra] = boxes[i], [b, rb] = boxes[j];
            // A control's own words are its own; text inside any other
            // element is still compared with it (CQ, 10-16).
            const control = e => ['INPUT', 'SELECT', 'TEXTAREA', 'BUTTON'].includes(e.tagName);
            if ((control(a) && a.contains(b)) || (control(b) && b.contains(a))) continue;
            const over = ra.some(p => rb.some(q =>
                Math.min(p.right, q.right) - Math.max(p.left, q.left) > 2
                && Math.min(p.bottom, q.bottom) - Math.max(p.top, q.top) > 2));
            if (over) wrong.push(['over', (a.textContent || a.tagName).trim().slice(0, 20),
                                  (b.textContent || b.tagName).trim().slice(0, 20)]);
        }
        const holds = [...document.querySelectorAll('.builder-system-holds')]
            .filter(e => e.textContent.trim()).map(e => e.getBoundingClientRect().width);
        const cards = getComputedStyle(document.querySelector('.page[data-page="run"] .builder-cards'))
            .gridTemplateColumns.split(' ').length;
        return {form: m.width, wrong, holds, cards}; }""")
    tab.context.close()
    assert seen["wrong"] == [], seen
    assert seen["holds"] and min(seen["holds"]) >= 160, seen
    if width in (912, 1280, 1366):
        # Laid out as on a phone: the start cards two a row, not five
        # (their words 88px wide at a form of 640; CK, 10-16).
        assert seen["form"] < 680 and seen["cards"] == 2, seen
    if width == 1400:
        assert 680 <= seen["form"] < 740, seen


def test_no_fraction_of_a_pixel_falls_between_the_viewer_s_two_layouts(browser, session) -> None:
    """The Agent's width is a share of the window, so the page's is not a
    whole number: at a 1249px window the Viewer has 787.28px. The tools go
    under the molecule below 788px to the fraction, as frame.js reads the
    room (C1, 10-16: neither layout applied, the tools squeezed)."""
    tab = _page(browser, session, width=1300, page="viewer")
    tab.evaluate("() => window.FastMDXAgentBeside.open(true)")
    seen = []
    for width in (1247, 1249, 1250, 1251):
        tab.set_viewport_size({"width": width, "height": 900})
        tab.wait_for_timeout(250)
        seen.append(tab.evaluate("""() => {
            const p = document.querySelector('.page[data-page="viewer"]'), s = getComputedStyle(p);
            const room = p.getBoundingClientRect().width - (p.offsetWidth - p.clientWidth)
                - parseFloat(s.paddingLeft) - parseFloat(s.paddingRight);
            const columns = getComputedStyle(document.querySelector('.viewer-layout'))
                .gridTemplateColumns.split(' ').length;
            return [room, columns]; }"""))
    tab.context.close()
    assert any(room % 1 for room, _ in seen), seen
    assert all(columns == (2 if room >= 788 else 1) for room, columns in seen), seen


@pytest.mark.parametrize("width", [1080, 1086, 1092])
def test_a_scroll_bar_that_comes_refits_the_page(browser_with_scroll_bars, session,
                                                width) -> None:
    """With scroll bars that take room (Windows, Linux), the builder's page
    grows long once a starter is chosen and its column scrolls, the bar
    taking 10px; the page is fitted again, so the summary stays beside the
    form, the sidebar folded (CI, 10-16: the summary went under with the
    sidebar open)."""
    tab = _page(browser_with_scroll_bars, session, width=width, page="run")
    tab.locator("#run-starters button").first.click()
    tab.wait_for_function("() => (document.getElementById('run-starters-note')"
                          ".textContent || '').startsWith('Started')")
    tab.wait_for_timeout(400)
    _two_frames(tab)
    seen = tab.evaluate("""() => {
        const m = document.querySelector('.page[data-page="run"] .builder-main').getBoundingClientRect();
        const s = document.querySelector('.builder-summary').getBoundingClientRect();
        const main = document.querySelector('.main');
        return {beside: s.top < m.bottom - 2, scrolls: main.scrollHeight > main.clientHeight,
                bar: main.offsetWidth - main.clientWidth}; }""")
    tab.context.close()
    assert seen["scrolls"] and seen["bar"] > 0, seen
    assert seen["beside"], seen


def test_the_fit_reads_the_page_to_the_fraction(browser, session) -> None:
    """A page whose width is not a whole number (the Agent's is a share of
    the window) and a quarter of a pixel under its least is under it for
    the container query, so frame.js folds the sidebar for it too, where
    whole pixels read 784 (CI, 10-16: nothing tested frame.js's reading)."""
    tab = _page(browser, session, width=1920, page="run")
    seen = tab.evaluate("""() => {
        const p = document.querySelector('.page[data-page="run"]'), s = getComputedStyle(p);
        const pad = parseFloat(s.paddingLeft) + parseFloat(s.paddingRight);
        p.style.boxSizing = 'border-box'; p.style.maxWidth = 'none';
        p.style.flex = 'none'; p.style.width = (783.75 + pad) + 'px';
        const room = p.getBoundingClientRect().width - (p.offsetWidth - p.clientWidth) - pad;
        const before = [room, p.clientWidth - pad, document.body.classList.contains('sidebar-collapsed')];
        window.FastMDXFrame.fitThePage();
        return [before, document.body.classList.contains('sidebar-collapsed')]; }""")
    tab.context.close()
    assert abs(seen[0][0] - 783.75) < 0.01 and seen[0][2] is False, seen
    assert seen[1] is True, seen


def test_the_steps_keep_their_names_while_they_fit(browser, session) -> None:
    """The steps' names show wherever the bar has room for them (a form of
    about 580px at 1280, the sidebar open), and under it (the form's 390px
    at 912) the numbers alone show, the names still read out (CJ, 10-16:
    hidden under 640px of form, though they fit from 540)."""
    tab = _page(browser, session, width=1280, page="run")
    tab.locator("#run-starters button").filter(has_text="A protein in water").first.click()
    tab.wait_for_function("() => [...document.querySelectorAll('.builder-step-link')]"
                          ".every(e => !e.hidden)")
    tab.wait_for_timeout(200)
    found = {}
    for width in (1280, 912):
        tab.set_viewport_size({"width": width, "height": 900})
        tab.wait_for_timeout(300)
        _two_frames(tab)
        found[width] = tab.evaluate(STEPS)
    tab.context.close()
    for width, named in ((1280, True), (912, False)):
        seen = found[width]
        assert seen["shown"] == [named] * 5 and all(seen["read"]) and seen["fits"], found


STEPS = """() => {
        const bar = document.querySelector('.page[data-page="run"] .builder-steps');
        const names = [...bar.querySelectorAll('.builder-step-link span')];
        return {form: document.querySelector('.page[data-page="run"] .builder-main')
                    .getBoundingClientRect().width,
                shown: names.map(e => e.getBoundingClientRect().width > 1),
                read: names.map(e => getComputedStyle(e).display !== 'none' && !!e.textContent.trim()),
                fits: bar.scrollWidth <= bar.clientWidth + 1}; }"""
