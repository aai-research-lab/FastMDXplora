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
NEW_NAV = ["run", "agent"]


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
    # The Playback starts closed; these read what is in it.
    context.add_init_script(
        "try { localStorage.setItem('fmx.viewerPlaybackOpen', '1'); } catch (e) {}")
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

    def test_the_ways_to_begin_then_the_studies_then_the_one_open(self, browser, studies) -> None:
        """New study and the Agent first, kept at the top;
        then Recent, All studies, the active study and its pages. The Agent
        was under New study, though it answers about the open study too."""
        page = _open(browser, studies["finished"])
        said = page.evaluate("""() => ({
            order: [...document.querySelectorAll('.sidebar a.nav-link')].map(a => a.dataset.viewLink),
            headings: [...document.querySelectorAll('.sidebar .nav-heading')]
                .map(h => h.textContent.trim()),
            top: getComputedStyle(document.querySelector('.sidebar-top')).position,
            begin: [...document.querySelectorAll('.sidebar-top .nav-link')].map(a => a.dataset.viewLink),
        })""")
        page.context.close()
        assert said == {"order": NEW_NAV + ["studies"] + STUDY_NAV,
                        "headings": ["Active study"],
                        "top": "sticky", "begin": NEW_NAV}

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
        # Its ID, four capitals from its file's name; the file on hover.
        assert name == "TRIA"
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

    def test_while_a_study_runs_the_log_gives_the_viewer_the_room(self, browser, studies) -> None:
        # The log is open while a study runs; the Viewer closes it, and the
        # settings stay beside the molecule.
        page = _open(browser, studies["running"])
        canvas = self._canvas(page)
        closed = page.evaluate("() => document.body.classList.contains('panel-collapsed')")
        beside = page.evaluate("""() => document.querySelector('.viewer-side').getBoundingClientRect().left
            > document.getElementById('viewer-canvas-frame').getBoundingClientRect().right""")
        page.context.close()
        assert closed and beside and canvas > 700

    def test_a_finished_studys_frame_is_not_called_live(self, browser, studies) -> None:
        page = _open(browser, studies["finished"], where="#viewer")
        page.wait_for_function(
            "() => document.getElementById('overlay-tag').textContent !== 'offline'",
            timeout=60000)
        tag = page.locator("#overlay-tag").text_content()
        page.context.close()
        assert tag != "LIVE"

    def test_a_finished_study_has_no_live_controls(self, browser, studies) -> None:
        # Following the run, "Now" and "Pause Updates" were offered beside a
        # finished study's last frame, which read "LATEST" and its age in
        # minutes: "age 5311m" for one three days old.
        page = _open(browser, studies["finished"], where="#viewer")
        # Once the viewer has the study's status, which is what decides.
        page.wait_for_function(
            "() => window.FastMDXMoleculeViewer"
            " && window.FastMDXMoleculeViewer.STATE.runStatus === 'completed'",
            timeout=60000)
        tag = page.locator("#overlay-tag").text_content()
        # Looked for in the View tool, where it is when there is a run.
        page.evaluate("() => window.FastMDXViewerRail.choose('side-view')")
        age = page.locator("#overlay-age").is_visible()
        follow = page.locator("label:has(#traj-follow)").is_visible()
        live = page.locator('[aria-label="Live structure"]').is_visible()
        page.context.close()
        assert tag not in ("LIVE", "LATEST")
        assert not age and not follow and not live

    def test_a_running_study_has_them(self, browser, studies) -> None:
        page = _open(browser, studies["running"], where="#viewer")
        page.wait_for_selector("label:has(#traj-follow)", state="visible", timeout=60000)
        # In the View tool, one of the Viewer's tools on its rail.
        page.evaluate("() => window.FastMDXViewerRail.choose('side-view')")
        live = page.locator('[aria-label="Live structure"]').is_visible()
        page.context.close()
        assert live


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
        label = page.locator("#live-simtime-label").text_content()
        note = page.locator("#live-simtime-note").text_content()
        page.context.close()
        # A live record with no plan in it says its own total, as such.
        assert (label, text, note) == ("Simulated", "12 ps", "equilibration included")


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
        assert facts["nav"] == len(["studies"] + STUDY_NAV + NEW_NAV)

    @pytest.mark.parametrize("where", STUDY_NAV + NEW_NAV + ["studies"])
    def test_every_page_keeps_the_navigation_and_the_screen(self, browser, studies, where) -> None:
        """The Viewer folds the sidebar (1303), which on a phone is the bar
        of navigation across the top: it went, and left an empty band 200
        pixels high above the Viewer's heading. And the Analysis page's
        table of what was determined pushed the page sideways."""
        page = _open(browser, studies["finished"], width=390, height=844)
        page.evaluate(f"() => window.FastMDXDashboard.navigate('{where}')")
        page.wait_for_timeout(1500)
        facts = page.evaluate(f"""() => {{
            const title = document.querySelector('.page[data-page="{where}"] .page-title');
            const r = title.getBoundingClientRect();
            const bar = document.querySelector('.sidebar').getBoundingClientRect();
            return {{
                title: Math.round(r.top),
                bar: getComputedStyle(document.querySelector('.sidebar')).visibility,
                barBottom: Math.round(bar.bottom),
                wider: document.scrollingElement.scrollWidth - innerWidth,
                nav: [...document.querySelectorAll('.sidebar-nav .nav-link')]
                    .filter(a => a.offsetWidth > 0 && getComputedStyle(a).visibility === 'visible').length,
            }};
        }}""")
        page.context.close()
        assert facts["bar"] == "visible" and facts["nav"] == len(["studies"] + STUDY_NAV + NEW_NAV), facts
        # The heading follows the bar, with nothing empty between.
        assert facts["title"] - facts["barBottom"] < 40, facts
        assert facts["wider"] <= 0, facts

    def test_the_navigation_goes_where_it_says(self, browser, studies) -> None:
        page = _open(browser, studies["finished"], width=390, height=844)
        page.locator('.sidebar-nav .nav-link[data-view-link="analysis"]').click()
        page.wait_for_timeout(300)
        shown = page.evaluate("() => document.documentElement.dataset.page")
        page.context.close()
        assert shown == "analysis"


class TestWhatIsNotSaid:

    def test_an_overlay_field_with_nothing_in_it(self, browser, studies) -> None:
        """A structure with no live frame showed each field with a dash for
        its value."""
        page = _open(browser, studies["finished"], where="#viewer")
        shown = page.evaluate("""() => [...document.querySelectorAll('#viewer-overlay .overlay-detail')]
            .filter(f => !f.hidden).map(f => f.textContent.trim())""")
        page.context.close()
        assert all("—" not in text for text in shown), shown

    def test_the_citation_stays_in_its_card(self, browser, studies) -> None:
        page = _open(browser, studies["finished"], where="#overview")
        page.evaluate("() => window.FastMDXDialog.open('cite-dialog')")
        page.wait_for_timeout(300)
        widths = page.evaluate("() => { const b = document.getElementById('cite-bibtex');"
                               " return [b.scrollWidth, b.clientWidth]; }")
        page.context.close()
        assert widths[0] <= widths[1]
