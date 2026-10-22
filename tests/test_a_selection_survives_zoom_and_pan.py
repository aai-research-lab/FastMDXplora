"""A point chosen by the keyboard is still the one Enter opens after a zoom.

A zoom or a move draws a plot again, and plot-tools gives the keyboard to
the new drawing. The keyboard's place lived in the old drawing, so the new
one began at the first point: on a cluster chart with source frames 100,
107, ..., 233, frame 156 chosen, then +, the view still showed frame 156
and Enter opened frame 100, which the view did not show.

The place is kept by the plot's host. A point still in view stays chosen;
one a move takes out of view gives way to the shown point nearest it, so
Enter never opens a frame the plot does not show. What opens is read from
the Viewer's own coordinates: frame k of the trajectory has its CA at
x = k angstroms.
"""

from __future__ import annotations

import numpy as np
import pytest

from tests.test_a_graph_opens_exact_source_frame import _series, _study

FRAMES = [100 + 7 * k for k in range(20)]


def _session(tmp_path):
    from fastmdxplora.gui.server import DashboardConfig, start_dashboard_session

    # 240 frames, the browser given three of them: every frame chosen
    # below is opened as an exact source frame, not one already played.
    root = _study(tmp_path / "study", frame_count=240)
    # The RMSD series loaded from frame 14 at a stride of 7: its source
    # frames are 98, 105, ..., 231.
    _series(root, first=14, stride=7, rows=20)
    return root, start_dashboard_session(output=str(root), host="127.0.0.1", port=0,
                                         config=DashboardConfig(max_browser_frames=3))


def _browser(pw, session):
    browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.add_init_script("try { localStorage.setItem('fmx.viewerPlaybackOpen', '1'); } catch (e) {}")
    page.set_default_timeout(60000)
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(session.url + "#analysis", wait_until="domcontentloaded")
    if not page.evaluate("() => !!document.createElement('canvas').getContext('webgl')"):
        browser.close()
        pytest.skip("this browser has no WebGL, so the viewer cannot render")
    page.wait_for_function("() => !!window.FastMDXFigures && !!window.FastMDXPlotTools")
    return browser, page, errors


def _plot(page, data):
    """A figure drawn on the Analysis page from its payload, as the page
    draws one."""
    page.evaluate("""data => {
        const host = document.createElement('div');
        host.id = 'kept-selection';
        host.className = 'figure-chart';
        host.style.width = '720px';
        document.querySelector('.page[data-page="analysis"]').appendChild(host);
        window.FastMDXFigures.plot(host, data);
    }""", data)
    svg = page.locator("#kept-selection svg")
    svg.scroll_into_view_if_needed()
    return svg


def _viewer_at(page, frame):
    """Wait until the Viewer shows source frame `frame`, by its coordinates."""
    page.wait_for_function(
        "f => { const atom = window.FastMDXMoleculeViewer?.STATE.engine?.find({atom: 'CA'})[0];"
        " return atom && Math.abs(atom.x - f) < 0.02; }", arg=float(frame), timeout=90000)
    return page.evaluate("() => window.FastMDXMoleculeViewer.STATE.engine.find({atom: 'CA'})[0].x")


def _back(page, svg_selector="#kept-selection svg"):
    page.evaluate("() => window.FastMDXDashboard.navigate('analysis')")
    page.wait_for_function("() => document.documentElement.dataset.page === 'analysis'")
    svg = page.locator(svg_selector)
    svg.scroll_into_view_if_needed()
    svg.focus()
    return svg


def _press(page, key, selector="#kept-selection svg", times=1):
    """A key on the plot; a zoom or a move draws it again, and the next key
    goes to the drawing that took its place."""
    for _ in range(times):
        page.locator(selector).focus()
        page.keyboard.press(key)
        page.wait_for_timeout(60)


def _view(page, host="#kept-selection"):
    return page.evaluate("h => { const v = window.FastMDXPlotTools.range() ||"
                         " document.querySelector(h)._view; return v ? [v.from ?? v.x[0], v.to ?? v.x[1]] : null; }",
                         host)


def _tip(page, host="#kept-selection"):
    return page.text_content(host + " .series-tip")


def test_a_cluster_chart_keeps_its_frame_through_zoom_and_gives_way_when_moved_off(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _, session = _session(tmp_path)
    data = {"ok": True, "kind": "labels", "title": "Conformational clustering (kmeans)",
            "x": list(range(20)), "x_label": "Frame", "frames": FRAMES, "linked": True,
            "labels": [k % 3 for k in range(20)], "clusters": [0, 1, 2], "y_label": "Cluster"}
    try:
        with sync_playwright() as pw:
            browser, page, errors = _browser(pw, session)
            _plot(page, data)
            _press(page, "Home")
            _press(page, "ArrowRight", times=8)
            chosen = _tip(page)
            _press(page, "+")
            page.wait_for_function("() => document.querySelector('#kept-selection').dataset.view")
            zoomed = _view(page)
            refocused = page.evaluate(
                "() => document.activeElement === document.querySelector('#kept-selection svg')")
            kept = _tip(page)
            label = page.get_attribute("#kept-selection svg", "aria-label")
            _press(page, "Enter")
            opened = _viewer_at(page, 156)

            # Moved right a quarter of the view: frame 156 (position 8) is
            # still shown, so it is still the one chosen.
            _back(page)
            _press(page, "ArrowRight")
            page.wait_for_function("v => window.FastMDXPlotTools.range().from > v", arg=zoomed[0])
            moved = _view(page)
            page.locator("#kept-selection svg").focus()
            still = _tip(page)
            # Home and End: the first and the last point shown.
            _press(page, "Home")
            first = _tip(page)
            _press(page, "End")
            last = _tip(page)
            # Moved back until the last point shown is out of view: the shown
            # point nearest it is chosen, and Enter opens that one.
            _press(page, "[", times=2)
            page.wait_for_function("v => window.FastMDXPlotTools.range().to < v", arg=moved[1] - 3)
            off = _view(page)
            page.locator("#kept-selection svg").focus()
            fallback = _tip(page)
            _press(page, "Enter")
            nearest = max(k for k in range(20) if k <= off[1] + 1e-9)
            opened_after = _viewer_at(page, FRAMES[nearest])
            browser.close()
    finally:
        session.server.shutdown()
    shown = [k for k in range(20) if moved[0] - 1e-9 <= k <= moved[1] + 1e-9]
    assert errors == []
    assert "frame 156" in chosen
    assert zoomed == pytest.approx([4.75, 14.25])
    assert refocused
    assert "frame 156" in kept, kept
    assert "Zoomed in: the arrows move the view" in label
    assert opened == pytest.approx(156.0, abs=0.02)
    assert 8 in shown and "frame 156" in still, (moved, still)
    assert f"frame {FRAMES[shown[0]]}" in first, (moved, first)
    assert f"frame {FRAMES[shown[-1]]}" in last, (moved, last)
    assert off[1] < shown[-1]
    assert f"frame {FRAMES[nearest]}" in fallback, (off, fallback)
    assert opened_after == pytest.approx(float(FRAMES[nearest]), abs=0.02)


def test_an_rmsd_matrix_keeps_its_row_and_column_through_zoom(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.figure_data import figure_payload

    root, session = _session(tmp_path)
    folder = root / "analysis" / "cluster"
    folder.mkdir(parents=True)
    # As the clustering writes it: the frames it loaded, counted from 0.
    np.savez(folder / "cluster_rmsd_matrix.npz", rmsd_nm=np.zeros((20, 20)),
             frames=np.arange(20), time_ns=np.arange(20) * 0.001)
    payload = figure_payload(root, "analysis/cluster/cluster_rmsd_matrix.png")
    # The trajectory's frames, through the analysis's first frame and stride.
    frames = payload["frames"]
    assert payload["ok"] and frames == [(14 + k) * 7 for k in range(20)]
    payload["linked"] = True
    try:
        with sync_playwright() as pw:
            browser, page, errors = _browser(pw, session)
            _plot(page, payload)
            _press(page, "Home")
            _press(page, "ArrowRight", times=8)
            _press(page, "ArrowDown", times=5)
            chosen = _tip(page)
            _press(page, "+")
            page.wait_for_function("() => document.querySelector('#kept-selection').dataset.view")
            kept = _tip(page)
            # Shift+Enter: the column's frame; Enter: the row's.
            page.locator("#kept-selection svg").focus()
            page.keyboard.press("Shift+Enter")
            column = _viewer_at(page, frames[8])
            _back(page)
            again = _tip(page)
            page.keyboard.press("Enter")
            row = _viewer_at(page, frames[5])
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert f"row frame {frames[5]}" in chosen and f"column frame {frames[8]}" in chosen, chosen
    assert f"row frame {frames[5]}" in kept and f"column frame {frames[8]}" in kept, kept
    assert f"row frame {frames[5]}" in again and f"column frame {frames[8]}" in again, again
    assert column == pytest.approx(float(frames[8]), abs=0.02)
    assert row == pytest.approx(float(frames[5]), abs=0.02)


def test_a_series_chart_keeps_its_point_through_zoom(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _, session = _session(tmp_path)
    rmsd = '.series-chart[data-analysis="rmsd"]'
    try:
        with sync_playwright() as pw:
            browser, page, errors = _browser(pw, session)
            page.wait_for_selector(rmsd + " .plot-tools")
            svg = rmsd + " svg"
            page.locator(svg).scroll_into_view_if_needed()
            _press(page, "Home", selector=svg)
            _press(page, "ArrowRight", selector=svg, times=8)
            chosen = _tip(page, rmsd)
            frames = page.evaluate(f"() => document.querySelector('{rmsd}').__seriesData.frames")
            _press(page, "+", selector=svg)
            page.wait_for_function(f"() => document.querySelector('{rmsd}').dataset.view")
            page.locator(svg).focus()
            kept = _tip(page, rmsd)
            _press(page, "Enter", selector=svg)
            opened = _viewer_at(page, 154)
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert frames[8] == 154
    assert "frame 154" in chosen, chosen
    assert "frame 154" in kept, kept
    assert opened == pytest.approx(154.0, abs=0.02)


def test_grouped_matrix_cells_stay_value_only_after_zoom(tmp_path):
    """A matrix whose cells average several frames names no single frame,
    zoomed or not: it takes no Enter and opens nothing."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _, session = _session(tmp_path)
    data = {"ok": True, "kind": "matrix", "title": "RMSD between frames",
            "values": [[0.0] * 12 for _ in range(12)], "extent": [-0.5, 47.5],
            "axis_label": "Frame", "label": "RMSD", "unit": "nm", "frames": None,
            "frame_groups": [{"first": 4 * k, "last": 4 * k + 3, "count": 4} for k in range(12)],
            "block": 4, "low": 0.0, "linked": True}
    try:
        with sync_playwright() as pw:
            browser, page, errors = _browser(pw, session)
            _plot(page, data)
            page.locator("#kept-selection svg").focus()
            page.keyboard.press("+")
            page.wait_for_function("() => document.querySelector('#kept-selection').dataset.view")
            role = page.get_attribute("#kept-selection svg", "role")
            requests: list[str] = []
            page.on("request", lambda r: requests.append(r.url) if "exact-frame" in r.url else None)
            page.locator("#kept-selection svg").focus()
            page.keyboard.press("Enter")
            page.wait_for_timeout(600)
            where = page.evaluate("() => document.documentElement.dataset.page")
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert role != "application"
    assert requests == [] and where == "analysis"


@pytest.mark.parametrize("kind", ["states", "lines", "projection"])
def test_the_other_charts_keep_their_place_through_zoom(tmp_path, kind):
    """The grid of secondary structure keeps its frame and its residue, the
    lines and the projection their frame, through a zoom that keeps them in
    view."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _, session = _session(tmp_path)
    x = list(range(20))
    if kind == "states":
        data = {"ok": True, "kind": "states", "title": "Secondary structure (DSSP)",
                "x": x, "x_label": "Frame", "frames": FRAMES, "linked": True,
                "residues": ["ALA1", "ALA2", "ALA3"], "entities": None, "y_label": "Residue",
                "states": ["HEC"] * 20,
                "classes": [{"code": "H", "label": "Helix"}, {"code": "E", "label": "Strand"},
                            {"code": "C", "label": "Coil"}]}
        keys = ["Home"] + ["ArrowRight"] * 8 + ["ArrowDown"]
        expect = ["frame 156", "residue ALA2"]
    elif kind == "lines":
        data = {"ok": True, "kind": "lines", "title": "Principal moments of inertia",
                "x": x, "frames": FRAMES, "x_label": "Frame", "y_label": "Moment", "unit": "",
                "series": [{"label": "I1", "y": [float(k) for k in x]}], "linked": True}
        keys = ["Home"] + ["ArrowRight"] * 8
        expect = ["frame 156"]
    else:
        # Points along a line, in time order: position k at (k, k).
        data = {"ok": True, "kind": "projection", "title": "PCA projection",
                "x": [float(k) for k in x], "y": [float(k) for k in x], "t": x,
                "t_label": "Frame", "frames": FRAMES, "axes": ["PC1", "PC2"], "linked": True}
        keys = ["Home"] + ["ArrowRight"] * 8
        expect = ["frame 156"]
    try:
        with sync_playwright() as pw:
            browser, page, errors = _browser(pw, session)
            _plot(page, data)
            for key in keys:
                _press(page, key)
            chosen = _tip(page)
            _press(page, "+")
            page.wait_for_function("() => document.querySelector('#kept-selection').dataset.view")
            page.locator("#kept-selection svg").focus()
            kept = _tip(page)
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    for said in expect:
        assert said in chosen, chosen
        assert said in kept, kept


def test_a_view_with_no_point_in_it_chooses_nothing_and_enter_opens_nothing(tmp_path):
    """Points with a gap between them, viewed inside the gap: no point is
    shown, so none is chosen, Home and End choose none, and Enter opens no
    frame; the chosen point comes back as the view does."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _, session = _session(tmp_path)
    positions = [0, 1, 2, 3, 4, 15, 16, 17, 18, 19]
    data = {"ok": True, "kind": "labels", "title": "Conformational clustering (kmeans)",
            "x": positions, "x_label": "Frame", "frames": [FRAMES[k] for k in positions],
            "linked": True, "labels": [k % 3 for k in positions], "clusters": [0, 1, 2],
            "y_label": "Cluster"}
    redraw = """view => { const host = document.getElementById('kept-selection');
        host._view = view; window.FastMDXFigures.plot(host, window.__keptData); }"""
    try:
        with sync_playwright() as pw:
            browser, page, errors = _browser(pw, session)
            page.evaluate("data => { window.__keptData = data; }", data)
            page.evaluate("""() => {
                const host = document.createElement('div');
                host.id = 'kept-selection';
                host.className = 'figure-chart';
                host.style.width = '720px';
                document.querySelector('.page[data-page="analysis"]').appendChild(host);
                window.FastMDXFigures.plot(host, window.__keptData);
            }""")
            page.locator("#kept-selection svg").scroll_into_view_if_needed()
            _press(page, "Home")
            _press(page, "ArrowRight", times=3)
            chosen = _tip(page)
            # Inside the gap: positions 6 to 13 hold no point.
            page.evaluate(redraw, {"x": [6, 13]})
            requests: list[str] = []
            page.on("request", lambda r: requests.append(r.url) if "exact-frame" in r.url else None)
            page.locator("#kept-selection svg").focus()
            empty_tip = page.is_hidden("#kept-selection .series-tip")
            for key in ("Home", "End", "Enter"):
                page.keyboard.press(key)
            page.wait_for_timeout(600)
            still_hidden = page.is_hidden("#kept-selection .series-tip")
            where = page.evaluate("() => document.documentElement.dataset.page")
            # The whole run again: the point chosen before is chosen again.
            page.evaluate(redraw, None)
            page.locator("#kept-selection svg").focus()
            back = _tip(page)
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert f"frame {FRAMES[3]}" in chosen, chosen
    assert empty_tip and still_hidden
    assert requests == [] and where == "analysis"
    # Nothing was chosen in the empty view, so the place falls back to the
    # point nearest the one chosen before it: position 3 itself.
    assert f"frame {FRAMES[3]}" in back, back


def test_a_matrix_column_moved_out_of_view_gives_way_and_its_row_stays(tmp_path):
    """The matrix's range is the page's time range on both axes. Moved along
    until the chosen column is out of view, the shown column nearest it is
    chosen; the row, still shown, is kept; Shift+Enter and Enter open those
    frames in the Viewer."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.figure_data import figure_payload

    root, session = _session(tmp_path)
    folder = root / "analysis" / "cluster"
    folder.mkdir(parents=True)
    np.savez(folder / "cluster_rmsd_matrix.npz", rmsd_nm=np.zeros((20, 20)),
             frames=np.arange(20), time_ns=np.arange(20) * 0.001)
    payload = figure_payload(root, "analysis/cluster/cluster_rmsd_matrix.png")
    frames = payload["frames"]
    payload["linked"] = True
    low, high = payload["extent"]
    centres = [low + (k + 0.5) * (high - low) / 20 for k in range(20)]
    try:
        with sync_playwright() as pw:
            browser, page, errors = _browser(pw, session)
            _plot(page, payload)
            _press(page, "Home")
            _press(page, "ArrowRight", times=8)
            _press(page, "ArrowDown", times=14)
            _press(page, "+")
            page.wait_for_function("() => document.querySelector('#kept-selection').dataset.view")
            zoomed = _view(page)
            # Moved right until column 8 has left the view.
            _press(page, "]", times=4)
            page.wait_for_function("c => window.FastMDXPlotTools.range().from > c",
                                   arg=centres[8])
            off = _view(page)
            page.locator("#kept-selection svg").focus()
            said = _tip(page)
            page.keyboard.press("Shift+Enter")
            nearest = min(k for k in range(20) if centres[k] >= off[0] - 1e-9)
            column = _viewer_at(page, frames[nearest])
            _back(page)
            page.keyboard.press("Enter")
            row = _viewer_at(page, frames[14])
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert zoomed[0] <= centres[8] <= zoomed[1] and zoomed[0] <= centres[14] <= zoomed[1]
    assert centres[8] < off[0] <= centres[14] <= off[1]
    assert f"column frame {frames[nearest]}" in said and f"row frame {frames[14]}" in said, said
    assert column == pytest.approx(float(frames[nearest]), abs=0.02)
    assert row == pytest.approx(float(frames[14]), abs=0.02)


def test_a_series_point_moved_out_of_view_gives_way_to_the_nearest_shown(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _, session = _session(tmp_path)
    rmsd = '.series-chart[data-analysis="rmsd"]'
    svg = rmsd + " svg"
    try:
        with sync_playwright() as pw:
            browser, page, errors = _browser(pw, session)
            page.wait_for_selector(rmsd + " .plot-tools")
            page.locator(svg).scroll_into_view_if_needed()
            _press(page, "Home", selector=svg)
            _press(page, "ArrowRight", selector=svg, times=3)
            data = page.evaluate(f"() => document.querySelector('{rmsd}').__seriesData")
            _press(page, "+", selector=svg)
            page.wait_for_function(f"() => document.querySelector('{rmsd}').dataset.view")
            # Moved right until the chosen point (index 3) has left the view.
            _press(page, "]", selector=svg, times=4)
            page.wait_for_function("x => window.FastMDXPlotTools.range().from > x", arg=data["x"][3])
            off = _view(page, rmsd)
            page.locator(svg).focus()
            said = _tip(page, rmsd)
            _press(page, "Enter", selector=svg)
            nearest = min(k for k in range(len(data["x"])) if data["x"][k] >= off[0] - 1e-9)
            opened = _viewer_at(page, data["frames"][nearest])
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert data["x"][3] < off[0]
    assert f"frame {data['frames'][nearest]}" in said, (off, said)
    assert opened == pytest.approx(float(data["frames"][nearest]), abs=0.02)
