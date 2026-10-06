"""The Viewer's tools are on a rail, one shown at a time; its ground follows
the scheme.

The settings beside the molecule were sixteen sections one under the
other, the one wanted often a long scroll down a narrow column. Each is a
button on a rail at the column's edge now, named on hover, and the column
shows the one chosen; a section the study has nothing for has no button.
The molecule was on black on the Light scheme too; it is on the scheme's
ground unless the person chose one.
"""

from __future__ import annotations

import pytest

from tests.test_the_drawing_scripts_run_in_a_browser import _open, _write_study


@pytest.fixture(scope="module")
def dashboard(tmp_path_factory):
    from fastmdxplora.gui.server import start_dashboard_session

    study = _write_study(tmp_path_factory.mktemp("rail") / "study")
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    yield session
    session.server.shutdown()


@pytest.fixture
def page(dashboard):
    pytest.importorskip("playwright.sync_api")
    for opened in _open(dashboard, "#viewer"):
        opened.set_default_timeout(60000)
        opened.wait_for_selector("#viewer-rail:not([hidden]) .rail-btn")
        yield opened


def _shown(page) -> list[str]:
    """The tools shown; what is picked and measured is under each."""
    shown = page.evaluate("""() => [...document.querySelectorAll('.viewer-side > .side-section')]
        .filter((s) => getComputedStyle(s).display !== 'none').map((s) => s.id)""")
    assert shown[-1] == "side-info"
    return shown[:-1]


def test_one_tool_is_shown_and_its_button_says_so(page) -> None:
    assert _shown(page) == ["side-display"]
    chosen = page.get_attribute('[data-tool="side-display"]', "aria-selected")
    assert chosen == "true"
    page.click('[data-tool="side-saved"]')
    assert _shown(page) == ["side-saved"]
    assert page.get_attribute('[data-tool="side-display"]', "aria-selected") == "false"
    assert page.evaluate("() => document.getElementById('side-saved').open")
    assert not page.errors


def test_a_section_with_nothing_has_no_button(page) -> None:
    # Read at one moment, as the study is still being learned: each hidden
    # section's button hidden with it.
    page.wait_for_function("""() => [...document.querySelectorAll(
        '.viewer-side > .side-section[role=tabpanel]')].every((s) =>
        document.querySelector(`[data-tool="${s.id}"]`).hidden === s.hidden)""")
    hidden = page.evaluate("""() => [...document.querySelectorAll('.viewer-side > .side-section')]
        .filter((s) => s.hidden).map((s) => s.id)""")
    assert hidden, "the fixture should leave some section hidden"
    # Shown as the study is learned: its button comes with it.
    page.evaluate("() => { document.getElementById('side-rama').hidden = false; }")
    page.wait_for_selector('[data-tool="side-rama"]:not([hidden])')


def test_a_tool_kept_is_chosen_once_the_study_shows_it(dashboard) -> None:
    """Most tools are shown only as the study is learned; the one kept was
    passed over as the page opened, and never chosen."""
    for opened in _open(dashboard, "#viewer"):
        opened.set_default_timeout(60000)
        opened.add_init_script("try { localStorage.setItem('fmx.viewerTool', 'side-rama'); } catch (e) {}")
        opened.reload(wait_until="domcontentloaded")
        opened.wait_for_function("() => window.FastMDXViewerRail.chosen === 'side-rama'")
        heads = opened.evaluate("""() => [...document.querySelectorAll(
            '.viewer-side > .side-section[role=tabpanel] > summary')].map((s) => s.tabIndex)""")
        assert heads and set(heads) == {-1}


def test_the_tool_chosen_is_kept(page) -> None:
    page.click('[data-tool="side-movie"]')
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("#viewer-rail:not([hidden]) .rail-btn")
    page.wait_for_function("() => window.FastMDXViewerRail.chosen === 'side-movie'")
    # What is picked and measured is no tool: it is under each.
    assert page.locator('[data-tool="side-info"]').count() == 0


def test_the_keys_move_along_the_rail(page) -> None:
    page.click('[data-tool="side-view"]')
    page.locator("#viewer-canvas").focus()
    page.keyboard.press("]")
    assert _shown(page) == ["side-display"]
    page.keyboard.press("[")
    assert _shown(page) == ["side-view"]
    # On the rail, the arrows, as along any tabs.
    page.focus('[data-tool="side-view"]')
    page.keyboard.press("ArrowDown")
    assert _shown(page) == ["side-display"]
    assert page.evaluate("() => document.activeElement.dataset.tool") == "side-display"


def test_a_section_opened_from_elsewhere_is_chosen(page) -> None:
    page.wait_for_timeout(1600)
    page.evaluate("() => { const s = document.getElementById('side-saved'); s.open = false; s.open = true; }")
    page.wait_for_function("() => window.FastMDXViewerRail.chosen === 'side-saved'")


def test_the_ground_follows_the_scheme(page) -> None:
    def ground():
        return page.get_attribute("#viewer-canvas-frame", "data-ground")

    page.wait_for_function("() => document.getElementById('viewer-canvas-frame').dataset.ground")
    page.evaluate("() => window.FastMDXFrame.applyTheme('dark', false)")
    page.wait_for_function("() => document.getElementById('viewer-canvas-frame').dataset.ground === 'dark'")
    page.evaluate("() => window.FastMDXFrame.applyTheme('light', false)")
    page.wait_for_function("() => document.getElementById('viewer-canvas-frame').dataset.ground === 'light'")
    # A view saved says the ground it was seen on.
    assert page.evaluate("() => window.FastMDXMoleculeViewer.viewNow().ground") == "white"
    # Chosen, it stays: the white ground on the dark scheme.
    page.evaluate("() => window.FastMDXFrame.applyTheme('dark', false)")
    page.click('[data-tool="side-view"]')
    page.select_option("#viewer-ground", "white")
    assert ground() == "light"
    page.evaluate("() => window.FastMDXFrame.applyTheme('light', false)")
    page.evaluate("() => window.FastMDXFrame.applyTheme('dark', false)")
    assert ground() == "light"
