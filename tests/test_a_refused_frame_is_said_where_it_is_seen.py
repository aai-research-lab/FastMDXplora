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
