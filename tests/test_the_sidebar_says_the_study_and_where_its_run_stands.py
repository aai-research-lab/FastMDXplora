"""The sidebar, top to bottom, as a person reads it.

The product on one line; the study on screen as a card saying where it
stands, which opens the studies to switch to; its pages and the two ways to
start another, each with a line icon; while a run goes on, or after it
stopped short, a card of where it stands (and, stopped or failed, what
would fix it); and the person with the settings. Nothing of it is cut off
in a window 900 pixels high: the progress card is there only while it says
something.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

import pytest

from tests.test_the_workspace_says_its_studies import _study


def _live(study: Path, **record) -> Path:
    (study / "simulation").mkdir(exist_ok=True)
    (study / "simulation" / "live_status.json").write_text(json.dumps(record),
                                                          encoding="utf-8")
    return study


@pytest.fixture
def workspace(tmp_path) -> Path:
    _study(tmp_path / "finished", system="1UBQ", started="2026-09-03T10:00:00+00:00")
    _live(_study(tmp_path / "running", system="1AKE", started="2026-09-02T10:00:00+00:00"),
          status="running", stage="production", current_step=1240000,
          total_planned_steps=2000000, platform="CUDA",
          stage_states={"setup": "completed", "minimization": "completed",
                        "nvt": "completed", "npt": "completed", "production": "current"})
    _live(_study(tmp_path / "stopped", system="1L2Y", started="2026-09-01T10:00:00+00:00"),
          status="stopped", stage="npt", current_step=40000, total_planned_steps=2000000,
          platform="CPU",
          stage_states={"setup": "completed", "minimization": "completed",
                        "nvt": "completed", "npt": "current"})
    return tmp_path


def test_the_icons_are_written_into_the_page():
    from fastmdxplora.gui.sidebar_icons import ICONS, icon, with_icons
    from fastmdxplora.gui.server import _load_template

    page = with_icons(_load_template())
    assert "<!--icon:" not in page
    sidebar = page[page.index('<aside class="sidebar"'):page.index("</aside>")]
    for name in ("studies", "overview", "viewer", "analysis", "report", "files", "agent",
                 "config"):
        assert icon(name) in sidebar, name
    assert with_icons("<!--icon:gear:x y-->") == icon("gear", "x y")
    assert set(ICONS) >= {"collapse", "chevron", "pause", "play", "refresh", "gear"}


@contextmanager
def _sidebar_of(browser, study: Path, height: int = 900):
    """The page open on a study, and what its sidebar says."""
    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    page = browser.new_page(viewport={"width": 1440, "height": height})
    try:
        page.set_default_timeout(60000)
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(session.url + "#overview", wait_until="domcontentloaded")
        page.wait_for_function("() => document.getElementById('topbar-run-title')"
                               ".textContent !== 'No active study'")
        page.wait_for_function("() => !/Ready|Waiting/.test(document.getElementById("
                               "'topbar-status-text').textContent)")
        page.wait_for_function("() => !document.body.classList.contains('state-loading')")
        page.wait_for_function("() => document.getElementById('nav-studies-count')"
                               ".textContent !== ''")
        facts = page.evaluate(_FACTS)
        facts["errors"] = errors
        yield facts, page
    finally:
        page.close()
        session.server.shutdown()


_FACTS = """() => {
    const $ = (id) => document.getElementById(id);
    const card = $('sidebar-progress');
    const shown = (el) => !!el && getComputedStyle(el).display !== 'none'
        && el.offsetParent !== null;
    const side = document.querySelector('aside.sidebar');
    const reachable = [...side.querySelectorAll('.sidebar-nav .nav-link, #settings-open')]
        .filter((a) => {
            const r = a.getBoundingClientRect();
            const top = document.elementFromPoint(r.left + 12, r.top + r.height / 2);
            return r.bottom <= innerHeight && a.contains(top);
        }).length;
    return {
        word: $('topbar-status-text').textContent,
        dot: $('topbar-status-dot').className,
        platform: shown($('sidebar-platform')) ? $('sidebar-platform').textContent : '',
        run: card.getAttribute('data-run'),
        card: shown(card),
        stage: $('topbar-stage').textContent,
        count: $('sidebar-stage-count').textContent,
        fix: shown($('sidebar-fix')),
        pause: shown($('pause-toggle')),
        links: side.querySelectorAll('.sidebar-nav .nav-link').length,
        icons: side.querySelectorAll('.sidebar-nav .nav-link svg.nav-icon').length,
        reachable,
        overflow: side.scrollHeight - side.clientHeight,
        studies: $('nav-studies-count').textContent,
    };
}"""


@pytest.fixture(scope="module")
def browser():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        launched = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        yield launched
        launched.close()


def test_a_finished_study_has_no_progress_card(browser, workspace):
    with _sidebar_of(browser, workspace / "finished") as (facts, _):
        pass
    assert facts["errors"] == []
    assert facts["word"] == "Completed" and "status-dot-completed" in facts["dot"]
    assert facts["run"] == "" and not facts["card"]
    # Every page's link and the person at the foot are on the screen, and
    # nothing in the sidebar scrolls out of reach at 900 pixels.
    assert facts["links"] == facts["icons"] == 8
    assert facts["reachable"] == 9 and facts["overflow"] <= 0
    assert facts["studies"] == "3"


def test_a_running_study_says_where_it_is(browser, workspace):
    with _sidebar_of(browser, workspace / "running") as (facts, _):
        pass
    assert facts["errors"] == []
    assert (facts["word"], facts["platform"]) == ("Running", "CUDA")
    assert "status-dot-live" in facts["dot"]
    assert facts["run"] == "running" and facts["card"]
    # Of the stages this study can reach: it plans no analysis or report.
    assert (facts["stage"], facts["count"]) == ("Production", "Stage 5 of 5")
    assert facts["pause"] and not facts["fix"]
    assert facts["reachable"] == 9 and facts["overflow"] <= 0


def test_a_stopped_study_says_where_and_what_would_fix_it(browser, workspace):
    with _sidebar_of(browser, workspace / "stopped") as (facts, page):
        page.click('.nav-link[data-view-link="analysis"]')
        page.click("#sidebar-fix")
        page.wait_for_function("() => document.documentElement.dataset.page === 'overview'")
    assert facts["errors"] == []
    assert facts["word"] == "Stopped" and "status-dot-waiting" in facts["dot"]
    assert facts["run"] == "stopped" and facts["card"]
    assert (facts["stage"], facts["count"]) == ("Stopped in NPT", "Stage 4 of 5")
    assert facts["fix"] and not facts["pause"]


def test_the_study_card_opens_the_studies_to_switch_to(browser, workspace):
    with _sidebar_of(browser, workspace / "finished") as (facts, page):
        assert page.get_attribute("#study-card", "aria-expanded") == "false"
        page.click("#study-card")
        page.wait_for_selector("#study-menu:not([hidden]) .study-menu-study")
        listed = page.eval_on_selector_all(".study-menu-study .study-menu-name",
                                           "n => n.map((x) => x.textContent)")
        expanded = page.get_attribute("#study-card", "aria-expanded")
        folder = page.text_content("#study-menu .study-path")
        page.keyboard.press("Escape")
        closed = page.is_hidden("#study-menu")
        focused = page.evaluate("() => document.activeElement.id")
        # All studies, from the menu, opens that page.
        page.click("#study-card")
        page.click('#study-menu [data-menu-page="studies"]')
        page.wait_for_function("() => document.documentElement.dataset.page === 'studies'")
        after = page.is_hidden("#study-menu")
    assert facts["errors"] == []
    # The workspace's other studies, newest first, not the one on screen.
    assert listed == ["running", "stopped"] and expanded == "true"
    assert folder.endswith("finished")
    assert closed and focused == "study-card" and after


def test_the_standalone_dashboard_has_the_same_sidebar(workspace):
    from types import SimpleNamespace

    from fastmdxplora.gui.report_dashboard import build_dashboard
    from fastmdxplora.gui.sidebar_icons import icon

    # A page written while the run went on: a live record and no manifest.
    (workspace / "running" / "manifest.json").unlink()
    pages = {}
    for name in ("finished", "running"):
        root = workspace / name
        (root / "report").mkdir(exist_ok=True)
        build_dashboard(orchestrator=SimpleNamespace(output_dir=root, system="1L2Y"),
                        output_dir=root / "report", title="A study")
        pages[name] = (root / "report" / "dashboard.html").read_text(encoding="utf-8")
    assert '<div class="sidebar-progress" data-run="">' in pages["finished"]
    assert '<div class="sidebar-progress" data-run="running">' in pages["running"]
    for html in pages.values():
        assert icon("analysis") in html and '<span class="study-kicker">Study</span>' in html
        assert 'class="sidebar-study-card"' in html and "<!--icon:" not in html
