"""The viewer has one transport and one toolbar of named icons.

Under the canvas were thirty text buttons in five rows: a transport (Play,
Pause, Reverse, Prev, Next, and two arrows), then a second row that repeated
three of them ("Play Trajectory", "< Frame", "Frame >"), then representation,
colour and camera as rows of chips, with a Spin button and a Stop button for
one motion. Now the transport is one row of icons with the play button
showing what pressing it does, the view is one group of icons (reset, zoom,
spin as one toggle, full screen, a picture), how the molecule is drawn and
coloured are two lists, and every icon is named to a screen reader and in its
tooltip, with its key where it has one.

Where the viewer's angstroms meet the analyses' nanometres, the pocket cutoff
is said in both.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

TEMPLATE = Path(__file__).resolve().parent.parent / "src" / "fastmdxplora" / "gui" / \
    "templates" / "dashboard.html"


def _viewer_controls() -> str:
    page = TEMPLATE.read_text(encoding="utf-8")
    start = page.index('<div class="viewer-controls">')
    return page[start:page.index('<aside class="info-panel"', start)]


class TestTheMarkup:
    def test_each_transport_control_once(self):
        controls = _viewer_controls()
        for action in ("play-trajectory", "prev-frame", "next-frame"):
            assert controls.count(f'data-action="{action}"') == 1, action
        for gone in ("play", "pause", "prev", "next"):
            assert f'data-traj="{gone}"' not in controls
        assert 'data-cam="stop"' not in controls

    def test_every_icon_is_named(self):
        for button in re.findall(r"<button[^>]*icon-btn[^>]*>", _viewer_controls()):
            assert re.search(r'aria-label="[^"]+"', button), button
            assert re.search(r'title="[^"]+"', button), button

    def test_a_key_is_named_where_there_is_one(self):
        controls = _viewer_controls()
        for action, key in (("play-trajectory", "Space"), ("prev-frame", "Left arrow"),
                            ("next-frame", "Right arrow"), ("reset-view", "(R)"),
                            ("fullscreen", "(F)")):
            button = re.search(rf'<button[^>]*data-action="{action}"[^>]*>', controls).group(0)
            assert key in button, action
        for end, key in (("first", "Home"), ("last", "End")):
            button = re.search(rf'<button[^>]*data-traj="{end}"[^>]*>', controls).group(0)
            assert key in button, end

    def test_drawn_and_coloured_are_lists(self):
        controls = _viewer_controls()
        assert "data-rep=" not in controls and 'data-color="' not in controls
        assert re.findall(r'<select id="viewer-rep"[\s\S]*?</select>', controls)[0].count("<option") == 6
        assert re.findall(r'<select id="viewer-color"[\s\S]*?</select>', controls)[0].count("<option") == 6


VIEWER = "window.FastMDXMoleculeViewer.STATE"


@pytest.fixture(scope="module")
def dashboard(tmp_path_factory):
    pytest.importorskip("playwright.sync_api")
    pytest.importorskip("mdtraj")
    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_the_drawing_scripts_run_in_a_browser import _write_study

    study = _write_study(tmp_path_factory.mktemp("toolbar") / "study")
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    yield session
    session.server.shutdown()


@pytest.fixture
def page(dashboard):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        opened = browser.new_page(viewport={"width": 1400, "height": 900})
        opened.set_default_timeout(60000)
        errors: list[str] = []
        opened.on("pageerror", lambda error: errors.append(str(error)))
        opened.errors = errors
        opened.goto(dashboard.url + "#viewer", wait_until="domcontentloaded")
        if not opened.evaluate("() => !!document.createElement('canvas').getContext('webgl')"):
            pytest.skip("this browser has no WebGL, so the viewer cannot render")
        opened.wait_for_function(f"() => window.FastMDXMoleculeViewer && {VIEWER}.model")
        opened.wait_for_selector("#trajectory-row:not([hidden])")
        yield opened
        browser.close()


def test_the_play_button_shows_what_pressing_it_does(page) -> None:
    play = page.locator('[data-action="play-trajectory"]')
    assert play.get_attribute("aria-label") == "Play"
    assert page.is_visible('[data-action="play-trajectory"] .when-stopped')
    play.click()
    page.wait_for_function(
        "() => document.querySelector('[data-action=\"play-trajectory\"]').hasAttribute('data-playing')")
    assert play.get_attribute("aria-label") == "Pause"
    assert page.is_visible('[data-action="play-trajectory"] .when-playing')
    assert not page.is_visible('[data-action="play-trajectory"] .when-stopped')
    play.click()
    page.wait_for_function(
        "() => !document.querySelector('[data-action=\"play-trajectory\"]').hasAttribute('data-playing')")
    assert play.get_attribute("aria-label") == "Play"
    assert page.errors == []


def test_backwards_says_it_is_on(page) -> None:
    reverse = page.locator('[data-traj="reverse"]')
    assert reverse.get_attribute("aria-pressed") == "false"
    reverse.click()
    page.wait_for_function(
        "() => document.querySelector('[data-traj=\"reverse\"]').getAttribute('aria-pressed') === 'true'")
    assert page.evaluate(f"() => {VIEWER}.playbackReverse") is True
    page.click('[data-action="play-trajectory"]')
    assert page.errors == []


def test_the_lists_draw_and_colour(page) -> None:
    page.select_option("#viewer-rep", "sticks")
    page.select_option("#viewer-color", "element")
    assert page.evaluate(f"() => {VIEWER}.representation") == "sticks"
    assert page.evaluate(f"() => {VIEWER}.colorMode") == "element"
    assert page.errors == []


def test_the_pocket_cutoff_is_said_in_nanometres_too(page) -> None:
    if not page.is_visible("#pocket-cutoff"):
        pytest.skip("no ligand tools for this structure")
    assert page.text_content("#pocket-cutoff-nm") == "(0.50 nm)"
    page.fill("#pocket-cutoff", "7.5")
    page.dispatch_event("#pocket-cutoff", "change")
    assert page.text_content("#pocket-cutoff-nm") == "(0.75 nm)"
    assert page.evaluate(f"() => {VIEWER}.pocketCutoff") == 7.5


def test_a_picture_is_saved_for_a_page_and_the_view_is_put_back(page) -> None:
    """Saved as the canvas stood, a picture was as wide as the view on the
    screen, about 900 pixels: a third of a double-column figure at 300 dpi.
    It is now drawn once at least 2,400 pixels across, and the view on the
    screen is as it was."""
    import struct

    before = page.evaluate(f"() => {{ const c = {VIEWER}.engine.plugin.canvas3d.webgl.gl.canvas; "
                           "return [c.width, c.height, c.clientWidth, window.devicePixelRatio]; }")
    with page.expect_download() as caught:
        page.click('[data-action="screenshot"]')
    saved = Path(caught.value.path()).read_bytes()
    assert saved[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", saved[16:24])
    assert width >= 2400 and abs(width / height - before[0] / before[1]) < 0.01
    after = page.evaluate(f"() => {{ const c = {VIEWER}.engine.plugin.canvas3d.webgl.gl.canvas; "
                          "return [c.width, c.height, c.clientWidth, window.devicePixelRatio]; }")
    assert after == before
    assert "2,400 pixels" in page.get_attribute('[data-action="screenshot"]', "title")
    assert page.errors == []
