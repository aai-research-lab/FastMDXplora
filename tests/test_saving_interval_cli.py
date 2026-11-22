"""The analysis interval is a setting, and its clock survives the CLI path."""

from __future__ import annotations

import json

import mdtraj as md
import numpy as np
import pytest


def _trajectory(where, *, suffix="dcd", frames=8):
    top = md.Topology()
    chain = top.add_chain()
    for _ in range(3):
        residue = top.add_residue("ALA", chain)
        for name, element in (("N", "N"), ("CA", "C"), ("C", "C"), ("O", "O")):
            top.add_atom(name, md.element.get_by_symbol(element), residue)
    xyz = np.random.default_rng(1).random((frames, top.n_atoms, 3), dtype=np.float32)
    trajectory = md.Trajectory(xyz, top, time=np.arange(frames, dtype=float) * 4)
    where.mkdir(parents=True, exist_ok=True)
    topology = where / "topology.pdb"
    path = where / f"trajectory.{suffix}"
    trajectory[0].save_pdb(str(topology))
    getattr(trajectory, f"save_{suffix}")(str(path))
    return path, topology


def _run_cli(monkeypatch, argv):
    import logging
    import os

    from fastmdxplora import orchestrator as project_orchestrator
    from fastmdxplora.analysis import analyze
    from fastmdxplora.cli.main import main
    from fastmdxplora.utils import logging as fastmdx_logging

    seen = {}
    original = analyze.AnalysisOrchestrator

    class RecordingOrchestrator(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            seen["load_kwargs"] = dict(self._load_kwargs)
            seen["time"] = np.asarray(self.traj.time).copy()

    monkeypatch.setattr(analyze, "AnalysisOrchestrator", RecordingOrchestrator)
    if not hasattr(os, "fchmod"):
        # Windows has no os.fchmod; these analysis-only runs do not use the
        # process-record lock whose writer needs it.
        monkeypatch.setattr(project_orchestrator, "_write_run_process", lambda *_a, **_k: None)
    logger = logging.getLogger("fastmdx")
    kept = (logger.propagate, logger.level, list(logger.handlers),
            fastmdx_logging._console_handler)
    try:
        assert main(argv) == 0
    finally:
        logger.propagate, logger.level = kept[:2]
        logger.handlers[:] = kept[2]
        fastmdx_logging._console_handler = kept[3]
    return seen


@pytest.mark.parametrize(
    ("phase", "interval_flag"),
    [("analyze", "--saving-interval-ps"),
     ("explore", "--analyze-saving-interval-ps")],
)
def test_explicit_interval_flows_through_each_cli_to_trimmed_strided_dcd(
        tmp_path, monkeypatch, phase, interval_flag):
    path, topology = _trajectory(tmp_path / "input")
    # The manual setting is authoritative over an optional join sidecar.
    path.with_suffix(path.suffix + ".join.json").write_text(
        json.dumps({"saving_interval_ps": 99.0}), encoding="utf-8")
    root = tmp_path / "study"
    if phase == "analyze":
        argv = ["analyze", "--system", str(topology), "--output", str(root),
                "--trajectory", str(path), "--topology", str(topology),
                "--analyses", "rmsd", "--scope", "all", "--stride", "2",
                "--first", "1", "--last", "3", interval_flag, "12"]
    else:
        argv = ["explore", "--system", str(topology), "--output", str(root),
                "--include-phase", "analysis", "--analyze-trajectory", str(path),
                "--analyze-topology", str(topology), "--analyze-analyses", "rmsd",
                "--analyze-scope", "all", "--analyze-stride", "2",
                "--analyze-first", "1", "--analyze-last", "3",
                interval_flag, "12"]

    seen = _run_cli(monkeypatch, argv)

    assert seen["load_kwargs"]["saving_interval_ps"] == 12.0
    # DCD source frames 2 and 4 remain after stride, then frame 2 is trimmed.
    assert seen["time"] == pytest.approx([36.0, 60.0])
    manifest = json.loads((root / "analysis" / "analysis_manifest.json").read_text(
        encoding="utf-8"))
    assert manifest["load_kwargs"]["saving_interval_ps"] == 12.0
    assert manifest["resolved"]["saving_interval_ps"] == 12.0


def test_absent_setting_uses_trajectory_sidecar(tmp_path, monkeypatch):
    path, topology = _trajectory(tmp_path / "input")
    path.with_suffix(path.suffix + ".join.json").write_text(
        json.dumps({"saving_interval_ps": 8.0}), encoding="utf-8")

    seen = _run_cli(monkeypatch, [
        "explore", "--system", str(topology), "--output", str(tmp_path / "study"),
        "--include-phase", "analysis", "--analyze-trajectory", str(path),
        "--analyze-topology", str(topology), "--analyze-analyses", "rmsd",
        "--analyze-scope", "all",
    ])

    assert seen["load_kwargs"]["saving_interval_ps"] == 8.0
    assert seen["time"] == pytest.approx(np.arange(1, 9) * 8.0)


def test_explicit_setting_overrides_fixed_run_record_and_sidecar(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from fastmdxplora.analysis import analyze

    path, topology = _trajectory(tmp_path / "input")
    path.with_suffix(path.suffix + ".join.json").write_text(
        json.dumps({"saving_interval_ps": 99.0}), encoding="utf-8")
    root = tmp_path / "study"
    simulation = root / "simulation"
    simulation.mkdir(parents=True)
    (simulation / "simulation_parameters.json").write_text(json.dumps({
        "parameters": {"integrator": "langevin_middle",
                       "trajectory_interval_steps": 4, "timestep_fs": 2.0},
        "n_production_frames": 8, "duration_ns_actual": 0.016,
    }), encoding="utf-8")
    seen = {}
    original = analyze.AnalysisOrchestrator

    class RecordingOrchestrator(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            seen["interval"] = self._load_kwargs["saving_interval_ps"]
            seen["time"] = np.asarray(self.traj.time).copy()

    monkeypatch.setattr(analyze, "AnalysisOrchestrator", RecordingOrchestrator)
    analyze.run(
        orchestrator=SimpleNamespace(output_dir=root, _presenter=None),
        output_dir=root / "analysis", trajectory=str(path),
        topology=str(topology), scope="all", include=["rmsd"],
        saving_interval_ps=12.0,
    )

    assert seen["interval"] == 12.0
    assert seen["time"] == pytest.approx(np.arange(1, 9) * 12.0)


@pytest.mark.parametrize("interval", [0, -1, float("nan"), float("inf")])
def test_nonpositive_or_nonfinite_intervals_are_refused_by_config(interval):
    from fastmdxplora.config.loader import ConfigError, validate_config

    with pytest.raises(ConfigError) as refused:
        validate_config({"systems": [{"system": "synthetic.pdb"}],
                         "analysis": {"saving_interval_ps": interval}})
    assert refused.value.code == "config.option.out_of_range"


@pytest.mark.parametrize("interval", [0, -1, float("nan"), float("inf")])
def test_nonpositive_or_nonfinite_intervals_are_refused_by_loader(tmp_path, interval):
    from fastmdxplora.analysis.loading import TrajectoryLoadError, load_trajectory

    path, topology = _trajectory(tmp_path / "input")
    with pytest.raises(TrajectoryLoadError) as refused:
        load_trajectory(path, topology, saving_interval_ps=interval)
    assert refused.value.code == "analysis.time.interval_invalid"


def test_unknown_interval_stays_a_frame_axis(tmp_path):
    from fastmdxplora.analysis.base import Analysis
    from fastmdxplora.analysis.loading import load_trajectory

    path, topology = _trajectory(tmp_path / "input")
    loaded = load_trajectory(path, topology)

    class Probe(Analysis):
        name = "probe"

        def compute(self):  # pragma: no cover - only the inherited axis is used
            raise NotImplementedError

        def plot(self, result, ax):  # pragma: no cover - only the inherited axis is used
            raise NotImplementedError

    probe = Probe.__new__(Probe)
    probe._user_xunit = None
    values, label = Analysis.frame_axis(probe, loaded)
    assert label == "Frame"
    assert np.array_equal(values, np.arange(loaded.n_frames))


def test_native_timestamp_clock_wins_over_explicit_interval(tmp_path):
    from fastmdxplora.analysis.loading import load_trajectory

    path, topology = _trajectory(tmp_path / "input", suffix="xtc")
    loaded = load_trajectory(path, topology, saving_interval_ps=100.0)
    assert loaded.time == pytest.approx(np.arange(8) * 4.0, abs=0.01)


def test_variable_step_study_refuses_explicit_interval(tmp_path):
    from types import SimpleNamespace

    from fastmdxplora.analysis.analyze import run
    from fastmdxplora.refusals import CodedError

    path, topology = _trajectory(tmp_path / "input")
    simulation = tmp_path / "study" / "simulation"
    simulation.mkdir(parents=True)
    (simulation / "simulation_parameters.json").write_text(json.dumps({
        "parameters": {"integrator": "variable_langevin"},
    }), encoding="utf-8")

    with pytest.raises(CodedError, match="variable-step") as refused:
        run(orchestrator=SimpleNamespace(output_dir=tmp_path / "study", _presenter=None),
            output_dir=tmp_path / "study" / "analysis", trajectory=str(path),
            topology=str(topology), saving_interval_ps=12.0)
    assert refused.value.code == "analysis.time.interval_unavailable"
