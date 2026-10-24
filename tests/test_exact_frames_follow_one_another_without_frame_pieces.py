"""Two exact frames in a row on the whole-DCD path.

Where the browser is given its frames as one DCD (no `/api/frames-pieces`),
the page's frame list holds the exact frame shown beside the sample. A
second exact frame was placed after that merged list, one place past the
frames the viewer held, and was not shown: the Viewer stayed on the first.
Read here from the Viewer's own coordinates: frame k has its CA at x = k
angstroms.
"""

from __future__ import annotations

import pytest

from tests.test_a_graph_opens_exact_source_frame import _series, _study


def test_a_second_exact_frame_is_shown_after_the_first(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import DashboardConfig, start_dashboard_session

    root = _study(tmp_path / "study")
    _series(root)
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0,
                                      config=DashboardConfig(max_browser_frames=3))
    at = ("f => { const atom = window.FastMDXMoleculeViewer?.STATE.engine?.find({atom: 'CA'})[0];"
          " return atom && Math.abs(atom.x - f) < 0.02; }")
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            # The whole-DCD path: no frames in pieces.
            page.route("**/api/frames-pieces*", lambda route: route.fulfill(status=404, body="{}"))
            page.add_init_script(
                "try { localStorage.setItem('fmx.viewerPlaybackOpen', '1'); } catch (e) {}")
            page.goto(session.url + "#viewer", wait_until="domcontentloaded")
            if not page.evaluate("() => !!document.createElement('canvas').getContext('webgl')"):
                pytest.skip("this browser has no WebGL, so the viewer cannot render")
            page.wait_for_function("() => !!window.FastMDXMoleculeViewer")
            assert page.evaluate("async () => !!(await window.FastMDXMoleculeViewer.movie.frames())")
            signature = page.evaluate(
                "() => window.FastMDXMoleculeViewer.STATE.playbackPayload.source_signature")
            seek = """([frame, signature]) => window.dispatchEvent(new CustomEvent(
                'dashboard:trajectory-seek', {detail: {trajectoryFrame: frame, sourceSignature: signature}}))"""
            page.evaluate(seek, [7, signature])
            page.wait_for_function(at, arg=7.0, timeout=90000)
            pieces = page.evaluate("() => window.FastMDXMoleculeViewer.STATE.framesPieces")
            page.evaluate(seek, [8, signature])
            page.wait_for_function(at, arg=8.0, timeout=90000)
            shown = page.evaluate("() => { const s = window.FastMDXMoleculeViewer.STATE;"
                                  " return [s.exactFrame.frame, s.playbackFrameIndices,"
                                  " s.engine.frameCount()]; }")
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert pieces is None
    frame, indices, count = shown
    # The sample of three and the one exact frame, not the earlier one too.
    assert frame == 8 and 8 in indices and 7 not in indices and count == len(indices) == 4
