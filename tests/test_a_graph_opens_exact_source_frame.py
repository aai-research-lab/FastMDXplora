"""A plotted source frame opens those coordinates, even when it was not sampled.

The browser's bounded playback is deliberately only frames 0, 5 and 10.
Frame 7 remains a source identity through the chart and is loaded as one
additional coordinate frame when selected.
"""

from __future__ import annotations

import json
import struct
import time
import urllib.request
import zlib
from pathlib import Path

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")


def _study(root: Path, *, frame_count: int = 11, residues: int = 1) -> Path:
    simulation = root / "simulation"
    simulation.mkdir(parents=True)
    topology = md.Topology()
    chain = topology.add_chain()
    atoms_per_residue = ("N", "CA", "C", "O", "CB")
    for residue_index in range(residues):
        residue = topology.add_residue("ALA", chain, resSeq=residue_index + 1)
        for name in atoms_per_residue:
            topology.add_atom(name, md.element.carbon, residue)
    n_atoms = topology.n_atoms
    xyz = np.zeros((frame_count, n_atoms, 3), dtype=np.float32)
    xyz[:, :, 0] = np.arange(frame_count, dtype=np.float32)[:, None] * 0.1
    xyz[:, :, 1] = np.arange(n_atoms, dtype=np.float32)[None, :] * 0.025
    xyz[:, :, 2] = 0.1
    trajectory = md.Trajectory(xyz, topology)
    trajectory[0].save_pdb(str(simulation / "trajectory_topology.pdb"))
    trajectory.save_dcd(str(simulation / "production.dcd"))
    (simulation / "live_status.json").write_text(json.dumps(
        {"status": "completed", "stage": "production"}), encoding="utf-8")
    (simulation / "simulation_parameters.json").write_text(json.dumps({
        "parameters": {"integrator": "verlet", "timestep_fs": 2.0},
        "resolved": {"trajectory_interval_steps": 500,
                     "production_steps": frame_count * 500},
    }), encoding="utf-8")
    return root


def _series(root: Path, *, first: int = 0, stride: int = 1, rows: int = 11) -> None:
    analysis = root / "analysis"
    series = analysis / "rmsd"
    series.mkdir(parents=True)
    (analysis / "analysis_manifest.json").write_text(json.dumps({
        "load_kwargs": {"first": first, "stride": stride, "saving_interval_ps": 1.0},
        "resolved": {"trajectory": str(root / "simulation" / "production.dcd")},
        "results": {"rmsd": {"status": "done"}},
    }), encoding="utf-8")
    (series / "rmsd.dat").write_text(
        "# RMSD\n" + "".join(f"{0.01 * frame:.4f}\n" for frame in range(rows)),
        encoding="utf-8")
    (series / "options.json").write_text(json.dumps({"findings": {}}), encoding="utf-8")
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))
    pixels = b"".join(b"\0" + b"\x80\x80\x80" * 65 for _ in range(42))
    (series / "rmsd.png").write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 65, 42, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b""))


def test_exact_frame_routes_validate_source_signature_and_bounds(tmp_path):
    from fastmdxplora.gui.trajectory_frames import exact_frame_info

    root = _study(tmp_path / "study")
    info = exact_frame_info(root, 7, most_frames=3)
    assert info["ok"]
    assert info["frame_indices"] == [0, 5, 10]
    assert info["time_ns"] == pytest.approx(0.008)
    assert "frame=7" in info["exact_url"]
    assert "exact_frame=7" in info["dcd_url"]
    assert exact_frame_info(root, 7, most_frames=3,
                            source_signature="a different trajectory")["ok"] is False
    assert exact_frame_info(root, -1, most_frames=3)["ok"] is False
    assert exact_frame_info(root, 11, most_frames=3)["ok"] is False
    assert exact_frame_info(tmp_path / "missing", 0, most_frames=3)["ok"] is False


def test_exact_xtc_and_dcd_fallback_hold_the_requested_coordinates(tmp_path):
    from fastmdxplora.gui.trajectory_frames import (
        exact_frame_xtc,
        exact_frames_dcd,
        frames_info,
    )

    root = _study(tmp_path / "study")
    info = frames_info(root, most_frames=3)
    exact, details = exact_frame_xtc(root, 7, most_frames=3,
                                     source_signature=info["source_signature"])
    assert details["ok"] and exact
    xtc = tmp_path / "exact.xtc"
    xtc.write_bytes(exact)
    read = md.load_xtc(str(xtc), top=str(root / "simulation" / "frames_topology.pdb"))
    assert read.n_frames == 1
    assert read.xyz[0, 1, 0] == pytest.approx(0.7, abs=0.001)

    combined, fallback = exact_frames_dcd(root, 7, most_frames=3,
                                         source_signature=info["source_signature"])
    assert fallback["ok"] and combined
    dcd = tmp_path / "combined.dcd"
    dcd.write_bytes(combined)
    read = md.load_dcd(str(dcd), top=str(root / "simulation" / "frames_topology.pdb"))
    assert read.n_frames == 4
    assert read.xyz[-1, 1, 0] == pytest.approx(0.7, abs=1e-5)


@pytest.mark.parametrize("to", ["first", "start"])
def test_exact_frame_uses_the_viewers_backbone_alignment(tmp_path, to):
    from fastmdxplora.gui.trajectory_frames import (
        exact_frame_xtc,
        frames_info,
        superposed_frames,
    )

    root = _study(tmp_path / "study")
    sample = frames_info(root, most_frames=3)
    aligned = superposed_frames(root, "backbone", to=to, smooth=1)
    assert aligned["ok"]
    expected = md.load_dcd(str(root / "simulation" / aligned["file"]),
                           top=str(root / "simulation" / "frames_topology.pdb"))
    body, details = exact_frame_xtc(root, 7, most_frames=3,
                                    source_signature=sample["source_signature"],
                                    on="backbone", to=to)
    assert details["ok"] and body
    output = tmp_path / f"exact-{to}.xtc"
    output.write_bytes(body)
    exact = md.load_xtc(str(output), top=str(root / "simulation" / "frames_topology.pdb"))
    assert exact.n_frames == 1
    assert np.allclose(exact.xyz[0], expected.xyz[0], atol=0.001)


def test_server_exact_frame_response_is_one_frame_and_signature_guarded(tmp_path):
    from fastmdxplora.gui.server import DashboardConfig, start_dashboard_session

    root = _study(tmp_path / "study")
    _series(root)
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0,
                                     config=DashboardConfig(max_browser_frames=3))
    try:
        base = session.url.rstrip("/")
        info = json.loads(urllib.request.urlopen(base + "/api/frames-info").read())
        metadata = json.loads(urllib.request.urlopen(
            base + f"/api/exact-frame?frame=7&signature={info['source_signature']}").read())
        assert metadata["ok"] and metadata["frame_indices"] == [0, 5, 10]
        started = time.perf_counter()
        response = urllib.request.urlopen(base + metadata["exact_url"], timeout=30)
        body = response.read()
        elapsed = time.perf_counter() - started
        assert response.status == 200
        assert len(body) == int(response.headers["Content-Length"])
        assert 0 < len(body) < 4096
        assert elapsed >= 0
        stale = urllib.request.urlopen(base + "/api/exact-frame?frame=7&signature=old")
        assert json.loads(stale.read())["ok"] is False
        non_ascii = urllib.request.urlopen(base + "/api/exact-frame?frame=%C2%B2")
        assert json.loads(non_ascii.read())["ok"] is False
    finally:
        session.server.shutdown()


@pytest.mark.parametrize("route, key", [
    ("/api/exact-frame", "frame"),
    ("/structure/exact-frame.xtc", "frame"),
    ("/structure/frames.dcd", "exact_frame"),
])
@pytest.mark.parametrize("invalid", ["oversized", "repeated", "empty"])
def test_exact_frame_routes_refuse_unreadable_or_ambiguous_indices(tmp_path, route, key, invalid):
    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(_study(tmp_path / "study")),
                                      host="127.0.0.1", port=0)
    try:
        base = session.url.rstrip("/")
        info = json.loads(urllib.request.urlopen(base + "/api/frames-info").read())
        raw = {"oversized": "9" * 5000, "repeated": f"1&{key}=2", "empty": ""}[invalid]
        url = base + route + f"?{key}={raw}&signature={info['source_signature']}"
        if route.startswith("/api/"):
            with urllib.request.urlopen(url) as response:
                assert json.loads(response.read())["ok"] is False
        else:
            with pytest.raises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(url)
            assert caught.value.code == 400
        with urllib.request.urlopen(base + "/api/exact-frame?frame=1") as response:
            assert json.loads(response.read())["ok"] is True
    finally:
        session.server.shutdown()


def _click_chart(svg, fraction, *, left=64, right=28):
    """Resolve the current SVG after redraw and wait until it receives clicks."""
    svg.click(trial=True)
    size = svg.evaluate("el => ({width:el.clientWidth, height:el.clientHeight})")
    svg.click(position={"x": left + (size["width"] - left - right) * fraction,
                        "y": size["height"] / 2})


def test_a_chart_click_shows_exact_frame_coordinates_and_playback_order(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui import trajectory_frames
    from fastmdxplora.gui.server import DashboardConfig, start_dashboard_session

    root = _study(tmp_path / "study")
    _series(root)
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0,
                                     config=DashboardConfig(max_browser_frames=3))
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.add_init_script(
                "try { localStorage.setItem('fmx.viewerPlaybackOpen', '1'); } catch (e) {}")
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#analysis", wait_until="domcontentloaded")
            if not page.evaluate("() => !!document.createElement('canvas').getContext('webgl')"):
                pytest.skip("this browser has no WebGL, so the viewer cannot render")
            page.wait_for_selector('.series-chart[data-analysis="rmsd"] svg')
            payload = json.loads(urllib.request.urlopen(
                session.url.rstrip("/") + "/api/frames-info").read())
            assert payload["frame_indices"] == [0, 5, 10]
            chart = page.locator('.series-chart[data-analysis="rmsd"] svg')
            with page.expect_request("**/api/exact-frame?frame=7*"):
                _click_chart(chart, 0.7)
            page.wait_for_function(
                "() => window.FastMDXMoleculeViewer?.STATE.exactFrame?.frame === 7",
                timeout=60000)
            page.wait_for_function(
                "() => window.FastMDXMoleculeViewer.STATE.engine"
                ".find({atom: 'CA'})[0]?.x > 6.98", timeout=90000)
            # The counter is said once the frame is rendered, a moment after
            # the atoms have moved.
            page.wait_for_function(
                "() => document.getElementById('traj-current').textContent === '3'")
            got = page.evaluate(
                "() => ({frame: document.getElementById('traj-current').textContent,"
                " slider: document.getElementById('traj-slider').value,"
                " sources: window.FastMDXMoleculeViewer.STATE.playbackFrameIndices,"
                " engine: window.FastMDXMoleculeViewer.STATE.playbackEngineIndices,"
                " x: window.FastMDXMoleculeViewer.STATE.engine.find({atom:'CA'})[0].x})"
            )
            assert got["frame"] == "3"
            assert got["sources"] == [0, 5, 7, 10]
            assert got["engine"] == [0, 1, 3, 2]
            assert got["x"] == pytest.approx(7.0, abs=0.02)
            # The counter's total is one more than the frames sent: said so.
            assert "and frame 7 read from the trajectory" in page.text_content("#traj-loaded")
            assert page.evaluate("() => window.FastMDXMoleculeViewer.viewNow().frame") == 7
            assert page.evaluate("() => window.FastMDXMoleculeViewer.currentViewHints().frame") == 7
            saved = page.evaluate("""async () => {
              const response = await fetch('/api/views', {method:'POST',
                headers:{'content-type':'application/json'}, body:JSON.stringify({
                  action:'save', name:'Exact source frame',
                  view:window.FastMDXMoleculeViewer.viewNow()})});
              return response.json();
            }""")
            assert saved["ok"]
            saved_view = next(view for view in saved["views"] if view["name"] == "Exact source frame")
            assert saved_view.get("frame_source") is True
            # The next slider step is source frame 10 and the previous one is 5.
            page.locator("#traj-slider").evaluate(
                "el => { el.value = '3'; el.dispatchEvent(new Event('input')); }"
            )
            page.wait_for_function(
                "() => document.getElementById('traj-current').textContent === '4'"
            )
            assert page.evaluate("() => window.FastMDXMoleculeViewer.viewNow().frame") == 10
            page.locator("#traj-slider").evaluate(
                "el => { el.value = '1'; el.dispatchEvent(new Event('input')); }"
            )
            page.wait_for_function(
                "() => document.getElementById('traj-current').textContent === '2'"
            )
            assert page.evaluate("view => window.FastMDXMoleculeViewer.showView(view)", saved_view)
            page.wait_for_function("() => window.FastMDXMoleculeViewer.currentViewHints().frame === 7")
            assert page.evaluate("() => window.FastMDXMoleculeViewer.STATE.engine"
                                 ".find({atom:'CA'})[0].x") == pytest.approx(7.0, abs=0.02)
            # Kept with the trajectory it was named in: in another, it is
            # said and not shown at the same number.
            assert saved_view["frame_signature"] == payload["source_signature"]
            assert page.evaluate("view => window.FastMDXMoleculeViewer.showView(view)",
                                 {**saved_view, "frame_signature": "elsewhere.dcd:1:2"}) is False
            assert "has since changed" in page.text_content("#sr-live")

            def select_chart_frame(frame: int) -> None:
                page.evaluate("() => window.FastMDXDashboard.navigate('analysis')")
                page.wait_for_function("() => document.documentElement.dataset.page === 'analysis'")
                page.wait_for_selector('.series-chart[data-analysis="rmsd"] svg', state="visible")
                svg = page.locator('.series-chart[data-analysis="rmsd"] svg')
                _click_chart(svg, frame / 10)

            select_chart_frame(0)
            page.wait_for_function(
                "() => document.getElementById('traj-current').textContent === '1'"
            )
            page.wait_for_function("() => window.FastMDXMoleculeViewer.STATE.engine"
                ".find({atom:'CA'})[0]?.x < 0.02")
            select_chart_frame(10)
            page.wait_for_function(
                "() => document.getElementById('traj-current').textContent === '4'"
            )
            page.wait_for_function("() => window.FastMDXMoleculeViewer.STATE.engine"
                ".find({atom:'CA'})[0]?.x > 9.98")

            original_info = trajectory_frames.exact_frame_info
            delayed_frames: set[int] = set()
            def exact_info_with_delay(*args, **kwargs):
                frame = args[1] if len(args) > 1 else kwargs.get("frame")
                if frame in delayed_frames:
                    time.sleep(0.7)
                return original_info(*args, **kwargs)
            monkeypatch.setattr(trajectory_frames, "exact_frame_info", exact_info_with_delay)
            delayed_frames.add(8)
            page.evaluate("() => window.FastMDXDashboard.navigate('analysis')")
            page.wait_for_function("() => document.documentElement.dataset.page === 'analysis'")
            page.wait_for_selector('.series-chart[data-analysis="rmsd"] svg', state="visible")
            svg = page.locator('.series-chart[data-analysis="rmsd"] svg')
            with page.expect_request("**/api/exact-frame?frame=8*"):
                _click_chart(svg, 0.8)
            page.evaluate("() => window.FastMDXDashboard.navigate('analysis')")
            page.wait_for_function("() => document.documentElement.dataset.page === 'analysis'")
            page.wait_for_selector('.series-chart[data-analysis="rmsd"] svg', state="visible")
            svg = page.locator('.series-chart[data-analysis="rmsd"] svg')
            _click_chart(svg, 0.9)
            page.wait_for_function(
                "() => window.FastMDXMoleculeViewer.STATE.exactFrame?.frame === 9", timeout=60000
            )
            page.wait_for_timeout(1000)
            assert page.evaluate("() => window.FastMDXMoleculeViewer.STATE.exactFrame.frame") == 9
            assert page.evaluate(
                "() => window.FastMDXMoleculeViewer.STATE.engine.find({atom:'CA'})[0].x"
            ) == pytest.approx(9.0, abs=0.02)

            original_xtc = trajectory_frames.exact_frame_xtc
            def interrupted_frame(output_dir, frame, **kwargs):
                if frame == 6:
                    return None, {"ok": False, "reason": "simulated interrupted request"}
                return original_xtc(output_dir, frame, **kwargs)
            monkeypatch.setattr(trajectory_frames, "exact_frame_xtc", interrupted_frame)
            select_chart_frame(6)
            page.wait_for_function("() => document.getElementById('sr-live').textContent"
                ".includes('Frame 6 could not be shown')", timeout=30000)
            assert page.evaluate("() => window.FastMDXMoleculeViewer.STATE.exactFrame.frame") == 9

            delayed_frames.add(6)
            monkeypatch.setattr(trajectory_frames, "exact_frame_xtc", original_xtc)
            page.evaluate("() => window.FastMDXDashboard.navigate('analysis')")
            page.wait_for_function("() => document.documentElement.dataset.page === 'analysis'")
            page.wait_for_selector('.series-chart[data-analysis="rmsd"] svg', state="visible")
            svg = page.locator('.series-chart[data-analysis="rmsd"] svg')
            with page.expect_request("**/api/exact-frame?frame=6*"):
                _click_chart(svg, 0.6)
            page.evaluate("() => window.dispatchEvent(new CustomEvent('dashboard:run-changed',"
                "{detail: {first: false}}))")
            page.wait_for_timeout(1100)
            assert page.evaluate("() => window.FastMDXMoleculeViewer.STATE.exactFrame") is None
            assert errors == []
            browser.close()
    finally:
        session.server.shutdown()


def test_the_per_frame_panels_follow_the_frames_played_around_an_inserted_frame(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import DashboardConfig, start_dashboard_session
    from tests import viewer_hooks as hooks

    root = _study(tmp_path / "study", residues=4)
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0,
                                     config=DashboardConfig(max_browser_frames=3))
    viewer, rama = "window.FastMDXMoleculeViewer", "window.FastMDXRamachandran"
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#viewer", wait_until="domcontentloaded")
            if not page.evaluate("() => !!document.createElement('canvas').getContext('webgl')"):
                pytest.skip("this browser has no WebGL, so the viewer cannot render")
            page.wait_for_function(f"() => {viewer} && {viewer}.STATE.playbackPayload")
            assert page.evaluate(f"async () => !!(await {viewer}.movie.frames())")
            page.evaluate("""() => { window.__shown = [];
                window.addEventListener('dashboard:frame-shown',
                    (e) => window.__shown.push(e.detail)); }""")
            page.evaluate("() => window.dispatchEvent(new CustomEvent("
                          "'dashboard:trajectory-seek', {detail: {trajectoryFrame: 7}}))")
            page.wait_for_function(f"() => {viewer}.STATE.exactFrame?.frame === 7"
                                   f" && {viewer}.STATE.playbackFrameIndices.join() === '0,5,7,10'")
            page.wait_for_function("() => !document.getElementById('side-rama').hidden")
            hooks.tool(page, "side-rama")
            page.wait_for_function(f"() => {rama}.state.data")

            def show(index: int) -> None:
                page.locator("#traj-slider").evaluate(
                    "(el, i) => { el.value = String(i); el.dispatchEvent(new Event('input')); }",
                    index)
                page.wait_for_function(
                    "(i) => document.getElementById('traj-current').textContent"
                    " === String(i + 1)", arg=index)

            # Source frame 10, after the inserted frame 7, is the third played.
            show(3)
            page.wait_for_function(f"() => {rama}.state.frame === 2")
            assert page.evaluate("() => window.__shown.at(-1)")["playedIndex"] == 2
            # The inserted frame is none of the frames played: no angles of
            # a neighbour are plotted for it.
            show(2)
            page.wait_for_function(f"() => {rama}.state.frame === null")
            assert page.evaluate("() => window.__shown.at(-1)")["playedIndex"] is None
            assert "not one of the frames played" in page.text_content("#rama-note")
            # A panel naming a frame played opens that one, not the slider's.
            page.evaluate("() => window.dispatchEvent(new CustomEvent("
                          "'dashboard:trajectory-seek', {detail: {playedFrame: 2}}))")
            page.wait_for_function(
                f"() => {viewer}.STATE.playbackFrameIndices["
                "Number(document.getElementById('traj-slider').value)] === 10")
            # Back at a frame played, the note no longer says otherwise.
            page.wait_for_function(f"() => {rama}.state.frame === 2")
            page.wait_for_function("() => !document.getElementById('rama-note')"
                                   ".textContent.includes('not one of the frames played')")
            assert page.text_content("#rama-note") == page.evaluate(f"() => {rama}.state.data.said")
            assert errors == []
            browser.close()
    finally:
        session.server.shutdown()


def test_a_drag_along_the_series_follows_the_frames_and_reads_where_it_ends(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import DashboardConfig, start_dashboard_session

    root = _study(tmp_path / "study", frame_count=200)
    _series(root, rows=200)
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0,
                                     config=DashboardConfig(max_browser_frames=5))
    viewer = "window.FastMDXMoleculeViewer"
    asked: list[str] = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.add_init_script(
                "try { localStorage.setItem('fmx.viewerPlaybackOpen', '1'); } catch (e) {}")
            page.set_default_timeout(60000)
            page.on("request", lambda r: asked.append(r.url) if "exact" in r.url else None)
            page.goto(session.url + "#viewer", wait_until="domcontentloaded")
            if not page.evaluate("() => !!document.createElement('canvas').getContext('webgl')"):
                pytest.skip("this browser has no WebGL, so the viewer cannot render")
            page.wait_for_function(f"() => !!{viewer}")
            assert page.evaluate(f"async () => !!(await {viewer}.movie.frames())")
            played = page.evaluate(f"() => {viewer}.STATE.playbackFrameIndices.slice()")
            assert len(played) == 5
            page.wait_for_selector("#frame-series:not([hidden]) .frame-series-line")
            svg = page.locator(".frame-series-svg")
            svg.scroll_into_view_if_needed()
            box = svg.bounding_box()
            y = box["y"] + box["height"] / 2
            shown = (f"() => {viewer}.STATE.playbackFrameIndices["
                     "Number(document.getElementById('traj-slider').value)]")
            page.mouse.move(box["x"] + 60, y)
            page.mouse.down()
            seen = set()
            # Across the track's plot, which ends 10 px before its right edge.
            for i in range(60):
                page.mouse.move(box["x"] + 60 + i * (box["width"] - 72) / 59, y)
                page.wait_for_timeout(15)
                seen.add(page.evaluate(shown))
            # The drag went on over every frame played, asking for none.
            assert asked == []
            assert set(played) - {played[0]} <= seen
            page.mouse.up()
            page.wait_for_function(f"() => {viewer}.STATE.exactFrame !== null")
            final = page.evaluate(f"() => {viewer}.STATE.exactFrame.frame")
            page.wait_for_function(f"(f) => ({shown})() === f", arg=final)
            assert final not in played
            # One frame read, where the drag ended.
            assert len([url for url in asked if "/api/exact-frame" in url]) == 1
            browser.close()
    finally:
        session.server.shutdown()


def _two_chains(root: Path) -> list[int]:
    """Four frames of two chains holding each other (as in
    test_what_holds_the_chains_is_shown); the frames the browser plays."""
    from fastmdxplora.gui.trajectory_frames import frames_info
    from tests.test_what_holds_the_chains_is_shown import _system

    (root / "simulation").mkdir(parents=True)
    topology, base, _ = _system()
    frames = np.array([base + (0.01 * k, 0, 0) for k in range(4)], dtype=np.float32)
    trajectory = md.Trajectory(frames, topology)
    trajectory[0].save_pdb(str(root / "simulation" / "trajectory_topology.pdb"))
    trajectory.save_dcd(str(root / "simulation" / "production.dcd"))
    (root / "simulation" / "live_status.json").write_text(json.dumps(
        {"status": "completed", "stage": "production"}), encoding="utf-8")
    (root / "setup").mkdir()
    (root / "setup" / "topology.pdb").write_bytes(
        (root / "simulation" / "trajectory_topology.pdb").read_bytes())
    return frames_info(root, most_frames=3)["frame_indices"]


def test_between_chains_says_a_frame_read_from_the_trajectory_was_not_played(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import DashboardConfig, start_dashboard_session

    root = tmp_path / "study"
    played = _two_chains(root)
    unplayed = next(frame for frame in range(4) if frame not in played)
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0,
                                     config=DashboardConfig(max_browser_frames=3))
    viewer = "window.FastMDXMoleculeViewer"
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.set_default_timeout(60000)
            page.goto(session.url + "#viewer", wait_until="domcontentloaded")
            if not page.evaluate("() => !!document.createElement('canvas').getContext('webgl')"):
                pytest.skip("this browser has no WebGL, so the viewer cannot render")
            page.wait_for_function(f"() => {viewer} && {viewer}.STATE.playbackPayload")
            assert page.evaluate(f"async () => !!(await {viewer}.movie.frames())")
            page.wait_for_function("() => window.FastMDXChainContacts.state.data")
            page.evaluate("(f) => window.dispatchEvent(new CustomEvent("
                          "'dashboard:trajectory-seek', {detail: {trajectoryFrame: f}}))",
                          unplayed)
            page.wait_for_function(f"(f) => {viewer}.STATE.exactFrame?.frame === f", arg=unplayed)
            page.wait_for_function("() => document.getElementById('chain-contacts-now')"
                                   ".textContent.includes('not one of the frames played')")
            marked = page.evaluate("""() => ({
                unplayed: document.getElementById('chain-contacts-list')
                    .hasAttribute('data-unplayed'),
                present: document.querySelectorAll('.chain-contact.is-present').length})""")
            browser.close()
    finally:
        session.server.shutdown()
    # Neither present nor absent: said not to be one of the frames played.
    assert marked == {"unplayed": True, "present": 0}


@pytest.mark.parametrize("pieces", [True, False], ids=["xtc-pieces", "dcd"])
def test_the_frames_fitted_again_hold_the_exact_frame_fitted_the_same_way(tmp_path, pieces):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import DashboardConfig, start_dashboard_session

    root = _study(tmp_path / "study")
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0,
                                     config=DashboardConfig(max_browser_frames=3))
    viewer = "window.FastMDXMoleculeViewer"
    state = f"""() => {{ const s = {viewer}.STATE;
        return {{count: s.engine.frameCount(), frames: s.playbackFrames,
            superposed: s.superposed, url: s.superposedUrl,
            exact: s.exactFrame && s.exactFrame.frame, sources: s.playbackFrameIndices,
            shown: s.playbackFrameIndices[Number(document.getElementById('traj-slider').value)],
            x: s.engine.find({{atom: 'CA'}})[0].x,
            said: document.getElementById('sr-live').textContent}}; }}"""
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.set_default_timeout(60000)
            if not pieces:
                page.route("**/api/frames-pieces*", lambda route: route.fulfill(
                    status=200, content_type="application/json", body='{"ok": false}'))
            page.goto(session.url + "#viewer", wait_until="domcontentloaded")
            if not page.evaluate("() => !!document.createElement('canvas').getContext('webgl')"):
                pytest.skip("this browser has no WebGL, so the viewer cannot render")
            page.wait_for_function(f"() => !!{viewer}")
            assert page.evaluate(f"async () => !!(await {viewer}.movie.frames())")
            assert page.evaluate(f"() => {viewer}.STATE.engine.framesBytes") == (
                "xtc" if pieces else "dcd")
            page.evaluate("() => window.dispatchEvent(new CustomEvent("
                          "'dashboard:trajectory-seek', {detail: {trajectoryFrame: 7}}))")
            page.wait_for_function(f"() => {viewer}.STATE.exactFrame?.frame === 7")
            # Fitted on the backbone: read again with the exact frame among them.
            page.evaluate(f"() => {viewer}.superpose('backbone')")
            page.wait_for_function(f"() => {viewer}.STATE.superposedUrl"
                                   f" && {viewer}.STATE.exactFrame?.frame === 7"
                                   f" && {viewer}.STATE.engine.frameCount() === 4")
            page.wait_for_function(f"() => ({state})().shown === 7")
            fitted = page.evaluate(state)
            # Smoothed: averages of the frames sent, so the exact frame goes.
            page.evaluate(f"() => {{ {viewer}.STATE.smoothedOver = 3;"
                          f" return {viewer}.superpose('backbone'); }}")
            page.wait_for_function(f"() => {viewer}.STATE.exactFrame === null"
                                   f" && {viewer}.STATE.engine.frameCount() === 3")
            page.wait_for_function(f"() => ({state})().shown === 5")
            smoothed = page.evaluate(state)
            browser.close()
    finally:
        session.server.shutdown()
    assert fitted["sources"] == [0, 5, 7, 10] and fitted["frames"] == 4, fitted
    assert fitted["superposed"] == "backbone" and fitted["url"], fitted
    # Fitted onto the first frame, the exact frame lies where frame 0 does.
    assert fitted["x"] == pytest.approx(0.0, abs=0.05), fitted
    assert "could not be read again" not in fitted["said"]
    assert smoothed["sources"] == [0, 5, 10] and smoothed["frames"] == 3, smoothed
    assert "smoothed frames are averages" in smoothed["said"]


def test_a_frame_asked_for_and_then_superseded_changes_nothing(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    import time

    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui import trajectory_frames
    from fastmdxplora.gui.server import DashboardConfig, start_dashboard_session

    root = _study(tmp_path / "study")
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0,
                                     config=DashboardConfig(max_browser_frames=3))
    original = trajectory_frames.exact_frames_dcd

    def slow(output_dir, frame, **kwargs):
        if frame == 8:
            time.sleep(2.5)
        return original(output_dir, frame, **kwargs)

    monkeypatch.setattr(trajectory_frames, "exact_frames_dcd", slow)
    viewer = "window.FastMDXMoleculeViewer"
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.set_default_timeout(60000)
            # Frames as one DCD: the engine's coordinates are replaced whole.
            page.route("**/api/frames-pieces*", lambda route: route.fulfill(
                status=200, content_type="application/json", body='{"ok": false}'))
            page.goto(session.url + "#viewer", wait_until="domcontentloaded")
            if not page.evaluate("() => !!document.createElement('canvas').getContext('webgl')"):
                pytest.skip("this browser has no WebGL, so the viewer cannot render")
            page.wait_for_function(f"() => !!{viewer}")
            assert page.evaluate(f"async () => !!(await {viewer}.movie.frames())")
            page.evaluate("() => window.dispatchEvent(new CustomEvent("
                          "'dashboard:trajectory-seek', {detail: {trajectoryFrame: 7}}))")
            page.wait_for_function(f"() => {viewer}.STATE.exactFrame?.frame === 7")
            # Frame 8 is slow to come; a drag under the Viewer supersedes it.
            with page.expect_request("**/structure/frames.dcd?exact_frame=8*"):
                page.evaluate("() => window.dispatchEvent(new CustomEvent("
                              "'dashboard:trajectory-seek', {detail: {trajectoryFrame: 8}}))")
            page.evaluate("() => window.dispatchEvent(new CustomEvent("
                          "'dashboard:trajectory-seek', {detail: {trajectoryFrame: 0,"
                          " nearest: true}}))")
            page.wait_for_timeout(4000)
            at7 = page.evaluate(f"() => {viewer}.STATE.playbackFrameIndices.indexOf(7)")
            page.evaluate(f"(i) => {{ {viewer}.movie.showFrame(i); }}", at7)
            page.wait_for_function(f"(i) => {viewer}.STATE.engine.frame()"
                                   f" === {viewer}.STATE.playbackEngineIndices[i]", arg=at7)
            page.wait_for_timeout(500)
            got = page.evaluate(f"""() => ({{exact: {viewer}.STATE.exactFrame.frame,
                sources: {viewer}.STATE.playbackFrameIndices,
                x: {viewer}.STATE.engine.find({{atom: 'CA'}})[0].x}})""")
            browser.close()
    finally:
        session.server.shutdown()
    # Frame 7 is still frame 7: the superseded frame 8 was not put in its place.
    assert got["exact"] == 7 and got["sources"] == [0, 5, 7, 10], got
    assert got["x"] == pytest.approx(7.0, abs=0.02), got


def test_a_busy_gui_says_so_and_how_to_go_on(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui import trajectory_frames
    from fastmdxplora.gui.server import DashboardConfig, start_dashboard_session

    root = _study(tmp_path / "study")
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0,
                                     config=DashboardConfig(max_browser_frames=3))
    monkeypatch.setattr(trajectory_frames, "_PREPARING_WAIT_S", 0.05)
    viewer = "window.FastMDXMoleculeViewer"
    held = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.set_default_timeout(60000)
            page.goto(session.url + "#viewer", wait_until="domcontentloaded")
            if not page.evaluate("() => !!document.createElement('canvas').getContext('webgl')"):
                pytest.skip("this browser has no WebGL, so the viewer cannot render")
            page.wait_for_function(f"() => !!{viewer}")
            assert page.evaluate(f"async () => !!(await {viewer}.movie.frames())")
            held = [trajectory_frames._PREPARING.acquire(), trajectory_frames._PREPARING.acquire()]
            page.evaluate("() => window.dispatchEvent(new CustomEvent("
                          "'dashboard:trajectory-seek', {detail: {trajectoryFrame: 7}}))")
            page.wait_for_function("() => document.getElementById('sr-live').textContent"
                                   ".includes('Frame 7 could not be shown')")
            said = page.text_content("#sr-live")
            browser.close()
    finally:
        for _ in held:
            trajectory_frames._PREPARING.release()
        session.server.shutdown()
    assert "other frames are being prepared; try again in a moment" in said, said


def test_a_larger_exact_frame_remains_bounded(tmp_path):
    from fastmdxplora.gui.trajectory_frames import exact_frame_xtc, frames_info

    root = _study(tmp_path / "study", frame_count=101, residues=2000)
    info = frames_info(root, most_frames=3)
    started = time.perf_counter()
    body, details = exact_frame_xtc(root, 73, most_frames=3,
                                    source_signature=info["source_signature"])
    elapsed = time.perf_counter() - started
    assert details["ok"] and body
    assert info["frame_indices"] == [0, 50, 100]
    assert len(body) < (info["n_atoms"] * 12)
    output = tmp_path / "exact.xtc"
    output.write_bytes(body)
    exact = md.load_xtc(str(output), top=str(root / "simulation" / "frames_topology.pdb"))
    assert exact.n_frames == 1
    assert exact.xyz[0, 1, 0] * 10 == pytest.approx(73.0, abs=0.01)
    assert elapsed >= 0


def test_a_source_frame_scene_exports_that_frames_coordinates(tmp_path):
    from fastmdxplora.scenes import build_scene, write_scene

    root = _study(tmp_path / "study", frame_count=4001)
    (root / "setup").mkdir()
    (root / "setup" / "topology.pdb").write_bytes(
        (root / "simulation" / "trajectory_topology.pdb").read_bytes())
    scene = build_scene(root, {"frame": 7, "frame_source": True})
    assert scene["ok"] and scene["of"] == "frames"
    ca = next(line for line in scene["files"]["structure.pdb"].splitlines()
              if line.startswith("ATOM") and line[12:16].strip() == "CA")
    assert float(ca[30:38]) == pytest.approx(7.0, abs=0.002)
    assert scene["state"]["root"]["custom"]["fastmdxplora"]["view"]["frame_source"] is True
    refused = write_scene(root, "outside", {"frame": 4001, "frame_source": True})
    assert not refused["ok"]
    assert not (root / "scenes" / "outside.mvsx").exists()


def test_a_saved_source_frame_view_opens_at_that_frame_fitted_its_way(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import DashboardConfig, start_dashboard_session

    root = _study(tmp_path / "study")
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0,
                                     config=DashboardConfig(max_browser_frames=3))
    viewer = "window.FastMDXMoleculeViewer"
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.set_default_timeout(60000)
            page.goto(session.url + "#viewer", wait_until="domcontentloaded")
            if not page.evaluate("() => !!document.createElement('canvas').getContext('webgl')"):
                pytest.skip("this browser has no WebGL, so the viewer cannot render")
            page.wait_for_function(f"() => !!{viewer}")
            assert page.evaluate(f"async () => !!(await {viewer}.movie.frames())")
            page.evaluate("() => window.dispatchEvent(new CustomEvent("
                          "'dashboard:trajectory-seek', {detail: {trajectoryFrame: 7}}))")
            page.wait_for_function(f"() => {viewer}.STATE.exactFrame?.frame === 7")
            view = page.evaluate(f"() => {viewer}.viewNow()")
            assert view["frame"] == 7 and view["frame_source"] is True
            # The view as saved with the frames fitted on the backbone.
            opened = page.evaluate(f"(view) => {viewer}.showView(view)",
                                   {**view, "superposed": "backbone", "superposed_to": "first"})
            got = page.evaluate(f"""() => {{ const s = {viewer}.STATE;
                return {{superposed: s.superposed, url: s.superposedUrl,
                    exact: s.exactFrame && s.exactFrame.frame, count: s.engine.frameCount(),
                    shown: s.playbackFrameIndices[
                        Number(document.getElementById('traj-slider').value)],
                    x: s.engine.find({{atom: 'CA'}})[0].x}}; }}""")
            browser.close()
    finally:
        session.server.shutdown()
    assert opened is True
    assert got["superposed"] == "backbone" and got["url"], got
    assert got["exact"] == 7 and got["shown"] == 7 and got["count"] == 4, got
    # At its own coordinates, fitted onto the first frame as the view has it.
    assert got["x"] == pytest.approx(0.0, abs=0.05), got


def test_a_scene_of_a_changed_trajectory_says_why_with_its_code(tmp_path):
    from fastmdxplora.gui.trajectory_frames import frames_info
    from fastmdxplora.scenes import write_scene

    root = _study(tmp_path / "study")
    (root / "setup").mkdir()
    (root / "setup" / "topology.pdb").write_bytes(
        (root / "simulation" / "trajectory_topology.pdb").read_bytes())
    frames_info(root, most_frames=3)
    said = write_scene(root, "old", {"frame": 7, "frame_source": True,
                                     "frame_signature": "simulation/production.dcd:1:2"})
    assert said["ok"] is False and said["code"] == "analysis.frame.changed", said
