from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from fastmdxplora.gui import exploration as launch
from fastmdxplora.gui import live_frames as frames
from fastmdxplora.gui import telemetry
from fastmdxplora.gui import trajectory_playback as playback
from fastmdxplora.gui.ligand_detection import detect_ligands, filter_pdb_to_ligand
from fastmdxplora.gui.structure_info import _count_structure_cached, count_structure, ligand_atom_counts
from fastmdxplora.simulation import runner


def _atom(record="ATOM", res="ALA", chain="A", seq=1, x=0.0, y=0.0, z=0.0) -> str:
    return f"{record:<6}{1:5d}  CA  {res:>3} {chain}{seq:4d}    {x:8.3f}{y:8.3f}{z:8.3f}  1.00 10.00           C"


def _launch_payload() -> dict:
    return {
        "system": "1L2Y", "run_name": "run",
        "setup": {"ph": 7, "forcefield": "charmm36", "water_model": "auto",
                  "ion_concentration_M": .15, "solvent_padding_nm": 1},
        "simulation": {"minimize": True, "nvt_steps": 1, "npt_steps": 1,
                       "production_steps": 10, "timestep_fs": 2, "temperature_K": 300,
                       "friction_per_ps": 1, "integrator": "langevin_middle", "platform": "CPU",
                       "precision": "mixed", "trajectory_interval_steps": 2,
                       "checkpoint_interval_steps": 0, "telemetry_interval": 2},
        "workflow": {"run_analysis": True, "run_report": True, "analyses": ["rmsd"],
                     "report_document": True, "report_slides": True, "report_bundle": True},
    }


class _Topology:
    def __init__(self, selected=(0, 1), fail=False):
        self.selected, self.fail = selected, fail

    def select(self, _query):
        if self.fail:
            raise RuntimeError("selection failed")
        return list(self.selected)


class _BrowserTrajectory:
    def __init__(self, times=(0.0, 0.0)):
        self.time = list(times)

    def save_pdb(self, path):
        Path(path).write_text(_atom() + "\nEND\n", encoding="utf-8")


class _Trajectory:
    def __init__(self, n_frames=5, times=(0.0, 0.0)):
        self.n_frames, self.times = n_frames, times

    def __getitem__(self, _indices):
        return _BrowserTrajectory(self.times)


class _MD:
    def __init__(self, *, n_frames=5, selected=(0, 1), select_fail=False, dcd_fail=False):
        self.topology = _Topology(selected, select_fail)
        self.trajectory = _Trajectory(n_frames)
        self.dcd_fail = dcd_fail

    def load_pdb(self, _path):
        return SimpleNamespace(topology=self.topology)

    def load_dcd(self, *_args, **_kwargs):
        if self.dcd_fail:
            raise RuntimeError("busy dcd")
        return self.trajectory


def test_playback_dcd_cache_fallback_and_helpers(tmp_path: Path, monkeypatch) -> None:
    out, sim = tmp_path / "run", tmp_path / "run" / "simulation"
    sim.mkdir(parents=True)
    (sim / "topology.pdb").write_text(_atom() + "\nEND\n", encoding="utf-8")
    (sim / "production.dcd").write_bytes(b"dcd")
    (sim / "live_status.json").write_text('{"status":"completed"}', encoding="utf-8")
    monkeypatch.setattr(playback, "_import_mdtraj", lambda: _MD())

    result = playback.playback_info(out, max_browser_frames=3, simulation_time_ns_total=1.0)
    assert result["source_kind"] == "production-dcd" and result["frame_times_ns"][-1] == 1.0
    assert playback.playback_info(out) == json.loads((sim / "playback_index.json").read_text())
    assert playback._even_indices(10, 2) == [0, 9]
    assert playback._even_indices(3, 5) == [0, 1, 2]
    assert playback._load_index(tmp_path / "missing.json")["reason"] == "invalid-companion"

    # A temporarily unreadable DCD falls back to completed live-history snapshots.
    for i in range(2):
        p = sim / f"h{i}.pdb"
        p.write_text(_atom(seq=i + 1) + "\nEND\n", encoding="utf-8")
    (sim / "live_frame_history.json").write_text(json.dumps({"frames": [
        {"path": "h0.pdb", "sequence": 0, "frame_index": 0, "simulation_time_ns": 0.0},
        {"path": "h1.pdb", "sequence": 1, "frame_index": 1, "simulation_time_ns": 0.1},
    ]}), encoding="utf-8")
    monkeypatch.setattr(playback, "_import_mdtraj", lambda: _MD(dcd_fail=True))
    result = playback.playback_info(out, force=True)
    assert result["source_kind"] == "live-history"


def test_playback_streams_multiple_dcd_chunks(tmp_path: Path, monkeypatch) -> None:
    topology = tmp_path / "topology.pdb"
    dcd = tmp_path / "production.dcd"
    companion_pdb = tmp_path / "playback.pdb"
    companion_idx = tmp_path / "playback_index.json"
    topology.write_text(_atom() + "\nEND\n", encoding="utf-8")
    dcd.write_bytes(b"dcd")

    class Chunk:
        def __init__(self, start: int) -> None:
            self.n_frames = 2
            self.xyz = np.arange(start * 6, (start + 2) * 6, dtype=float).reshape(2, 2, 3)
            self.time = np.array([float(start), float(start + 1)])

    class ChunkedTrajectory:
        def __init__(self, xyz, topology, time) -> None:
            self.xyz = xyz
            self.topology = topology
            self.time = time
            self.n_frames = len(time)

        def save_pdb(self, path) -> None:
            Path(path).write_text(_atom() + "\nEND\n", encoding="utf-8")

    class ChunkedMD(_MD):
        Trajectory = ChunkedTrajectory

        def iterload(self, *_args, **_kwargs):
            yield Chunk(0)
            yield Chunk(2)

    result = playback._generate_from_dcd(
        md=ChunkedMD(),
        topology_path=topology,
        dcd_path=dcd,
        companion_pdb=companion_pdb,
        companion_idx=companion_idx,
        max_browser_frames=3,
        simulation_time_ns_total=3.0,
        source_signature="chunks",
    )

    assert result["n_frames_total"] == 4
    assert result["n_frames_browser"] == 3
    assert result["frame_indices"] == [0, 2, 3]
    assert companion_pdb.exists()


def test_playback_error_branches_and_neighborhood(tmp_path: Path) -> None:
    topology = tmp_path / "top.pdb"
    topology.write_text("\n".join([
        _atom("HETATM", "LIG", seq=9),
        _atom("ATOM", "ALA", seq=1, x=1),
        "ATOM bad-coordinate-line",
        "END",
    ]), encoding="utf-8")
    assert playback.neighborhood_residues(topology_path=topology, ligand_resname="LIG") == [("A", 1)]
    assert playback.neighborhood_residues(topology_path=tmp_path / "none", ligand_resname="LIG") == []
    assert playback.neighborhood_residues(topology_path=topology, ligand_resname="XXX") == []

    idx = tmp_path / "index.json"
    pdb = tmp_path / "playback.pdb"
    one = playback._generate_from_dcd(
        md=_MD(n_frames=1, selected=(), select_fail=True), topology_path=topology,
        dcd_path=tmp_path / "x.dcd", companion_pdb=pdb, companion_idx=idx,
        max_browser_frames=2, simulation_time_ns_total=None, source_signature="x",
    )
    assert one["reason"] == "not-enough-trajectory-frames"

    unreadable = playback._generate_from_history(
        sim_dir=tmp_path, records=[{"path": "missing"}, {"path": "also-missing"}],
        companion_pdb=pdb, companion_idx=idx, max_browser_frames=2, source_signature="x",
    )
    assert unreadable["reason"] == "not-enough-readable-history-frames"


def test_exploration_validation_command_and_runtime_branches(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(launch, "exploration_environment_error", lambda _config: None)
    bad = _launch_payload()
    bad.update(system="x" * 4097)
    bad["setup"].update(ph="bad", forcefield="bad", ion_concentration_M="bad", solvent_padding_nm=99)
    bad["simulation"].update(integrator="bad", platform="bad", precision="bad", nvt_steps="bad")
    bad["workflow"].update(run_analysis=False, run_report=True, analyses="bad")
    result = launch.validate_exploration_payload(bad)
    assert not result["valid"] and result["warnings"]
    assert {"system", "setup.ph", "setup.forcefield", "simulation.integrator"} <= result["errors"].keys()

    cfg = launch.validate_exploration_payload(_launch_payload())["config"]
    cfg["setup"].update(water_model="tip3p", keep_heterogens=True, keep_water=True)
    cfg["simulation"]["minimize"] = False
    cfg["workflow"].update(run_analysis=False, report_document=False, report_slides=False, report_bundle=False)
    command = launch.build_exploration_command(cfg, tmp_path / "out")
    for flag in ("--setup-water-model", "--setup-keep-heterogens", "--setup-keep-water",
                 "--simulate-no-minimize", "--include", "--report-no-document",
                 "--report-no-slides", "--report-no-bundle"):
        assert flag in command

    runtime = launch.DashboardRuntime(tmp_path / "workspace", tmp_path / "runs")
    assert runtime.launch({"system": ""})["valid"] is False
    runtime.process = SimpleNamespace(poll=lambda: 0)
    assert runtime.snapshot()["status"] == "completed"
    runtime.process = SimpleNamespace(poll=lambda: 2)
    runtime.process_returncode = None
    assert runtime.snapshot()["status"] == "failed"

    terminated = []
    runtime.process = SimpleNamespace(poll=lambda: None, terminate=lambda: terminated.append(True))
    assert runtime.stop()["stopped"] and terminated
    assert runtime.launch(_launch_payload())["errors"]["run"]

    occupied = launch.DashboardRuntime(tmp_path / "w2", tmp_path / "runs2")
    target = occupied.exploration_root / "run"
    target.mkdir()
    (target / "file").write_text("x", encoding="utf-8")
    assert "not empty" in occupied.launch(_launch_payload())["errors"]["run_name"]

    failing = launch.DashboardRuntime(tmp_path / "w3", tmp_path / "runs3")
    monkeypatch.setattr(launch.subprocess, "Popen", lambda *_a, **_k: (_ for _ in ()).throw(OSError("no")))
    with pytest.raises(OSError):
        failing.launch(_launch_payload())


def test_live_frame_failure_fallbacks_and_bounded_archive(tmp_path: Path, monkeypatch) -> None:
    text = "CRYST1".ljust(80) + "\n" + _atom() + "\nTER\nEND\n"
    assert "CRYST1" in frames.dashboard_display_pdb(text) and "TER" in frames.dashboard_display_pdb(text)

    with patch.object(Path, "stat", side_effect=OSError), patch.object(frames, "_atomic_json", side_effect=OSError):
        result = frames.write_live_frame(tmp_path / "fallback", pdb_text=text, frame_index=1)
    assert result["ok"] and result["live_frame_size"] == len(text.encode())

    def old_writer(_topology, _positions, buf):
        buf.write(text)
    assert frames.write_openmm_live_frame(tmp_path / "openmm", pdbfile_writer=old_writer,
                                          topology=None, positions=None)["ok"]
    assert not frames.write_openmm_live_frame(tmp_path, pdbfile_writer=lambda *_a, **_k: None,
                                              topology=None, positions=None)["ok"]
    assert not frames.write_openmm_live_frame(tmp_path, pdbfile_writer=lambda *_a, **_k: 1 / 0,
                                              topology=None, positions=None)["ok"]

    archive = tmp_path / "archive"
    for i in range(3):
        assert frames.write_live_frame(archive, pdb_text=text, frame_index=i, archive=True,
                                       max_history_frames=2)["ok"]
    history = frames.read_live_frame_history(archive)
    assert history["count"] == 2 and history["frames"][0]["frame_index"] == 1

    (archive / frames.LIVE_FRAME_INDEX_FILE).write_text("bad", encoding="utf-8")
    assert frames.read_live_frame_index(archive) == {"live_frame_available": False}
    monkeypatch.setattr(Path, "mkdir", lambda *_a, **_k: (_ for _ in ()).throw(OSError("blocked")))
    assert frames._archive_frame(tmp_path / "blocked", pdb_text=text, frame_index=0, stage="NVT",
                                 simulation_time_ns=0, max_history_frames=2)["count"] == 0


def test_ligand_and_structure_edge_paths(tmp_path: Path) -> None:
    detected = detect_ligands([("A", "", "1"), ("A", "ALA", "2"), ("A", "LIG", "10A")])
    assert detected["resnames"] == ["LIG"]
    pdb = "HEADER keep\n" + _atom("HETATM", "LIG") + "\n" + _atom("ATOM", "ALA")
    assert filter_pdb_to_ligand(pdb, "") == pdb
    filtered = filter_pdb_to_ligand(pdb, "lig")
    assert "LIG" in filtered and "ALA" not in filtered and "HEADER" in filtered

    structure = tmp_path / "structure.pdb"
    structure.write_text("\n".join([_atom("HETATM", "NA"), "ATOM bad-coordinate-line", "END"]), encoding="utf-8")
    info = count_structure(structure)
    assert info["ions"] == 1

    empty = tmp_path / "empty.pdb"
    empty.write_text("HEADER\nEND\n", encoding="utf-8")
    assert count_structure(empty)["centroid_angstrom"] == [None, None, None]
    assert _count_structure_cached(str(tmp_path), 0, 0)["reason"].startswith("read-error")

    fake_path = SimpleNamespace(is_file=lambda: True, stat=lambda: (_ for _ in ()).throw(OSError("stat")), as_posix=lambda: "fake")
    # `structure_info` moved out of the gui package -- it reads a file as
    # text and the orchestrator needs it before any interface exists. The
    # old path re-exports the function, so patching a name there no
    # longer reaches the module the function actually lives in.
    with patch("fastmdxplora.structure_info.Path", return_value=fake_path):
        assert count_structure(structure)["reason"].startswith("stat-error")
    with patch.object(Path, "open", side_effect=OSError("read")):
        assert ligand_atom_counts(structure) == {}


def test_orchestrator_dashboard_telemetry_paths(tmp_path: Path, monkeypatch) -> None:
    from fastmdxplora.orchestrator import FastMDXplora, PhaseResult

    app = object.__new__(FastMDXplora)
    app.output_dir = tmp_path / "run"
    app.output_dir.mkdir()
    app.options = {"simulation": {}}

    class Writer:
        def __init__(self):
            self.root = app.output_dir / "simulation"
            self.statuses, self.stages, self.events = [], [], []

        def write_status(self, **kwargs): self.statuses.append(kwargs)
        def mark_stage(self, *args, **kwargs): self.stages.append((args, kwargs))
        def event(self, *args, **kwargs): self.events.append((args, kwargs))

    writer = Writer()
    monkeypatch.setenv("FASTMDX_DASHBOARD_ACTIVE", "1")
    monkeypatch.setenv("FASTMDX_DASHBOARD_OUTPUT", str(app.output_dir))
    monkeypatch.setattr("fastmdxplora.gui.telemetry.TelemetryWriter", lambda *_a, **_k: writer)
    assert app._dashboard_writer() is writer
    monkeypatch.setattr("fastmdxplora.gui.telemetry.TelemetryWriter", lambda *_a, **_k: 1 / 0)
    assert app._dashboard_writer() is None

    FastMDXplora._initialize_dashboard_timeline(writer, ["setup", "simulation", "report"])
    assert writer.statuses[-1]["stage_states"]["analysis"] == "skipped"
    FastMDXplora._initialize_dashboard_timeline(writer, [])
    assert writer.statuses[-1]["status"] == "completed"

    FastMDXplora._mark_dashboard_phase_start(None, "setup", {})
    FastMDXplora._mark_dashboard_phase_start(writer, "simulation", {"minimize": False})
    FastMDXplora._mark_dashboard_phase_start(writer, "analysis", {})

    ok = PhaseResult("simulation", "ok", app.output_dir)
    skipped = PhaseResult("report", "skipped", app.output_dir)
    failed = PhaseResult("analysis", "error", app.output_dir, message="boom")
    monkeypatch.setattr("fastmdxplora.gui.telemetry.read_status", lambda _root: {
        "stage_states": {"minimization": "waiting", "nvt": "current",
                         "npt": "completed", "production": "waiting"}
    })
    FastMDXplora._mark_dashboard_phase_end(None, "setup", ok)
    FastMDXplora._mark_dashboard_phase_end(writer, "simulation", ok)
    FastMDXplora._mark_dashboard_phase_end(writer, "report", skipped)
    FastMDXplora._mark_dashboard_phase_end(writer, "analysis", failed)
    assert any(args[1] == "failed" for args, _kwargs in writer.stages)
    assert writer.events[-1][1]["level"] == "error"


def test_server_helpers_cover_changed_display_branches(tmp_path: Path, monkeypatch) -> None:
    from fastmdxplora.gui import server

    assert server.DashboardConfig(binding_pocket_cutoff_A=4.5).binding_pocket_cutoff_m == 4.5
    assert server._artifact_records(tmp_path / "missing") == []
    assert server._summary_records(tmp_path, {"phases": [{"status": "ok"}]}, {}, {})[0]["value"] == "completed"
    assert server._summary_records(tmp_path, {"phases": [{"status": "failed"}]}, {}, {})[0]["value"] == "failed"
    assert server._summary_records(tmp_path, {"phases": [{"status": "running"}]}, {}, {})[0]["value"] == "in progress"

    monkeypatch.setattr(server, "read_metrics", lambda *_a, **_k: [{"temperature": 301}, {"temperature": None}])
    assert server._last_metric_value(tmp_path, "temperature") is None
    monkeypatch.setattr(server, "read_metrics", lambda *_a, **_k: [{"temperature": 301}])
    assert server._last_metric_value(tmp_path, "temperature") == "301"

    assert server._run_title(tmp_path, {"title": "Named"}) == "Named"
    assert server._run_title(tmp_path, {"system": "/x/1abc.pdb"}).startswith("1abc")
    assert server._setup_details({"parameters": {}, "resolved_forcefield": {"ligand": {"name": "LIG"}}})["ligand"] == "LIG"
    assert server._analysis_records({"status": "skipped", "note": "none"}, [], [])[0]["status"] == "skipped"
    assert server._phase_records({"phases": [{"name": "setup", "status": "ok"}]})[0]["status"] == "ok"
    assert server._plot_title("analysis/rmsd/rmsd_plot.png") == "RMSD"
    assert server._plot_title("report/dashboard_assets/rmsd.png") == "RMSD"
    assert server._plot_category("analysis/cluster/plot.png") == "Clustering"
    assert server._plot_category("analysis/sasa/plot.png") == "Additional Analysis"
    assert server._plot_category("misc/plot.png") == "Other"

    def fail_open(*_args, **_kwargs):
        raise OSError("no opener")

    # Windows uses os.startfile; POSIX platforms use subprocess.Popen.
    # Exercise each branch directly so the test is deterministic on every CI OS.
    monkeypatch.setattr(server.os, "name", "nt")
    monkeypatch.setattr(server.os, "startfile", lambda *_a, **_k: None, raising=False)
    assert server._open_local_path(tmp_path) == (True, "opened")
    monkeypatch.setattr(server.os, "startfile", fail_open)
    assert server._open_local_path(tmp_path) == (False, "no opener")

    monkeypatch.setattr(server.os, "name", "posix")
    monkeypatch.setattr(server.sys, "platform", "darwin")
    monkeypatch.setattr(server.subprocess, "Popen", lambda *_a, **_k: object())
    assert server._open_local_path(tmp_path) == (True, "opened")

    monkeypatch.setattr(server.sys, "platform", "linux")
    monkeypatch.setattr(server.subprocess, "Popen", fail_open)
    assert server._open_local_path(tmp_path) == (False, "no opener")


def test_server_error_and_exploration_routes(tmp_path: Path, monkeypatch) -> None:
    import urllib.error
    import urllib.request
    from fastmdxplora.gui import server as live_server

    root = tmp_path / "run"
    root.mkdir()
    monkeypatch.setattr(live_server, "_open_local_path", lambda _path: (True, "opened"))
    httpd, url = live_server.start_test_server(root, home_mode=True)

    def get(path: str, expected: int = 200):
        try:
            with urllib.request.urlopen(url + path) as response:
                assert response.status == expected
                return response.read()
        except urllib.error.HTTPError as exc:
            assert exc.code == expected
            return exc.read()

    def post(path: str, value, expected: int):
        request = urllib.request.Request(
            url + path, data=json.dumps(value).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(request) as response:
                assert response.status == expected
                return response.read()
        except urllib.error.HTTPError as exc:
            assert exc.code == expected
            return exc.read()

    try:
        get("/api/playback-info?max=bad")
        get("/api/open-output")
        get("/structure/playback.pdb", 404)
        get("/analysis-figures-svg.zip", 404)
        get("/structure/topology.pdb", 404)
        get("/artifacts/missing.txt", 404)
        get("/static/missing.txt", 404)
        get("/not-found", 404)
        post("/api/explore/start", {"system": ""}, 422)
        post("/api/explore/stop", {}, 200)
        post("/not-found", {}, 404)
        post("/api/explore/validate", [], 500)

        monkeypatch.setattr(live_server, "_results_payload", lambda _root: (_ for _ in ()).throw(RuntimeError("route")))
        get("/api/results", 500)
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_runner_live_metric_fields_stage_mapping_and_sample_failure(
    tmp_path: Path,
) -> None:
    """Exercise the dashboard-only OpenMM metric calculations without OpenMM."""

    class Quantity:
        def __init__(self, value):
            self.value = value

        def value_in_unit(self, _unit):
            return self.value

    class Unit:
        kilojoules_per_mole = 1.0
        nanometer = 1.0
        dalton = 1.0

    class State:
        def getPotentialEnergy(self):
            return Quantity(-90.0)

        def getKineticEnergy(self):
            return Quantity(30.0)

        def getPeriodicBoxVolume(self):
            return Quantity(2.0)

    class System:
        masses = (12.0, 18.0)

        def getNumParticles(self):
            return len(self.masses)

        def getNumConstraints(self):
            return 0

        def getNumForces(self):
            return 1

        def getForce(self, _index):
            return type("CMMotionRemover", (), {})()

        def getParticleMass(self, index):
            return self.masses[index]

    state = State()
    simulation = SimpleNamespace(
        context=SimpleNamespace(getState=lambda **_kwargs: state),
        system=System(),
        topology="topology",
    )

    class Recorder:
        def __init__(self):
            self.root = tmp_path / "simulation"
            self.start_time = datetime.now(timezone.utc) - timedelta(seconds=60)
            self.metrics = []
            self.stages = []
            self.events = []

        def append_metric(self, **values):
            self.metrics.append(values)

        def mark_stage(self, *values, **updates):
            self.stages.append((values, updates))

        def event(self, message, **updates):
            self.events.append((message, updates))

    recorder = Recorder()
    stage_cases = (
        ("Minimizing energy", "minimization"),
        ("NVT equilibration", "nvt"),
        ("NPT equilibration", "npt"),
        ("Production", "production"),
        ("custom", "custom"),
    )
    with patch.object(runner, "_maybe_write_live_frame") as live_frame:
        for stage, expected_key in stage_cases:
            runner._append_live_metric(
                {"unit": Unit()},
                simulation,
                recorder,
                stage=stage,
                step=50,
                total_steps=100,
                timestep_fs=2.0,
                frame_count=5,
            )
            assert recorder.stages[-1][0] == (expected_key, "current")

    metric = recorder.metrics[-1]
    assert metric["potential_energy"] == -90.0
    assert metric["kinetic_energy"] == 30.0
    assert metric["total_energy"] == -60.0
    assert metric["temperature"] == pytest.approx(
        60.0 / (3.0 * 0.00831446261815324)
    )
    assert metric["volume"] == 2.0
    assert metric["density"] == pytest.approx(30.0 * 1.66053906660e-3 / 2.0)
    assert metric["speed"] > 0
    assert metric["progress_percent"] == 50.0
    assert live_frame.call_count == len(stage_cases)

    failed_recorder = Recorder()
    failed_simulation = SimpleNamespace(
        context=SimpleNamespace(
            getState=lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("busy"))
        )
    )
    runner._append_live_metric(
        {"unit": Unit()},
        failed_simulation,
        failed_recorder,
        stage="production",
        step=1,
        total_steps=10,
        timestep_fs=2.0,
    )
    assert "could not sample live metrics" in failed_recorder.events[-1][0]
    assert failed_recorder.events[-1][1]["level"] == "warning"


def test_runner_metric_helper_edge_cases(tmp_path: Path) -> None:
    class Unit:
        nanometer = 1.0
        dalton = 1.0

    class ZeroDofSystem:
        def getNumParticles(self):
            return 1

        def getNumConstraints(self):
            return 3

        def getNumForces(self):
            return 0

    assert runner._temperature_from_kinetic_energy(SimpleNamespace(), None) is None
    assert (
        runner._temperature_from_kinetic_energy(
            SimpleNamespace(system=ZeroDofSystem()), 10.0
        )
        is None
    )
    assert runner._temperature_from_kinetic_energy(SimpleNamespace(), 10.0) is None

    broken_state = SimpleNamespace(
        getPeriodicBoxVolume=lambda: (_ for _ in ()).throw(RuntimeError("no box"))
    )
    assert runner._periodic_volume_nm3(broken_state, Unit()) is None
    assert runner._system_density_g_ml(SimpleNamespace(), Unit(), None) is None
    assert runner._system_density_g_ml(SimpleNamespace(), Unit(), 0.0) is None
    assert runner._system_density_g_ml(SimpleNamespace(), Unit(), 1.0) is None

    assert (
        runner._telemetry_speed_ns_day(
            SimpleNamespace(start_time="not a datetime"), 1.0
        )
        is None
    )
    assert (
        runner._telemetry_speed_ns_day(
            SimpleNamespace(start_time=datetime.now(timezone.utc) + timedelta(days=1)),
            1.0,
        )
        is None
    )
    assert (
        runner._telemetry_speed_ns_day(
            SimpleNamespace(start_time=datetime.now(timezone.utc) - timedelta(days=1)),
            1.0,
        )
        == pytest.approx(1.0, rel=0.01)
    )

    class BadQuantity:
        def value_in_unit(self, _unit):
            raise RuntimeError("bad quantity")

    assert runner._quantity_to_float(BadQuantity(), 1.0) is None
    assert runner._quantity_to_float("not numeric", 1.0) is None
    assert runner._quantity_to_float([[3.5, 4.5]], 1.0) == 3.5

    # Live-frame snapshots are best-effort and must not terminate a run.
    simulation = SimpleNamespace(
        context=SimpleNamespace(
            getState=lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("busy"))
        )
    )
    runner._maybe_write_live_frame(
        {"PDBFile": SimpleNamespace(writeFile=lambda *_a, **_k: None)},
        simulation,
        SimpleNamespace(root=tmp_path),
        1,
        stage="nvt",
        simulation_time_ns=0.1,
    )


def test_telemetry_merges_live_and_energy_rows_and_normalizes_status(
    tmp_path: Path,
) -> None:
    run_root = tmp_path / "run"
    simulation_dir = run_root / "simulation"
    simulation_dir.mkdir(parents=True)
    status_path = simulation_dir / telemetry.STATUS_FILE
    status_path.write_text(
        json.dumps(
            {
                "run_started_at": "2026-07-24T12:00:00",
                "stage_states": ["invalid"],
                "current_frame_count": 7,
            }
        ),
        encoding="utf-8",
    )

    writer = telemetry.TelemetryWriter(simulation_dir)
    writer.write_status(stage="production")
    status = telemetry.read_status(run_root)
    assert status["run_started_at"].endswith("+00:00")
    assert status["stage_states"] == {
        name: "waiting" for name in telemetry.STAGE_ORDER
    }
    assert status["current_frame_count"] == 7

    before = dict(status["stage_states"])
    writer.mark_stage("not-a-stage", "failed")
    assert telemetry.read_status(run_root)["stage_states"] == before
    writer.mark_stage("analysis", "CURRENT", status="running")
    assert telemetry.read_status(run_root)["stage_states"]["analysis"] == "current"

    (simulation_dir / telemetry.METRICS_FILE).write_text(
        "timestamp,stage,step,temperature,current_frame_count\n"
        ",production,10,,\n",
        encoding="utf-8",
    )
    (simulation_dir / "energy.csv").write_text(
        '#"Step","Time (ps)","Temperature (K)","Speed (ns/day)","Ignored"\n'
        "10,2500,302.0,4.2,value\n"
        "20,bad,303.0,5.0,value\n",
        encoding="utf-8",
    )
    metrics = telemetry.read_metrics(run_root)
    assert len(metrics) == 1
    assert metrics[0]["temperature"] == "302.0"
    assert metrics[0]["simulation_time_ns"] == "2.5"
    assert metrics[0]["speed"] == "4.2"
    assert metrics[0]["current_frame_count"] == "7"

    assert telemetry._normalise_energy_header("unknown") is None
    assert telemetry._parse_iso_datetime(None) is None
    assert telemetry._parse_iso_datetime("bad-date") is None
    assert telemetry._parse_iso_datetime("2026-07-24T12:00:00").tzinfo is not None

    status_path.write_text("[]", encoding="utf-8")
    assert telemetry._read_status_path(status_path) == {}
    status_path.write_text("{bad", encoding="utf-8")
    assert telemetry._read_status_path(status_path) == {}


def test_cli_dashboard_remaining_lifecycle_and_startup_branches(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import importlib

    cli = importlib.import_module("fastmdxplora.cli.main")

    # Other CLI tests intentionally activate dashboard telemetry in this
    # process.  This assertion covers startup with no activation marker, so
    # make that precondition explicit instead of depending on test order.
    monkeypatch.delenv("FASTMDX_DASHBOARD_ACTIVE", raising=False)
    monkeypatch.delenv("FASTMDX_DASHBOARD_URL", raising=False)

    url, enabled = cli._startup_dashboard_details(
        ["gui", "--host=0.0.0.0", "--port", "9001"]
    )
    assert (url, enabled) == ("http://127.0.0.1:9001", True)

    url, enabled = cli._startup_dashboard_details(
        ["explore", "--dashboard-host", "localhost", "--dashboard-port=9002"]
    )
    assert (url, enabled) == ("http://localhost:9002", False)

    with patch.dict(
        os.environ,
        {
            "FASTMDX_DASHBOARD_ACTIVE": "1",
            "FASTMDX_DASHBOARD_URL": "http://example.test:1234",
        },
    ):
        assert cli._startup_dashboard_details([]) == (
            "http://example.test:1234",
            True,
        )

    assert cli._dashboard_requested(SimpleNamespace(dashboard=True))
    assert not cli._dashboard_requested(SimpleNamespace())
    config = {}
    cli._enable_dashboard_telemetry(config)
    assert config["simulation"] == {"live_telemetry": True}

    explicit = cli._resolve_dashboard_output_dir(
        SimpleNamespace(output_dir=tmp_path / "explicit")
    )
    configured = cli._resolve_dashboard_output_dir(
        SimpleNamespace(output_dir=None), {"output": tmp_path / "configured"}
    )
    generated = cli._resolve_dashboard_output_dir(SimpleNamespace(output_dir=None))
    assert explicit == (tmp_path / "explicit").resolve()
    assert configured == (tmp_path / "configured").resolve()
    assert generated.name.startswith("fastmdxplora_") and "_study_" in generated.name

    gui_args = SimpleNamespace(
        output=tmp_path,
        host="127.0.0.1",
        port=8765,
        no_browser=True,
        ligand_resname=None,
        binding_pocket_cutoff_A=None,
    )
    with patch("fastmdxplora.gui.server.serve_dashboard") as serve:
        assert cli._cmd_gui(gui_args) == 0
    assert serve.call_args.kwargs["output"] == tmp_path
    assert serve.call_args.kwargs["config"].binding_pocket_cutoff_A == 5.0

    gui_args.no_browser = False
    with (
        patch("fastmdxplora.gui.server.serve_dashboard"),
        patch("webbrowser.open", side_effect=RuntimeError("headless")),
    ):
        assert cli._cmd_gui(gui_args) == 0

    with patch("fastmdxplora.gui.server.serve_dashboard") as serve_home:
        assert cli._cmd_dashboard_home() == 0
    assert serve_home.call_args.kwargs == {
        "output": Path.cwd(),
        "host": "127.0.0.1",
        "port": 8765,
    }

    stopped = []
    cli._finish_dashboard_for_command(None, SimpleNamespace())
    session = SimpleNamespace(stop=lambda: stopped.append("stopped"))
    cli._finish_dashboard_for_command(
        session, SimpleNamespace(dashboard_stop_on_complete=True)
    )
    assert stopped == ["stopped"]

    waited = []

    def interrupt_wait():
        waited.append(True)
        raise KeyboardInterrupt

    session = SimpleNamespace(
        url="http://127.0.0.1:8765",
        wait_forever=interrupt_wait,
        stop=lambda: stopped.append("after-wait"),
    )
    cli._finish_dashboard_for_command(
        session, SimpleNamespace(dashboard_stop_on_complete=False)
    )
    assert waited and stopped[-1] == "after-wait"
