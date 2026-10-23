"""A frame or a residue the page cannot give is said where a person sees it.

A chart point opens its trajectory frame, or selects its residue. Where the
trajectory or the structure that names the residues is a link out of the
study, it is not read, and the page says so: in the notice a sighted person
sees (and `#sr-live`, read out) for a frame, in a residue's hover box and
under a figure for a residue. A GUI too busy to prepare a frame says that
in the notice too.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_the_study_pages_read_only_the_study import _link

pytest.importorskip("mdtraj")

TOAST = "#dashboard-toast.show .dashboard-toast-said"


def _moved_out(root: Path, tmp_path: Path, relative: str) -> None:
    target = tmp_path / "outside" / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    (root / relative).rename(target)
    _link(root / relative, target)


def _page(pw, url: str, hash_: str):
    browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.set_default_timeout(60000)
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(url + hash_, wait_until="domcontentloaded")
    return browser, page, errors


def _seen(page, selector: str) -> str:
    """The text of ``selector`` as it is shown: visible, not only present."""
    locator = page.locator(selector)
    locator.wait_for(state="visible")
    return locator.inner_text()


def test_a_chart_s_structure_not_read_is_said_on_the_chart(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_a_chart_s_residue_is_read_held_to_the_study import FIGURES, _study, _write
    from tests.test_an_analysis_is_drawn_from_its_numbers import _figure

    root = _study(tmp_path)
    _write(root, *FIGURES["ss"])
    manifest = root / "analysis" / "analysis_manifest.json"
    record = json.loads(manifest.read_text(encoding="utf-8"))
    record["results"]["ss"] = {"status": "ok"}
    manifest.write_text(json.dumps(record), encoding="utf-8")
    _figure(root / "analysis" / "rmsf" / "rmsf.png")
    _figure(root / "analysis" / "ss" / "ss.png")
    _moved_out(root, tmp_path, "simulation/trajectory_topology.pdb")
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser, page, errors = _page(pw, session.url, "#analysis")
            svg = page.locator('.series-chart[data-analysis="rmsf"] svg')
            svg.wait_for(state="visible")
            svg.focus()
            page.keyboard.press("ArrowRight")
            tip = _seen(page, '.series-chart[data-analysis="rmsf"] .series-tip')
            note = _seen(page, '.analysis-card:has([data-figure="analysis/ss/ss.png"])'
                               ' .figure-chart .figure-note')
            browser.close()
    finally:
        session.server.shutdown()
    assert "simulation/trajectory_topology.pdb was not read" in tip, tip
    assert "no unique topology match" not in tip
    assert "simulation/trajectory_topology.pdb was not read" in note, note
    assert errors == []


def test_a_frame_of_a_trajectory_linked_out_is_said_not_read_where_it_is_seen(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import DashboardConfig, start_dashboard_session
    from fastmdxplora.gui.trajectory_frames import frames_info
    from tests.test_a_graph_opens_exact_source_frame import _click_chart, _series, _study

    root = _study(tmp_path / "study")
    _series(root)
    assert frames_info(root, most_frames=3)["playback_available"]
    _moved_out(root, tmp_path, "simulation/production.dcd")
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0,
                                     config=DashboardConfig(max_browser_frames=3))
    try:
        with sync_playwright() as pw:
            browser, page, errors = _page(pw, session.url, "#analysis")
            svg = page.locator('.series-chart[data-analysis="rmsd"] svg')
            svg.wait_for(state="visible")
            # A chart point: the Viewer is opened and the notice says why
            # the frame is not.
            _click_chart(svg, 0.7)
            from_the_chart = _seen(page, TOAST)
            page.wait_for_function("() => document.documentElement.dataset.page === 'viewer'")
            page.evaluate("() => document.getElementById('dashboard-toast')"
                          ".classList.remove('show')")
            # A frame asked for in the Viewer (a saved view's): the same.
            page.evaluate("() => window.dispatchEvent(new CustomEvent("
                          "'dashboard:trajectory-seek', {detail: {trajectoryFrame: 7}}))")
            in_the_viewer = _seen(page, TOAST)
            page.wait_for_function("() => document.getElementById('sr-live').textContent"
                                   ".includes('Frame 7 is unavailable')")
            read_out = page.text_content("#sr-live")
            browser.close()
    finally:
        session.server.shutdown()
    for said in (from_the_chart, in_the_viewer, read_out):
        assert "Frame 7 is unavailable" in said, said
        assert "simulation/production.dcd was not read" in said, said
        assert str(tmp_path) not in said
    assert errors == []


def test_a_busy_gui_says_so_where_it_is_seen(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui import trajectory_frames
    from fastmdxplora.gui.server import DashboardConfig, start_dashboard_session
    from tests.test_a_graph_opens_exact_source_frame import _study

    root = _study(tmp_path / "study")
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0,
                                     config=DashboardConfig(max_browser_frames=3))
    monkeypatch.setattr(trajectory_frames, "_PREPARING_WAIT_S", 0.05)
    viewer = "window.FastMDXMoleculeViewer"
    held = []
    try:
        with sync_playwright() as pw:
            browser, page, errors = _page(pw, session.url, "#viewer")
            if not page.evaluate("() => !!document.createElement('canvas').getContext('webgl')"):
                pytest.skip("this browser has no WebGL, so the viewer cannot render")
            page.wait_for_function(f"() => !!{viewer}")
            assert page.evaluate(f"async () => !!(await {viewer}.movie.frames())")
            held = [trajectory_frames._PREPARING.acquire(), trajectory_frames._PREPARING.acquire()]
            page.evaluate("() => window.dispatchEvent(new CustomEvent("
                          "'dashboard:trajectory-seek', {detail: {trajectoryFrame: 7}}))")
            said = _seen(page, TOAST)
            browser.close()
    finally:
        for _ in held:
            trajectory_frames._PREPARING.release()
        session.server.shutdown()
    assert "Frame 7 could not be shown" in said, said
    assert "other frames are being prepared; try again in a moment" in said, said
    assert errors == []


@pytest.mark.parametrize("width, scheme", [(1440, "light"), (390, "dark")])
def test_a_viewer_whose_trajectory_is_not_read_says_so_where_it_is_seen(tmp_path, width,
                                                                       scheme):
    """The Viewer of a finished study whose trajectory is linked out of it
    says, and goes on saying, that its frames are not played and why, where
    the transport would be; its overlay waits for nothing; Play (Space)
    says it in the notice."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import DashboardConfig, start_dashboard_session
    from tests.test_a_graph_opens_exact_source_frame import _study

    root = _study(tmp_path / "study")
    _moved_out(root, tmp_path, "simulation/production.dcd")
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0,
                                     config=DashboardConfig(max_browser_frames=3))
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": width, "height": 900},
                                    color_scheme=scheme)
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#viewer", wait_until="domcontentloaded")
            note = _seen(page, "#traj-loaded")
            page.wait_for_function(
                "() => document.getElementById('overlay-stage').textContent"
                " === 'frames not read'")
            stage = _seen(page, "#overlay-stage")
            buttons = page.locator('#trajectory-row [data-action="play-trajectory"]')
            hidden = not buttons.is_visible()
            wide = page.evaluate("() => document.documentElement.scrollWidth > innerWidth")
            webgl = page.evaluate("() => !!document.createElement('canvas').getContext('webgl')")
            played = None
            if webgl:
                page.keyboard.press(" ")
                played = _seen(page, TOAST)
            still = _seen(page, "#traj-loaded")
            browser.close()
    finally:
        session.server.shutdown()
    for said in (note, still):
        assert said.startswith("Not played: simulation/production.dcd was not read"), said
        assert str(tmp_path) not in said
    assert stage == "frames not read"
    assert hidden and not wide
    if played is not None:
        assert "Trajectory playback is not ready: simulation/production.dcd was not read" \
            in played, played
    assert errors == []


def _viewer_page(pw, url: str):
    browser, page, errors = _page(pw, url, "#viewer")
    if not page.evaluate("() => !!document.createElement('canvas').getContext('webgl')"):
        browser.close()
        pytest.skip("this browser has no WebGL, so the viewer cannot render")
    page.wait_for_function("() => !!window.FastMDXMoleculeViewer")
    return browser, page, errors


def test_a_refusal_is_read_out_once(tmp_path):
    """A frame refused is read out from `#sr-live` and shown in the notice,
    which is not read out again; the next notice is read out as ever."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import DashboardConfig, start_dashboard_session
    from tests.test_a_graph_opens_exact_source_frame import _study

    root = _study(tmp_path / "study")
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0,
                                     config=DashboardConfig(max_browser_frames=3))
    said = ".dashboard-toast-said"
    try:
        with sync_playwright() as pw:
            browser, page, errors = _viewer_page(pw, session.url)
            page.evaluate("() => window.dispatchEvent(new CustomEvent("
                          "'dashboard:trajectory-seek', {detail: {trajectoryFrame: -1}}))")
            shown = _seen(page, TOAST)
            read_out = page.text_content("#sr-live")
            quiet = page.get_attribute(f"#dashboard-toast {said}", "aria-hidden")
            page.evaluate("() => window.FastMDXDashboard.toast('Saved.')")
            page.wait_for_function(f"() => document.querySelector('{said}').textContent"
                                   " === 'Saved.'")
            heard = page.get_attribute(f"#dashboard-toast {said}", "aria-hidden")
            browser.close()
    finally:
        session.server.shutdown()
    assert shown == read_out == "The selected source frame is not a non-negative integer."
    assert quiet == "true" and heard is None
    assert errors == []


def test_a_frame_whose_frames_cannot_be_loaded_is_said_where_it_is_seen(tmp_path):
    """The frames said ready, and then not sent (gone between the two): a
    frame asked for says it could not be shown, in the notice."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import DashboardConfig, start_dashboard_session
    from tests.test_a_graph_opens_exact_source_frame import _study

    root = _study(tmp_path / "study")
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0,
                                     config=DashboardConfig(max_browser_frames=3))
    try:
        with sync_playwright() as pw:
            browser, page, errors = _viewer_page(pw, session.url)
            for sent in ("**/structure/frames.dcd*", "**/structure/frames-topology.pdb*",
                         "**/api/frames-pieces*"):
                page.route(sent, lambda route: route.fulfill(status=404, body="Not found"))
            page.evaluate("() => window.dispatchEvent(new CustomEvent("
                          "'dashboard:trajectory-seek', {detail: {trajectoryFrame: 7}}))")
            shown = _seen(page, TOAST)
            browser.close()
    finally:
        session.server.shutdown()
    assert shown == "Frame 7 could not be shown: the frames could not be loaded.", shown


def test_frames_pieces_linked_out_are_said_not_read_in_the_viewer(tmp_path):
    """The frames' pieces linked out of the study are not read, and the
    frames are played whole: the notice says so as the frames load."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session
    from fastmdxplora.gui.trajectory_frames import frames_info
    from tests.test_a_graph_opens_exact_source_frame import _study

    root = _study(tmp_path / "study")
    assert frames_info(root)["playback_available"]
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _link(root / "simulation" / "frames_pieces", elsewhere)
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser, page, errors = _page(pw, session.url, "#viewer")
            if not page.evaluate("() => !!document.createElement('canvas').getContext('webgl')"):
                pytest.skip("this browser has no WebGL, so the viewer cannot render")
            viewer = "window.FastMDXMoleculeViewer"
            page.wait_for_function(f"() => {viewer} && {viewer}.STATE.engine"
                                   f" && {viewer}.STATE.playbackPayload")
            page.evaluate(f"async () => {{ await {viewer}.movie.frames(); }}")
            page.wait_for_function("() => document.getElementById('sr-live').textContent"
                                   ".includes('frames_pieces was not read')")
            # In the notice, which shows it for a while (`showToast`).
            said = page.text_content("#dashboard-toast .dashboard-toast-said")
            played = page.evaluate(f"() => {viewer}.STATE.playbackPayload.playback_available")
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert said.startswith("The frames are played whole: simulation/frames_pieces was not read")
    assert played is True
    assert list(elsewhere.iterdir()) == []
