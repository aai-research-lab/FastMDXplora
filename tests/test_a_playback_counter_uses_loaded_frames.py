"""Playback counts use the loaded frame position, not an integrator step."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("mdtraj")


def _write_history(root: Path, count: int = 200) -> None:
    from fastmdxplora.gui.live_frames import write_live_frame

    simulation = root / "simulation"
    for index in range(count):
        step = 163_000 + index * 1_000
        pdb = "".join(
            f"ATOM  {atom + 1:5d}  CA  ALA A{atom + 1:4d}    "
            f"{1.0 * atom + 0.01 * index:8.3f}{0.0:8.3f}{0.0:8.3f}  1.00  0.00           C\n"
            for atom in range(5)
        )
        write_live_frame(
            simulation,
            pdb_text=pdb + "END\n",
            frame_index=step,
            stage="production",
            simulation_time_ns=0.326 + 0.002 * index,
            archive=True,
        )
    simulation.mkdir(parents=True, exist_ok=True)
    (simulation / "live_status.json").write_text(
        json.dumps({"status": "running", "stage": "production"}), encoding="utf-8"
    )


def test_history_payload_separates_recorded_sequence_step_and_time(tmp_path):
    from fastmdxplora.gui.trajectory_frames import frames_info

    root = tmp_path / "study"
    _write_history(root)
    payload = frames_info(root)

    assert payload["source_kind"] == "live-history"
    assert payload["n_frames_total"] == payload["n_frames_browser"] == 200
    assert payload["frame_indices"] == list(range(200))
    assert payload["simulation_steps"] == [163_000 + 1_000 * index for index in range(200)]
    assert payload["frame_times_ns"][0] == pytest.approx(0.326)
    assert payload["frame_times_ns"][-1] == pytest.approx(0.724)


@pytest.mark.parametrize("reject, supersede", [(False, False), (True, False), (False, True)],
                         ids=["delayed-render", "failed-render", "superseded-render"])
def test_the_counter_announces_only_a_frame_the_renderer_committed(tmp_path, reject, supersede):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_a_graph_opens_exact_source_frame import _study

    session = start_dashboard_session(output=str(_study(tmp_path / "study", frame_count=3)),
                                      host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            try:
                page.goto(session.url + "#viewer", wait_until="domcontentloaded")
                page.locator("#viewer-under-fold").evaluate("e => { e.open = true; }")
                page.evaluate("async () => window.FastMDXMoleculeViewer.loadPlayback("
                              "await (await fetch('/api/frames-info')).json())")
                page.wait_for_function("() => window.FastMDXMoleculeViewer.STATE.playbackLoaded")
                assert page.locator("#traj-current").inner_text() == "1"
                page.evaluate("""() => {
                    const engine = window.FastMDXMoleculeViewer.STATE.engine;
                    // Mol* is the external rendering boundary. Keep the real
                    // adapter and geometry, but hold its next state commit.
                    const build = engine.plugin.build.bind(engine.plugin);
                    engine.plugin.build = () => {
                        const update = build();
                        const commit = update.commit.bind(update);
                        update.commit = (...args) => {
                            engine.plugin.build = build;
                            window.__frameCommit = new Promise((resolve, reject) => {
                                window.__finishFrameCommit = fail => fail
                                    ? reject(new Error('synthetic render failure'))
                                    : resolve(commit(...args));
                            });
                            return window.__frameCommit;
                        };
                        return update;
                    };
                    window.__shownFrames = [];
                    window.addEventListener('dashboard:frame-shown', e =>
                        window.__shownFrames.push(e.detail.index));
                }""")
                if reject:
                    page.locator('#trajectory-row [data-traj="last"]').click()
                else:
                    page.locator("#traj-slider").evaluate(
                        "e => { e.value = '2'; e.dispatchEvent(new Event('input')); }")
                page.wait_for_function("() => !!window.__finishFrameCommit")
                page.evaluate("() => { const d = window.FastMDXDashboard;"
                              " d.applyPlayback(d.state.playbackData); }")
                assert page.evaluate("() => window.FastMDXMoleculeViewer.STATE.engine.frame()") == 0
                assert page.locator("#traj-current").inner_text() == "1"
                assert page.locator("#traj-slider").input_value() == "0"
                assert page.locator("#overlay-frame").inner_text() == "frame 1 / 3"
                assert page.evaluate("() => window.__shownFrames") == []
                if supersede:
                    page.evaluate("""() => {
                        window.__newFrame = window.FastMDXMoleculeViewer.movie.showFrame(1);
                    }""")
                page.evaluate("""async fail => {
                    window.__finishFrameCommit(fail);
                    await window.__frameCommit.catch(() => {});
                    if (window.__newFrame) await window.__newFrame;
                }""", reject)
                if reject:
                    page.wait_for_function("() => document.getElementById('sr-live')"
                                           ".textContent.includes('could not be shown')")
                    assert page.locator("#traj-current").inner_text() == "1"
                    assert page.locator("#overlay-frame").inner_text() == "frame 1 / 3"
                    assert page.evaluate("() => window.__shownFrames") == []
                else:
                    expected = 1 if supersede else 2
                    page.wait_for_function("n => document.getElementById('traj-current')"
                                           ".textContent === String(n + 1)", arg=expected)
                    assert page.evaluate("() => window.FastMDXMoleculeViewer.STATE.engine.frame()") == expected
                    assert page.locator("#overlay-frame").inner_text() == f"frame {expected + 1} / 3"
                    assert page.evaluate("() => window.FastMDXMoleculeViewer.STATE.engine"
                                         ".find({atom: 'CA'})[0].x") == pytest.approx(float(expected), abs=0.02)
                    assert page.evaluate("() => window.__shownFrames") == [expected]
            finally:
                page.evaluate("() => window.__finishFrameCommit?.(true)")
                browser.close()
    finally:
        session.server.shutdown()


def test_live_dashboard_counter_and_transport_use_the_same_loaded_frame(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    root = tmp_path / "study"
    _write_history(root)
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#viewer", wait_until="domcontentloaded")
            page.locator("#viewer-under-fold").evaluate("element => { element.open = true; }")
            page.wait_for_function(
                "() => window.FastMDXDashboard?.state.playbackAvailable"
                " && document.getElementById('traj-total').textContent === '200'",
                timeout=30_000,
            )
            # Before the player renders a trajectory frame, the toolbar has
            # no current playback position. Opening the first frame explicitly
            # makes the overlay and toolbar describe the same loaded frame.
            if not page.evaluate("() => window.FastMDXMoleculeViewer?.STATE.playbackLoaded"):
                assert page.locator("#traj-current").inner_text() == "—"
                assert page.locator("#traj-step").inner_text() == "—"
                assert page.locator("#traj-simtime").inner_text() == "—"
            page.locator('#trajectory-row [data-traj="first"]').click()
            page.wait_for_function(
                "() => window.FastMDXMoleculeViewer?.STATE.playbackLoaded"
                " && document.getElementById('traj-current').textContent === '1'"
                " && document.getElementById('overlay-frame').textContent === 'frame 1 / 200'"
                " && document.getElementById('overlay-step').textContent === 'step 163,000'",
                timeout=60_000,
            )
            assert page.locator("#traj-step").inner_text() == "step 163,000"
            assert page.locator("#traj-simtime").inner_text() == "time 0.326 ns"
            assert page.locator("#overlay-frame").inner_text() == "frame 1 / 200"
            assert page.locator("#overlay-step").inner_text() == "step 163,000"
            assert page.locator("#overlay-simtime").inner_text() == "sim time 0.326 ns"
            assert page.evaluate("() => window.FastMDXMoleculeViewer.STATE.engine"
                                 ".find({atom: 'CA'})[0].x") == pytest.approx(0.0, abs=0.006)

            page.click('.ctl-btn[data-action="next-frame"]')
            page.wait_for_function(
                "() => window.FastMDXMoleculeViewer?.STATE.playbackLoaded"
                " && document.getElementById('traj-current').textContent === '2'",
                timeout=60_000,
            )
            page.locator("#traj-slider").evaluate(
                "element => { element.value = element.max; element.dispatchEvent(new "
                "Event('input')); }"
            )
            page.wait_for_function(
                "() => document.getElementById('traj-current').textContent === '200'"
                " && document.getElementById('traj-total').textContent === '200'"
                " && document.getElementById('traj-step').textContent === 'step 362,000'"
                " && document.getElementById('traj-simtime').textContent === 'time 0.724 ns'",
                timeout=60_000,
            )
            page.wait_for_function(
                "() => window.FastMDXMoleculeViewer.STATE.engine.frame() === 199"
            )
            assert page.locator("#overlay-frame").inner_text() == "frame 200 / 200"
            assert page.locator("#overlay-step").inner_text() == "step 362,000"
            assert page.evaluate("() => window.FastMDXMoleculeViewer.STATE.engine"
                                 ".find({atom: 'CA'})[0].x") == pytest.approx(1.99, abs=0.006)

            page.locator("#traj-slider").evaluate(
                "element => { element.value = '42'; element.dispatchEvent(new Event('input')); }"
            )
            page.wait_for_function(
                "() => document.getElementById('traj-current').textContent === '43'"
            )
            page.click('#trajectory-row [data-action="prev-frame"]')
            page.wait_for_function(
                "() => document.getElementById('traj-current').textContent === '42'"
            )
            page.click('#trajectory-row [data-traj="first"]')
            page.wait_for_function(
                "() => document.getElementById('traj-current').textContent === '1'"
            )
            page.click('#trajectory-row [data-traj="last"]')
            page.wait_for_function(
                "() => document.getElementById('traj-current').textContent === '200'"
            )

            page.locator("#traj-speed").select_option("4")
            assert page.evaluate("() => window.FastMDXMoleculeViewer.STATE.playbackSpeed") == 4
            page.locator("#trajectory-row [data-traj='first']").click()
            page.locator("#trajectory-row [data-action='play-trajectory']").click()
            page.wait_for_function(
                "() => Number(document.getElementById('traj-current').textContent) >= 4",
                timeout=10_000,
            )
            page.locator("#trajectory-row [data-action='play-trajectory']").click()
            paused_at = page.locator("#traj-current").inner_text()
            page.wait_for_timeout(300)
            assert page.locator("#traj-current").inner_text() == paused_at
            page.locator("#traj-loop").check()
            page.locator("#traj-slider").evaluate(
                "element => { element.value = element.max; element.dispatchEvent(new "
                "Event('input')); }"
            )
            page.locator("#trajectory-row [data-action='play-trajectory']").click()
            page.wait_for_function(
                "() => Number(document.getElementById('traj-current').textContent) < 200",
                timeout=10_000,
            )
            page.locator("#trajectory-row [data-action='play-trajectory']").click()
            # The real pointer click may arrive after another timer tick. The
            # loop must cross the boundary and Pause must hold the frame it
            # actually reached, rather than racing a particular display value.
            assert not page.evaluate("() => window.FastMDXMoleculeViewer.STATE.playbackPlaying")
            loop_paused_at = page.locator("#traj-current").inner_text()
            assert 1 <= int(loop_paused_at) < 200
            page.wait_for_timeout(300)
            assert page.locator("#traj-current").inner_text() == loop_paused_at
            assert errors == []
            browser.close()
    finally:
        session.server.shutdown()


def test_single_frame_playback_is_available_but_does_not_start_a_timer(tmp_path):
    md = pytest.importorskip("mdtraj")
    np = pytest.importorskip("numpy")
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    root = tmp_path / "study"
    simulation = root / "simulation"
    setup = root / "setup"
    simulation.mkdir(parents=True)
    setup.mkdir()
    topology = md.Topology()
    chain = topology.add_chain()
    for index in range(5):
        residue = topology.add_residue("ALA", chain, resSeq=index + 1)
        topology.add_atom("CA", md.element.carbon, residue)
    trajectory = md.Trajectory(np.zeros((1, 5, 3), dtype=np.float32), topology)
    trajectory[0].save_pdb(str(simulation / "trajectory_topology.pdb"))
    trajectory.save_dcd(str(simulation / "production.dcd"))
    (simulation / "live_status.json").write_text(
        json.dumps({"status": "completed", "stage": "production"}), encoding="utf-8"
    )
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.goto(session.url + "#viewer", wait_until="domcontentloaded")
            page.locator("#viewer-under-fold").evaluate("element => { element.open = true; }")
            page.wait_for_function(
                "() => window.FastMDXDashboard?.state.playbackAvailable"
                " && document.getElementById('traj-total').textContent === '1'",
                timeout=30_000,
            )
            assert page.locator("#traj-current").inner_text() == "—"
            assert page.locator("#trajectory-row [data-action='prev-frame']").is_disabled()
            assert page.locator("#trajectory-row [data-action='next-frame']").is_disabled()
            assert not page.locator("#trajectory-row [data-action='play-trajectory']").is_disabled()
            page.locator("#trajectory-row [data-action='play-trajectory']").click()
            page.wait_for_function("() => window.FastMDXMoleculeViewer?.STATE.playbackLoaded",
                                   timeout=60_000)
            assert page.evaluate("() => window.FastMDXMoleculeViewer.STATE.playbackFrames") == 1
            assert page.evaluate("() => window.FastMDXMoleculeViewer.STATE.playbackTimer") is None
            assert not page.evaluate("() => window.FastMDXMoleculeViewer.STATE.playbackPlaying")
            assert page.locator("#traj-current").inner_text() == "1"
            assert page.locator("#traj-total").inner_text() == "1"
            browser.close()
    finally:
        session.server.shutdown()


def test_empty_trajectory_keeps_transport_hidden_and_at_zero_of_zero(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(tmp_path / "empty"), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.goto(session.url + "#viewer", wait_until="domcontentloaded")
            page.wait_for_function("() => window.FastMDXDashboard?.state.playbackData !== null",
                                   timeout=30_000)
            assert page.locator("#trajectory-row").is_hidden()
            assert page.locator("#traj-current").inner_text() == "0"
            assert page.locator("#traj-total").inner_text() == "0"
            browser.close()
    finally:
        session.server.shutdown()


def test_switching_studies_reconciles_playback_counts_and_position(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_a_graph_opens_exact_source_frame import _study

    large = _study(tmp_path / "large", frame_count=200)
    _study(tmp_path / "short", frame_count=3)
    _study(tmp_path / "single", frame_count=1)
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "manifest.json").write_text("{}", encoding="utf-8")
    session = start_dashboard_session(output=str(large), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#viewer", wait_until="domcontentloaded")
            page.locator("#viewer-under-fold").evaluate("e => {e.open = true;}")
            page.wait_for_function(
                "() => document.getElementById('traj-total').textContent === '200'"
            )
            page.locator('#trajectory-row [data-traj="last"]').click()
            page.wait_for_function(
                "() => document.getElementById('traj-current').textContent === '200'"
            )
            for name, count in (("short", 3), ("single", 1), ("empty", 0), ("large", 200)):
                page.evaluate("() => window.FastMDXDashboard.navigate('studies')")
                page.evaluate("path => window.FastMDXStudies.load(path)", str(tmp_path))
                page.locator(f'.study-card[data-path$="{name}"] .study-open').click()
                page.wait_for_function(
                    "name => document.getElementById('study-folder')?.textContent.trim() === name",
                    arg=name)
                page.evaluate("() => window.FastMDXDashboard.navigate('viewer')")
                page.wait_for_function(
                    "n => document.getElementById('traj-total').textContent === String(n)",
                    arg=count,
                )
                assert page.locator("#traj-current").inner_text() == ("—" if count else "0")
                assert page.locator("#traj-slider").input_value() == "0"
                assert page.locator("#traj-step").inner_text() == "—"
                assert page.locator("#traj-simtime").inner_text() == "—"
                if count:
                    page.locator("#viewer-under-fold").evaluate("e => {e.open = true;}")
                    page.locator('#trajectory-row [data-traj="last"]').click()
                    page.wait_for_function(
                        "n => document.getElementById('traj-current').textContent === String(n)",
                        arg=count,
                    )
                else:
                    assert page.locator("#trajectory-row").is_hidden()
            assert errors == []
            browser.close()
    finally:
        session.server.shutdown()
