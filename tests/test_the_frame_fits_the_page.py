"""The frame gives the page the room it needs, on a desk and on a phone.

Opened on a finished study at 1440 by 900, the frame gave the log a fixed
560 pixels on every page, left the page 640, and the viewer's canvas 240 of
them beside its information pane. The navigation sat below the progress
block and a 900-pixel window cut its last entry off under the settings
trigger. "Pause" was offered beside a finished study's results, the study
was named by the path of its structure file, and at 390 pixels the sidebar
and the log each took the screen and the page had none of it.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_the_drawing_scripts_run_in_a_browser import _write_study  # noqa: E402

STUDY_NAV = ["overview", "viewer", "analysis", "report", "files"]
NEW_NAV = ["agent", "run"]


def _status(root: Path, status: str) -> None:
    (root / "simulation" / "live_status.json").write_text(json.dumps({
        "status": status, "stage": "production", "current_step": 2500,
        "total_steps": 2500, "simulation_time_completed_ns": 0.012,
        "system_id": "/data/lab/structures/tri-ala.pdb"}), encoding="utf-8")


@pytest.fixture(scope="module")
def studies(tmp_path_factory):
    from fastmdxplora.gui.server import start_dashboard_session

    finished = _write_study(tmp_path_factory.mktemp("frame") / "finished")
    _status(finished, "completed")
    running = finished.parent / "running"
    shutil.copytree(finished, running)
    _status(running, "running")
    sessions = {name: start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
                for name, root in (("finished", finished), ("running", running))}
    yield sessions
    for session in sessions.values():
        session.server.shutdown()


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        launched = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        yield launched
        launched.close()


def _open(browser, session, where="#overview", width=1440, height=900, stored=None):
    context = browser.new_context(viewport={"width": width, "height": height})
    if stored:
        context.add_init_script(
            "(() => {" + "".join(f"localStorage.setItem('fmx.{k}', '{v}');"
                                 for k, v in stored.items()) + "})()")
    page = context.new_page()
    # A software renderer beside a full suite on two cores is slow to start.
    page.set_default_timeout(60000)
    page.goto(session.url + where, wait_until="domcontentloaded")
    page.wait_for_function(
        "() => document.getElementById('topbar-run-title').textContent !== 'No active study'"
        " && document.getElementById('topbar-step').textContent !== '—'",
        timeout=60000)
    page.wait_for_timeout(300)
    return page


class TestTheNavigation:

    def test_the_study_then_the_ways_to_start_one(self, browser, studies) -> None:
        page = _open(browser, studies["finished"])
        order = page.evaluate(
            "() => [...document.querySelectorAll('.sidebar-nav .nav-link')]"
            ".map(a => a.dataset.viewLink)")
        headings = page.evaluate(
            "() => [...document.querySelectorAll('.sidebar-nav .nav-heading')]"
            ".map(h => h.textContent)")
        page.context.close()
        assert order == STUDY_NAV + NEW_NAV
        assert headings == ["Study", "New study"]

    def test_every_entry_is_in_a_short_window(self, browser, studies) -> None:
        page = _open(browser, studies["finished"], height=720)
        hidden = """() => [...document.querySelectorAll('.sidebar-nav .nav-link')]
            .filter(a => {
                const r = a.getBoundingClientRect();
                const top = document.elementFromPoint(r.left + 20, r.top + r.height / 2);
                return r.bottom > innerHeight || !a.contains(top);
            }).map(a => a.dataset.viewLink)"""
        # Once the loading screen has gone, which on a loaded machine is
        # after the first numbers arrive: it covers everything until then.
        try:
            page.wait_for_function(f"() => ({hidden})().length === 0", timeout=60000)
        except Exception:  # noqa: BLE001 - the assertion below says which
            pass
        covered = page.evaluate(hidden)
        page.context.close()
        assert covered == []

    def test_the_study_is_named_by_its_system(self, browser, studies) -> None:
        page = _open(browser, studies["finished"])
        title = page.locator("#topbar-run-title")
        name, tip = title.text_content(), title.get_attribute("title")
        page.context.close()
        assert name == "tri-ala"
        assert tip == "/data/lab/structures/tri-ala.pdb"


class TestTheLog:

    def _collapsed(self, page) -> bool:
        return page.evaluate("() => document.body.classList.contains('panel-collapsed')")

    def test_closed_on_a_finished_study(self, browser, studies) -> None:
        page = _open(browser, studies["finished"])
        assert self._collapsed(page)
        page.context.close()

    def test_open_while_a_study_runs(self, browser, studies) -> None:
        page = _open(browser, studies["running"])
        assert not self._collapsed(page)
        page.context.close()

    def test_a_choice_is_kept(self, browser, studies) -> None:
        page = _open(browser, studies["finished"], stored={"panelCollapsed": "0"})
        assert not self._collapsed(page)
        page.context.close()
        page = _open(browser, studies["running"], stored={"panelCollapsed": "1"})
        assert self._collapsed(page)
        page.context.close()

    def test_its_width_leaves_the_page_the_most(self, browser, studies) -> None:
        page = _open(browser, studies["running"])
        width = page.evaluate("() => document.getElementById('side-panel').offsetWidth")
        page.context.close()
        assert width <= 420


class TestTheViewer:

    def _canvas(self, page) -> float:
        page.evaluate("() => window.FastMDXDashboard.navigate('viewer')")
        page.wait_for_timeout(400)
        return page.evaluate("() => document.getElementById('viewer-canvas-frame').offsetWidth")

    def test_the_canvas_has_the_page(self, browser, studies) -> None:
        page = _open(browser, studies["finished"])
        assert self._canvas(page) > 700
        page.context.close()

    def test_beside_the_log_the_pane_goes_under_it(self, browser, studies) -> None:
        page = _open(browser, studies["running"])
        canvas = self._canvas(page)
        main = page.evaluate("() => document.querySelector('.page-shell').offsetWidth")
        page.context.close()
        assert canvas > 0.85 * (main - 64)

    def test_a_finished_studys_frame_is_not_called_live(self, browser, studies) -> None:
        page = _open(browser, studies["finished"], where="#viewer")
        page.wait_for_function(
            "() => document.getElementById('overlay-tag').textContent !== 'offline'",
            timeout=60000)
        tag = page.locator("#overlay-tag").text_content()
        page.context.close()
        assert tag != "LIVE"


class TestTheControls:

    def test_no_pause_for_a_finished_study(self, browser, studies) -> None:
        page = _open(browser, studies["finished"])
        assert page.locator("#pause-toggle").is_hidden()
        page.context.close()

    def test_pause_while_a_study_runs(self, browser, studies) -> None:
        page = _open(browser, studies["running"])
        assert page.locator("#pause-toggle").is_visible()
        page.context.close()

    def test_simulated_time_has_no_empty_places(self, browser, studies) -> None:
        page = _open(browser, studies["finished"])
        text = page.locator("#live-simtime-cell").text_content()
        page.context.close()
        assert text == "0.012 ns"


class TestAPhone:

    def test_the_page_is_the_screen(self, browser, studies) -> None:
        page = _open(browser, studies["running"], width=390, height=844)
        facts = page.evaluate("""() => {
            const title = document.querySelector('.page[data-page="overview"] .page-title');
            const r = title.getBoundingClientRect();
            const panel = document.getElementById('side-panel');
            return {
                title: r.top >= 0 && r.bottom <= innerHeight && r.left >= 0,
                wider: document.scrollingElement.scrollWidth - innerWidth,
                panel: panel.offsetWidth,
                nav: [...document.querySelectorAll('.sidebar-nav .nav-link')]
                    .filter(a => a.offsetWidth > 0).length,
            };
        }""")
        page.context.close()
        assert facts["title"], "the page's own heading is not on the screen"
        assert facts["wider"] <= 0
        assert facts["panel"] == 0
        assert facts["nav"] == len(STUDY_NAV + NEW_NAV)

    def test_the_navigation_goes_where_it_says(self, browser, studies) -> None:
        page = _open(browser, studies["finished"], width=390, height=844)
        page.locator('.sidebar-nav .nav-link[data-view-link="analysis"]').click()
        page.wait_for_timeout(300)
        shown = page.evaluate("() => document.documentElement.dataset.page")
        page.context.close()
        assert shown == "analysis"
