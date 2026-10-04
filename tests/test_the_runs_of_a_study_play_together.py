"""The runs of a study, played together in one Viewer.

A study of several runs opened to an empty Viewer: each run's frames were
inside the run, and the root had no trajectory. Now the Viewer plays the
first run with a trajectory and renders the other runs of the same atoms
beside it, each in its colour, fitted on the backbone to the first run's
first frame, at the same source frames; a run of other atoms is left out,
and why is said.
"""

from __future__ import annotations

import gzip
import json
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")

DATA = Path(__file__).parent / "data" / "assemblies"
FRAMES = (6, 4, 7, 6)


def _replicas(root: Path) -> Path:
    """Trypsin with benzamidine run four times by seed: each run turned and
    moved apart as written, the second shorter, the fourth without its
    ligand (other atoms)."""
    from fastmdxplora.batch.explorer import BatchExplorer

    study = BatchExplorer(config_data={"systems": [{"system": "3PTB"}],
                                       "simulation": {"duration_ns": 1.0},
                                       "sweep": {"simulation.random_seed": [1, 2, 3, 4]}},
                          output_dir=str(root))
    (root / "runs").mkdir(parents=True)
    study._write_study_config()
    study._write_batch_manifest()
    raw = root.parent / "3PTB.pdb"
    raw.write_bytes(gzip.decompress((DATA / "3PTB.pdb.gz").read_bytes()))
    whole = md.load_pdb(str(raw))
    complex_ = whole.atom_slice(whole.topology.select("protein or resname BEN"))
    for i, (spec, n) in enumerate(zip(study.run_specs, FRAMES)):
        run = study._run_output_dir(spec)
        (run / "setup").mkdir(parents=True)
        (run / "simulation").mkdir()
        system = complex_ if i < 3 else complex_.atom_slice(complex_.topology.select("protein"))
        xyz = np.repeat(system.xyz, n, axis=0)
        xyz += np.random.default_rng(i).normal(0, 0.02, xyz.shape).astype(np.float32)
        angle = 0.6 * i
        turn = np.array([[np.cos(angle), -np.sin(angle), 0], [np.sin(angle), np.cos(angle), 0],
                         [0, 0, 1]], dtype=np.float32)
        xyz = xyz @ turn.T + np.array([1.5 * i, 0, 0], dtype=np.float32)
        # In a periodic box, as a run's frames are: each is made whole.
        trajectory = md.Trajectory(xyz, system.topology,
                                   unitcell_lengths=np.full((n, 3), 9.0, dtype=np.float32),
                                   unitcell_angles=np.full((n, 3), 90.0, dtype=np.float32))
        trajectory[0].save_pdb(str(run / "setup" / "topology.pdb"))
        trajectory[0].save_pdb(str(run / "simulation" / "trajectory_topology.pdb"))
        trajectory.save_dcd(str(run / "simulation" / "production.dcd"))
        (run / "simulation" / "live_status.json").write_text(json.dumps(
            {"status": "completed", "stage": "completed", "current_step": 500,
             "total_planned_steps": 500}), encoding="utf-8")
    return root


@pytest.fixture(scope="module")
def replicas(tmp_path_factory) -> Path:
    return _replicas(tmp_path_factory.mktemp("replicas") / "study")


def _get(url: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=120) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, b""


def test_the_runs_are_fitted_to_one_shared_frame(replicas):
    from fastmdxplora.gui.runs_together import RUN_COLOURS, TOGETHER, run_shown, runs_together

    assert run_shown(replicas) == replicas / "runs" / "s1__random-seed-1"
    said = runs_together(replicas, most_frames=2000)
    together = said["runs_together"]
    assert said["available"] and said["n_frames_browser"] == 6
    assert [(r["label"], r["colour"], r["frames"], r["main"]) for r in together["runs"]] == [
        ("random_seed 1", RUN_COLOURS[0], 6, True),
        ("random_seed 2", RUN_COLOURS[1], 4, False),
        # Read at the first run's source frames: its seventh is not shown.
        ("random_seed 3", RUN_COLOURS[2], 6, False)]
    assert together["excluded"] == [{"run_id": "s1__random-seed-4", "label": "random_seed 4",
                                     "reason": "Its atoms are not those of the run shown."}]
    assert together["fitted"].startswith("on the protein's backbone (")
    assert together["fitted"].endswith("to the first frame of random_seed 1")

    shown = replicas / "runs" / "s1__random-seed-1" / "simulation"
    first = md.load_dcd(str(shown / "frames.dcd"), top=str(shown / "frames_topology.pdb"))
    backbone = first.topology.select("protein and backbone")
    for index in (1, 2):
        fitted = md.load_dcd(str(replicas / TOGETHER / f"run_{index}.dcd"),
                             top=str(shown / "frames_topology.pdb"))
        # On the first run's first frame, within the noise the runs were
        # given; as written they sat a nanometre and more apart.
        assert md.rmsd(fitted, first, 0, atom_indices=backbone).max() < 0.06
        written = md.load_dcd(str(replicas / "runs" / f"s1__random-seed-{index + 1}"
                                  / "simulation" / "production.dcd"),
                              top=str(shown / "frames_topology.pdb"))
        apart = np.linalg.norm(written.xyz[0, backbone].mean(axis=0)
                               - first.xyz[0, backbone].mean(axis=0))
        assert apart > 1.0
        assert np.linalg.norm(fitted.xyz[0, backbone].mean(axis=0)
                              - first.xyz[0, backbone].mean(axis=0)) < 0.01

    # Written once: asked again, nothing is written again.
    stamp = (replicas / TOGETHER / "run_1.dcd").stat().st_mtime_ns
    again = runs_together(replicas, most_frames=2000)
    assert again["source_signature"] == said["source_signature"]
    assert (replicas / TOGETHER / "run_1.dcd").stat().st_mtime_ns == stamp


def test_a_study_of_one_run_and_runs_without_frames(replicas, tmp_path):
    import shutil

    from fastmdxplora.gui.runs_together import run_shown, runs_together

    one = replicas / "runs" / "s1__random-seed-1"
    assert runs_together(one, most_frames=2000) is None
    assert run_shown(one) is None
    # A run not yet started is left out, and why is said; with no run
    # written, nothing is played.
    copy = tmp_path / "copy"
    shutil.copytree(replicas, copy)
    shutil.rmtree(copy / "runs" / "s1__random-seed-3" / "simulation")
    excluded = runs_together(copy, most_frames=2000)["runs_together"]["excluded"]
    assert {"run_id": "s1__random-seed-3", "label": "random_seed 3",
            "reason": "It has no trajectory yet."} in excluded
    # A run of the same number of atoms named otherwise is not the run
    # shown's; nor is one whose trajectory cannot be read.
    third = copy / "runs" / "s1__random-seed-3" / "simulation"
    third.mkdir()
    shutil.copy(replicas / "runs" / "s1__random-seed-3" / "simulation" / "production.dcd", third)
    topology = (replicas / "runs" / "s1__random-seed-3" / "simulation"
                / "trajectory_topology.pdb").read_text()
    (third / "trajectory_topology.pdb").write_text(topology.replace(" BEN ", " BZM ", 1))
    (third / "live_status.json").write_text(json.dumps({"status": "completed"}))
    broken = copy / "runs" / "s1__random-seed-4" / "simulation"
    shutil.copy(replicas / "runs" / "s1__random-seed-1" / "simulation"
                / "trajectory_topology.pdb", broken / "trajectory_topology.pdb")
    (broken / "production.dcd").write_bytes(b"not a trajectory")
    excluded = {entry["run_id"]: entry["reason"]
                for entry in runs_together(copy, most_frames=2000)["runs_together"]["excluded"]}
    assert excluded["s1__random-seed-3"] == "Its atoms are not those of the run shown."
    assert excluded["s1__random-seed-4"].startswith("Its trajectory could not be read: ")
    # A run still writing its frames is played from its snapshots, at the
    # times of the run shown's frames; without a clock it cannot be.
    from fastmdxplora.gui.runs_together import TOGETHER

    running = copy / "runs" / "s1__random-seed-2" / "simulation"
    snapshots = running / "live_frames"
    snapshots.mkdir()
    from fastmdxplora.gui.live_frames import read_live_frame_history

    pdb = (running / "trajectory_topology.pdb").read_text()

    def written(count):
        for i in range(count):
            (snapshots / f"frame_{i}.pdb").write_text(pdb)
        (running / "live_frame_history.json").write_text(json.dumps({"frames": [
            {"path": f"live_frames/frame_{i}.pdb", "sequence": i, "frame_index": i,
             "mtime_ns": i, "simulation_time_ns": 0.25 * i} for i in range(count)]}))

    written(2)
    (running / "live_status.json").write_text(json.dumps({"status": "running",
                                                          "stage": "production"}))
    assert len(read_live_frame_history(running)["frames"]) == 2
    said = runs_together(copy, most_frames=2000)
    assert {"run_id": "s1__random-seed-2", "label": "random_seed 2",
            "reason": "The run played has no clock, so a run still running cannot be set "
                      "beside it in time."} in said["runs_together"]["excluded"]
    played = copy / "runs" / "s1__random-seed-1" / "simulation"
    (played / "simulation_parameters.json").write_text(json.dumps({"duration_ns_actual": 1.0}))
    said = runs_together(copy, most_frames=2000, force=True)
    entry = next(r for r in said["runs_together"]["runs"] if r["run_id"] == "s1__random-seed-2")
    # Frames at 0, 0.2, ... 1.0 ns: the snapshots at 0 and 0.25 ns reach 0.2.
    assert entry["running"] and entry["frames"] == 2
    assert entry["topology"] == "/structure/frames-topology.pdb?run=1"
    fitted = md.load_dcd(str(copy / TOGETHER / "run_1.dcd"), top=str(copy / TOGETHER / "run_1.pdb"))
    first = md.load_dcd(str(played / "frames.dcd"), top=str(played / "frames_topology.pdb"),
                        frame=0)
    backbone = first.topology.select("protein and backbone")
    # The snapshot was turned and moved apart: fitted, it is on the first frame.
    assert np.sqrt(((fitted.xyz[0, backbone] - first.xyz[0, backbone]) ** 2)
                   .sum(axis=1).mean()) < 0.06
    # It writes more: it is written again, and grows.
    stamp = (copy / TOGETHER / "run_1.dcd").stat().st_mtime_ns
    same = runs_together(copy, most_frames=2000)
    assert same["runs_together"]["signature"] == said["runs_together"]["signature"]
    assert (copy / TOGETHER / "run_1.dcd").stat().st_mtime_ns == stamp
    written(3)
    grown = runs_together(copy, most_frames=2000)
    assert grown["runs_together"]["signature"] != said["runs_together"]["signature"]
    assert next(r for r in grown["runs_together"]["runs"]
                if r["run_id"] == "s1__random-seed-2")["frames"] == 4
    assert grown["source_signature"] == said["source_signature"]
    # The run shown's frames cannot be written: nothing is played.
    (copy / "runs" / "s1__random-seed-1" / "simulation" / "production.dcd").write_bytes(b"x")
    unread = runs_together(copy, most_frames=2000)
    assert unread["available"] is False
    assert unread["reason"].startswith("The frames could not be written")
    assert unread["runs_together"] == {"runs": [], "excluded": []}
    for run in (copy / "runs").iterdir():
        shutil.rmtree(run)
    nothing = runs_together(copy, most_frames=2000)
    assert nothing["available"] is False
    assert nothing["reason"] == "No run of this study has a trajectory yet."


def test_the_server_plays_the_first_run_with_the_others(replicas):
    from fastmdxplora.gui.server import _artifact_label, start_dashboard_session

    session = start_dashboard_session(output=str(replicas), host="127.0.0.1", port=0)
    try:
        status, body = _get(session.url + "/api/frames-info")
        payload = json.loads(body)
        assert status == 200 and payload["playback_available"]
        assert len(payload["runs_together"]["runs"]) == 3
        info = json.loads(_get(session.url + "/api/structure-info")[1])
        assert info["valid"] and info["ligand_resnames"] == ["BEN"]
        status, topology = _get(session.url + "/structure/frames-topology.pdb")
        assert status == 200 and topology.count(b"\nATOM  ") > 1000
        status, frames = _get(session.url + "/structure/frames.dcd?run=2")
        assert status == 200
        assert frames == (replicas / "viewer_runs" / "run_2.dcd").read_bytes()
        assert _get(session.url + "/structure/frames.dcd?run=9")[0] == 404
        assert _get(session.url + "/structure/frames.dcd?run=..%2Fbatch_manifest")[0] == 404
        # A scene written from the Viewer is of the run played, alone.
        request = urllib.request.Request(
            session.url + "/api/scenes", method="POST",
            data=json.dumps({"name": "together", "view": {"frame": 2}}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=120) as response:
            said = json.loads(response.read())
        assert said["ok"] and said["frame"] == 2
        assert said["notes"][-1] == ("The scene is of the run played (s1__random-seed-1) "
                                     "alone: the other runs are not part of it.")
        assert (replicas / "scenes" / "together.mvsx").is_file()
    finally:
        session.server.shutdown()
    assert _artifact_label("viewer_runs/run_1.dcd") == (
        "Frames of a run fitted for the Viewer: run_1.dcd", "record")
    assert _artifact_label("viewer_runs/index.json") == (
        "Which runs the Viewer plays together", "record")


def test_the_viewer_renders_each_run_in_its_colour(replicas):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    state = "window.FastMDXMoleculeViewer.STATE"
    session = start_dashboard_session(output=str(replicas), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.set_default_timeout(120000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#viewer", wait_until="domcontentloaded")
            if not page.evaluate("() => !!document.createElement('canvas').getContext('webgl')"):
                pytest.skip("this browser has no WebGL, so the viewer cannot render")
            page.wait_for_function(f"() => {state}.model")
            page.wait_for_function("() => !document.getElementById('side-runs').hidden")
            before = page.locator("#viewer-runs-note").inner_text()
            colour = page.evaluate("() => document.getElementById('viewer-color').value")
            page.evaluate("() => window.dispatchEvent(new CustomEvent("
                          "'dashboard:trajectory-seek', {detail: {frame: 2}}))")
            page.wait_for_function(f"() => {state}.engine.runsShown().length === 2"
                                   f" && {state}.superposedUrl && {state}.engine.frame() === 2")
            at_two = page.evaluate(f"() => {state}.engine.runsShown()")
            superposed = page.evaluate("() => document.getElementById('traj-superpose').value")
            items = page.locator("#viewer-runs-list li").all_inner_texts()
            # The second run has four frames: past its last it is not shown.
            page.evaluate("() => window.dispatchEvent(new CustomEvent("
                          "'dashboard:trajectory-seek', {detail: {frame: 5}}))")
            # Hidden once the frame is shown: the second run's last is 3.
            page.wait_for_function(f"() => {state}.engine.frame() === 5"
                                   f" && !{state}.engine.runsShown()[0].rendered")
            at_five = page.evaluate(f"() => {state}.engine.runsShown()")
            # Hidden by the person, and shown again.
            page.locator('#viewer-runs-list li[data-run="s1__random-seed-3"] input').uncheck()
            page.wait_for_function(f"() => !{state}.engine.runsShown()[1].rendered")
            page.locator('#viewer-runs-list li[data-run="s1__random-seed-3"] input').check()
            page.wait_for_function(f"() => {state}.engine.runsShown()[1].rendered")
            # Frames as written are not fitted as the others are: they are
            # set aside, and back once the frames are fitted again.
            page.select_option("#traj-superpose", "none")
            page.wait_for_function(f"() => {state}.superposedUrl === null"
                                   f" && !{state}.engine.runsShown()[1].rendered")
            page.wait_for_function("() => document.getElementById('sr-live').textContent"
                                   ".includes('The other runs are hidden')")
            aside = page.locator("#sr-live").inner_text()
            page.select_option("#traj-superpose", "backbone")
            page.wait_for_function(f"() => {state}.engine.runsShown()[1].rendered")
            # A click on another run's atom is not an atom of the run played.
            clicked = page.evaluate(f"""() => {{
                const e = {state}.engine;
                const cells = e.plugin.state.data.cells;
                const other = cells.get(e.runs[1].structureRef).obj.data;
                return e.isARun(other) && !e.isARun(e.structure());
            }}""")
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert "press Play" in before
    assert colour == "run"
    assert superposed == "backbone"
    assert [(run["label"], run["colour"], run["frames"], run["rendered"]) for run in at_two] == [
        ("random_seed 2", "#E69F00", 4, True), ("random_seed 3", "#009E73", 6, True)]
    assert [(run["ended"], run["rendered"]) for run in at_five] == [(True, False), (False, True)]
    assert items[0].startswith("random_seed 1") and "played, 6 frames" in items[0]
    assert items[-1] == ("random_seed 4: not shown. Its atoms are not those of the run shown.")
    assert "The other runs are hidden" in aside
    assert clicked


def test_an_ai_app_writes_a_scene_of_the_run_played(replicas, monkeypatch, tmp_path):
    from fastmdxplora.mcp import App, Workspace
    from tests._mcp_wire import Wire

    monkeypatch.setenv("FASTMDXPLORA_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path / "settings"))
    wire = Wire(App(Workspace.at(replicas.parent)).server())
    try:
        said = wire.request("tools/call", {"name": "write_scene", "arguments": {
            "study": replicas.name, "name": "by an app", "frame": 3}})["result"]
    finally:
        wire.close()
    assert not said.get("isError"), said
    text = "\n".join(part["text"] for part in said["content"] if part["type"] == "text")
    assert "frame 3 of the frames the GUI plays" in text
    assert ("The study is of several runs: the scene is of s1__random-seed-1, the run the "
            "GUI plays, alone.") in text
    assert (replicas / "scenes" / "by an app.mvsx").is_file()


def test_while_no_run_has_finished_the_first_running_is_played(replicas, tmp_path):
    import shutil

    from fastmdxplora.gui.runs_together import run_shown, runs_together

    study = tmp_path / "running"
    shutil.copytree(replicas, study)
    for run in sorted((study / "runs").iterdir())[:2]:
        simulation = run / "simulation"
        pdb = (simulation / "trajectory_topology.pdb").read_text()
        (simulation / "live_frames").mkdir()
        for k in range(3):
            (simulation / "live_frames" / f"frame_{k}.pdb").write_text(pdb)
        (simulation / "live_frame_history.json").write_text(json.dumps({"frames": [
            {"path": f"live_frames/frame_{k}.pdb", "sequence": k, "frame_index": k,
             "mtime_ns": k, "simulation_time_ns": 0.1 * k} for k in range(3)]}))
        (simulation / "production.dcd").unlink()
        (simulation / "live_status.json").write_text(json.dumps({"status": "running"}))
    for run in sorted((study / "runs").iterdir())[2:]:
        shutil.rmtree(run / "simulation")
    assert run_shown(study) == study / "runs" / "s1__random-seed-1"
    said = runs_together(study, most_frames=2000)
    assert said["available"] and said["source_kind"] == "live-history"
    runs = said["runs_together"]["runs"]
    assert [(r["run_id"], r["running"], r["frames"]) for r in runs] == [
        ("s1__random-seed-1", True, 3), ("s1__random-seed-2", True, 3)]


def test_the_viewer_follows_a_run_still_running(replicas, tmp_path):
    pytest.importorskip("playwright.sync_api")
    import shutil

    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    study = tmp_path / "growing"
    shutil.copytree(replicas, study)
    played = study / "runs" / "s1__random-seed-1" / "simulation"
    (played / "simulation_parameters.json").write_text(json.dumps({"duration_ns_actual": 1.0}))
    for name in ("frames_index.json", "frames.dcd"):
        (played / name).unlink(missing_ok=True)
    running = study / "runs" / "s1__random-seed-2" / "simulation"
    pdb = (running / "trajectory_topology.pdb").read_text()
    (running / "live_frames").mkdir()
    (running / "production.dcd").unlink()

    def written(count):
        for i in range(count):
            (running / "live_frames" / f"frame_{i}.pdb").write_text(pdb)
        (running / "live_frame_history.json").write_text(json.dumps({"frames": [
            {"path": f"live_frames/frame_{i}.pdb", "sequence": i, "frame_index": i,
             "mtime_ns": i, "simulation_time_ns": 0.25 * i} for i in range(count)]}))

    written(2)
    (running / "live_status.json").write_text(json.dumps({"status": "running"}))
    state = "window.FastMDXMoleculeViewer.STATE"
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.set_default_timeout(120000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#viewer", wait_until="domcontentloaded")
            if not page.evaluate("() => !!document.createElement('canvas').getContext('webgl')"):
                pytest.skip("this browser has no WebGL, so the viewer cannot render")
            page.wait_for_function("() => !document.getElementById('side-runs').hidden")
            page.evaluate("() => window.dispatchEvent(new CustomEvent("
                          "'dashboard:trajectory-seek', {detail: {frame: 0}}))")
            page.wait_for_function(f"() => {state}.engine.runsShown().length === 2")
            before = page.evaluate(f"() => {state}.engine.runsShown().map((r) => r.frames)")
            said = page.locator('#viewer-runs-list li[data-run="s1__random-seed-2"]').inner_text()
            written(3)
            page.wait_for_function(f"() => {state}.engine.runsShown().length === 2"
                                   f" && {state}.engine.runsShown()"
                                   ".some((r) => r.frames === 4)", timeout=60000)
            after = page.evaluate(f"() => {state}.engine.runsShown().map((r) => r.frames)")
            frames = page.evaluate(f"() => {state}.engine.frameCount()")
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert before == [2, 6]
    assert "running, 2 frames so far" in said
    assert after == [4, 6] and frames == 6


def test_snapshots_that_cannot_be_set_beside_say_why(replicas, tmp_path):
    from fastmdxplora.gui.runs_together import _context, _snapshots_of, run_shown, runs_together
    from fastmdxplora.gui.trajectory_frames import frames_info

    played = run_shown(replicas)
    shown = frames_info(played, simulation_time_ns_total=1.0, force=True)
    assert runs_together(replicas, most_frames=2000) is not None
    context = _context(played, shown)
    simulation = tmp_path / "simulation"
    (simulation / "live_frames").mkdir(parents=True)
    pdb = (played / "simulation" / "trajectory_topology.pdb").read_text()

    def source(records, texts):
        for name, text in texts.items():
            (simulation / "live_frames" / name).write_text(text)
        return {"kind": "live-history", "simulation": simulation, "records": records,
                "signature": "x"}

    untimed = source([{"path": "live_frames/a.pdb"}], {"a.pdb": pdb})
    assert _snapshots_of(untimed, context)[2] == "Its snapshots have no times yet."
    water = "HETATM    1  O   HOH W   1       0.000   0.000   0.000  1.00  0.00           O\n"
    other = source([{"path": "live_frames/w.pdb", "simulation_time_ns": 0.0}], {"w.pdb": water})
    assert _snapshots_of(other, context)[2] == "Its atoms share no backbone with the run shown."
    shorter = "\n".join(pdb.splitlines()[:-40]) + "\n"
    uneven = source([{"path": "live_frames/a.pdb", "simulation_time_ns": 0.0},
                     {"path": "live_frames/b.pdb", "simulation_time_ns": 0.5}],
                    {"a.pdb": shorter, "b.pdb": pdb})
    assert _snapshots_of(uneven, context)[2] == "Its snapshots do not all hold the same atoms."
    late = source([{"path": "live_frames/a.pdb", "simulation_time_ns": 5.0},
                   {"path": "live_frames/b.pdb", "simulation_time_ns": 6.0}],
                  {"a.pdb": pdb, "b.pdb": pdb})
    frames, _, _ = _snapshots_of(late, context)
    assert frames.n_frames == 6
