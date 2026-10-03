"""The viewer answers the keys a player has.

Every control was a button to find and click: stepping through a trajectory
a frame at a time meant a click per frame on "Frame >". Space now plays and
pauses, the arrows step a frame (ten with Shift), Home and End go to the
ends, R centres and F fills the screen; only on the viewer's page, and never
while something is being typed.
"""

from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")
pytest.importorskip("mdtraj")

from tests.test_the_drawing_scripts_run_in_a_browser import FRAMES, _write_study  # noqa: E402

VIEWER = "window.FastMDXMoleculeViewer.STATE"


@pytest.fixture(scope="module")
def dashboard(tmp_path_factory):
    from fastmdxplora.gui.server import start_dashboard_session

    study = _write_study(tmp_path_factory.mktemp("keys") / "study")
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    yield session
    session.server.shutdown()


@pytest.fixture
def page(dashboard):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        opened = browser.new_page(viewport={"width": 1400, "height": 900})
        opened.goto(dashboard.url + "#viewer", wait_until="domcontentloaded")
        if not opened.evaluate("() => !!document.createElement('canvas').getContext('webgl')"):
            pytest.skip("this browser has no WebGL, so the viewer cannot render")
        # Sixty seconds: a software renderer, beside a full suite, took
        # longer than thirty to render once.
        opened.wait_for_function(f"() => window.FastMDXMoleculeViewer && {VIEWER}.model",
                                 timeout=60000)
        opened.set_default_timeout(60000)
        # A click where nothing is, so the page has the keyboard: the mouse
        # itself, since waiting for the body to count as clickable took over
        # thirty seconds beside a full suite.
        opened.mouse.click(5, 5)
        yield opened
        browser.close()


def _frame(page) -> str:
    return page.evaluate("() => document.getElementById('traj-slider').value")


def _press(page, key: str, frame: str) -> None:
    page.keyboard.press(key)
    page.wait_for_function(
        f"() => {VIEWER}.playbackLoaded && document.getElementById('traj-slider').value === '{frame}'",
        timeout=20000)


def test_the_arrows_step_through_it(page) -> None:
    _press(page, "ArrowRight", "1")
    _press(page, "Shift+ArrowRight", "11")
    _press(page, "ArrowLeft", "10")
    _press(page, "End", str(FRAMES - 1))
    _press(page, "Home", "0")


def test_space_plays_and_pauses(page) -> None:
    page.keyboard.press("Space")
    page.wait_for_function(f"() => {VIEWER}.playbackPlaying", timeout=20000)
    page.keyboard.press("Space")
    page.wait_for_function(f"() => !{VIEWER}.playbackPlaying", timeout=20000)


def test_typing_is_typing(page) -> None:
    field = page.locator("#pocket-cutoff")
    page.evaluate("() => document.getElementById('ligand-tools').hidden = false")
    field.focus()
    page.keyboard.press("ArrowRight")
    page.keyboard.press("f")
    page.wait_for_timeout(300)
    assert not page.evaluate(f"() => {VIEWER}.playbackLoaded")


def test_only_on_the_viewers_page(page) -> None:
    page.evaluate("() => window.FastMDXDashboard.navigate('files')")
    page.keyboard.press("ArrowRight")
    page.wait_for_timeout(300)
    assert not page.evaluate(f"() => {VIEWER}.playbackLoaded")


def test_the_keys_are_said(page) -> None:
    keys = page.locator("#viewer-keys")
    assert keys.is_visible() and "Space" in keys.text_content()
