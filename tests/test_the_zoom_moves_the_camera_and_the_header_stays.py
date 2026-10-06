"""The zoom moves the camera, a measurement is labelled beside its line, the
molecule can be shown on white, and a page's header stays where it is.

The zoom buttons changed Mol*'s focus radius, which is the depth it fogs
and clips at, so the molecule faded and came back rather than coming nearer.
A distance's label was two angstroms high in a heavy box, on the line it
measured. The page header was placed under the page's top padding and so
scrolled up by that much before it stopped, on every page.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from tests import viewer_hooks as hooks

pytest.importorskip("playwright.sync_api")

STATE = "window.FastMDXMoleculeViewer.STATE"


@pytest.fixture(scope="module")
def study(tmp_path_factory) -> Path:
    from tests.test_the_cartoon_is_dssp_of_each_frame import _helical_study

    return _helical_study(tmp_path_factory.mktemp("zoom") / "study", analyse=False)


@pytest.fixture(scope="module")
def page(study):
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            opened = browser.new_page(viewport={"width": 1440, "height": 900})
            opened.set_default_timeout(60000)
            opened.errors = []
            opened.on("pageerror", lambda error: opened.errors.append(str(error)))
            opened.goto(session.url + "#viewer", wait_until="domcontentloaded")
            if not opened.evaluate("() => !!document.createElement('canvas')"
                                   ".getContext('webgl')"):
                pytest.skip("this browser has no WebGL, so the viewer cannot render")
            opened.wait_for_function(f"() => window.FastMDXMoleculeViewer && {STATE}.model")
            opened.session_url = session.url
            yield opened
            browser.close()
    finally:
        session.server.shutdown()


def _at_rest(page):
    """Once the camera's move has run its course: a frame can take a second
    to render in software."""
    page.wait_for_timeout(100)
    page.wait_for_function(f"() => !{STATE}.engine.plugin.canvas3d.camera.transition.inTransition")
    return page.evaluate(f"() => {STATE}.engine.cameraSnapshot()")


def _distance(camera) -> float:
    return math.dist(camera["position"], camera["target"])


def test_zooming_in_brings_the_camera_nearer(page):
    before = _at_rest(page)
    hooks.tool(page, "side-view")
    page.click('[data-cam="zoom-in"]')
    nearer = _at_rest(page)
    page.click('[data-cam="zoom-out"]')
    further = _at_rest(page)
    assert _distance(nearer) == pytest.approx(_distance(before) / 1.2, rel=1e-3)
    assert _distance(further) == pytest.approx(_distance(nearer) / 0.8, rel=1e-3)
    # The same view, nearer: what it looks at and the depth it fogs at are kept.
    assert nearer["target"] == pytest.approx(before["target"])
    assert nearer["radius"] == pytest.approx(before["radius"])


def test_a_distance_is_labelled_beside_its_line(page):
    page.evaluate(f"() => {STATE}.engine.measure([10, 400])")
    params = page.evaluate(f"""() => {{
        const cell = {STATE}.engine.plugin.managers.structure.measurement.state.distances[0];
        const p = cell.transform.params;
        return {{size: p.textSize, attachment: p.attachment, x: p.offsetX, y: p.offsetY}};
    }}""")
    page.evaluate(f"() => {STATE}.engine.clearMeasurements()")
    assert params["size"] < 1.5 and params["attachment"] == "bottom-left"
    assert params["x"] > 0 and params["y"] > 0


def test_the_molecule_is_shown_on_white_and_back(page):
    # On the dark scheme, whose ground is dark until white is chosen.
    page.evaluate("() => window.FastMDXFrame.applyTheme('dark', false)")
    hooks.tool(page, "side-view")
    page.select_option("#viewer-ground", "white")
    white = page.evaluate(f"() => [{STATE}.engine.background, {STATE}.ground]")
    framed = page.get_attribute("#viewer-canvas-frame", "data-ground")
    page.select_option("#viewer-ground", "dark")
    dark = page.evaluate(f"() => [{STATE}.engine.background, {STATE}.ground]")
    assert white == [0xFFFFFF, "white"] and framed == "light"
    assert dark[1] == "dark" and dark[0] != 0xFFFFFF


def test_the_publication_look_is_its_own_and_gives_the_ground_back(page):
    """The ground and the publication look were two buttons that both
    made the ground white. The ground is chosen in one place; the look
    adds outlines and shading on white, and gives the ground back."""
    page.evaluate("() => window.FastMDXFrame.applyTheme('dark', false)")
    hooks.tool(page, "side-view")
    assert page.locator('[data-action="background"]').count() == 0
    page.select_option("#viewer-ground", "dark")
    page.click('[data-action="publication"]')
    on = page.evaluate(f"() => [{STATE}.engine.background, {STATE}.ground]")
    framed = page.get_attribute("#viewer-canvas-frame", "data-ground")
    page.click('[data-action="publication"]')
    off = page.evaluate(f"() => [{STATE}.engine.background, {STATE}.ground]")
    assert on == [0xFFFFFF, "dark"] and framed == "light"
    assert off[1] == "dark" and off[0] != 0xFFFFFF
    assert page.get_attribute("#viewer-canvas-frame", "data-ground") == "dark"


def test_a_view_keeps_its_ground():
    from fastmdxplora.gui.saved_views import _checked

    camera = {"position": [0, 0, 50], "target": [0, 0, 0], "up": [0, 1, 0]}
    assert _checked({"camera": camera, "ground": "white"})["ground"] == "white"
    assert "ground" not in _checked({"camera": camera, "ground": "grey"})


@pytest.mark.parametrize("name", ["viewer", "overview", "analysis", "files", "config"])
def test_a_page_s_header_stays_where_it_is(page, name):
    page.evaluate(f"() => window.FastMDXDashboard.navigate('{name}')")
    page.wait_for_timeout(500)
    tops = page.evaluate("""() => {
        const header = document.querySelector('.page:not([hidden]) .page-header');
        let box = header.parentElement;
        while (box && !(box.scrollHeight > box.clientHeight
               && ['auto', 'scroll'].includes(getComputedStyle(box).overflowY))) {
            box = box.parentElement;
        }
        box = box || document.scrollingElement;
        const tops = [];
        for (const y of [0, 10, 30, 120]) {
            box.scrollTop = y;
            tops.push(Math.round(header.getBoundingClientRect().top));
        }
        box.scrollTop = 0;
        return tops;
    }""")
    assert len(set(tops)) == 1, tops
    assert page.errors == []
