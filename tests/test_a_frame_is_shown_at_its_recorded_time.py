"""A frame is shown at the time it was written, as the analyses plot it.

OpenMM's reporter writes frame k at (k + 1) saving intervals, and the
analyses plot that clock (`gui/series.analysed_axis`). The Viewer spread its
frames evenly from 0 to the run's length: 100 frames of 10 ns read 0,
0.101, ... 10 where they were 0.1, 0.2, ... 10, so a frame in the Viewer was
a frame's spacing from the same frame on the Analysis page. A DCD written
through MDTraj records no clock, so the interval is read from the records:
what the analyses read the trajectory at, else the run's own saving
interval and timestep. The reading of the run's record is Derrick Kwan's
(`fix/viewer-recorded-frame-times`).
"""

from __future__ import annotations

import json
from pathlib import Path

import mdtraj as md
import numpy as np
import pytest

from fastmdxplora.gui.trajectory_frames import FRAMES_INDEX, frames_info

FRAMES = 10


def _study(root: Path) -> Path:
    (root / "simulation").mkdir(parents=True)
    topology = md.Topology()
    residue = topology.add_residue("ALA", topology.add_chain())
    for name in ("N", "CA", "C"):
        topology.add_atom(name, md.element.carbon, residue)
    xyz = np.zeros((FRAMES, 3, 3), dtype=np.float32)
    xyz[:, 1, 1] = 0.15
    xyz[:, 2, 2] = 0.15
    trajectory = md.Trajectory(xyz, topology)
    trajectory[0].save_pdb(str(root / "simulation" / "trajectory_topology.pdb"))
    trajectory.save_dcd(str(root / "simulation" / "production.dcd"))
    return root


def _run_record(root: Path, *, interval_steps=500, timestep_fs=2.0,
                integrator="langevin_middle", **more) -> None:
    (root / "simulation" / "simulation_parameters.json").write_text(json.dumps({
        "parameters": {"timestep_fs": timestep_fs, "integrator": integrator},
        "resolved": {"trajectory_interval_steps": interval_steps,
                     "production_steps": interval_steps * FRAMES},
        **more}), encoding="utf-8")


def _analysed(root: Path, *, interval_ps: float, read_at: str) -> None:
    (root / "analysis").mkdir(exist_ok=True)
    (root / "analysis" / "analysis_manifest.json").write_text(json.dumps({
        "trajectory_input": read_at, "resolved": {"trajectory": read_at},
        "load_kwargs": {"stride": None, "first": None, "saving_interval_ps": interval_ps}}),
        encoding="utf-8")


def test_without_a_record_the_frames_are_spread_as_before(tmp_path):
    root = _study(tmp_path / "s")
    said = frames_info(root, simulation_time_ns_total=0.009)
    assert said["frame_times_ns"][0] == 0 and said["frame_times_ns"][-1] == pytest.approx(0.009)


def test_the_run_s_record_gives_each_frame_its_time(tmp_path):
    root = _study(tmp_path / "s")
    _run_record(root)
    said = frames_info(root, simulation_time_ns_total=0.01)
    # 500 steps of 2 fs: a frame each 0.001 ns, the first at 0.001.
    assert said["frame_times_ns"] == pytest.approx([0.001 * (k + 1) for k in range(FRAMES)])


@pytest.mark.parametrize("record", [{"timestep_fs": 0}, {"interval_steps": 0},
                                    {"continues": True}])
def test_a_record_that_cannot_say_it_is_not_used(tmp_path, record):
    root = _study(tmp_path / "s")
    _run_record(root, **record)
    said = frames_info(root, simulation_time_ns_total=0.009)
    assert said["frame_times_ns"][0] == 0


@pytest.mark.parametrize("integrator", ["variable_langevin", "variable_verlet"])
def test_a_run_that_chose_its_own_step_is_given_no_time(tmp_path, integrator):
    """Its frames are a fixed number of steps apart, not a fixed time: no
    time is spread over them, nor taken from an analysis that gave one."""
    root = _study(tmp_path / "s")
    _run_record(root, integrator=integrator)
    _analysed(root, interval_ps=5.0, read_at=str(root / "simulation" / "production.dcd"))
    assert frames_info(root, simulation_time_ns_total=0.009)["frame_times_ns"] == [None] * FRAMES
    # Read again from what was written: still none.
    assert frames_info(root, simulation_time_ns_total=0.009)["frame_times_ns"] == [None] * FRAMES

    from fastmdxplora.analysis.analyze import _saving_interval_ps

    assert _saving_interval_ps(root) is None


def test_a_run_still_going_is_known_by_its_config(tmp_path):
    """Before the run writes its record, the study's resolved config says
    which integrator it has: a run going, or killed before its record."""
    root = _study(tmp_path / "s")
    (root / "resolved_config.yml").write_text(
        "simulation:\n  integrator: variable_langevin\n", encoding="utf-8")
    assert frames_info(root, simulation_time_ns_total=0.009)["frame_times_ns"] == [None] * FRAMES


@pytest.mark.parametrize("integrator, said", [
    ("variable_langevin", [None, None, None]), ("langevin_middle", [0.0, 0.1, 0.2])])
def test_a_run_s_snapshots_are_timed_only_where_its_step_is_fixed(tmp_path, integrator, said):
    """While it runs the Viewer plays its snapshots, each timed at its step
    times the timestep: no time where the integrator chose its steps."""
    root = _study(tmp_path / "s")
    simulation = root / "simulation"
    pdb = (simulation / "trajectory_topology.pdb").read_text()
    (simulation / "live_frames").mkdir()
    for k in range(3):
        (simulation / "live_frames" / f"frame_{k}.pdb").write_text(pdb)
    (simulation / "live_frame_history.json").write_text(json.dumps({"frames": [
        {"path": f"live_frames/frame_{k}.pdb", "sequence": k, "frame_index": k,
         "mtime_ns": k, "simulation_time_ns": 0.1 * k} for k in range(3)]}))
    (simulation / "production.dcd").unlink()
    (root / "resolved_config.yml").write_text(
        f"simulation:\n  integrator: {integrator}\n", encoding="utf-8")
    shown = frames_info(root)
    assert shown["source_kind"] == "live-history"
    if said[0] is None:
        assert shown["frame_times_ns"] == said
    else:
        assert shown["frame_times_ns"] == pytest.approx(said)


def test_an_analysis_made_before_is_plotted_by_frame(tmp_path):
    """An analysis made before a variable step was known to give no clock
    recorded one; the pages plot its series by frame."""
    root = _study(tmp_path / "s")
    _run_record(root, integrator="variable_verlet")
    _analysed(root, interval_ps=5.0, read_at=str(root / "simulation" / "production.dcd"))
    from fastmdxplora.gui.figure_data import _clock
    from fastmdxplora.gui.series import analysed_axis

    frames, axis, name = analysed_axis(root, 3)
    assert name == "Frame" and axis == [0.0, 1.0, 2.0]
    assert _clock(root, [0, 1, 2])[2] == "Frame"


def test_the_analyses_clock_is_the_one_shown(tmp_path):
    root = _study(tmp_path / "s")
    _run_record(root)
    # Read where the study ran before it was moved here.
    _analysed(root, interval_ps=5.0, read_at="/elsewhere/3ptb-demo/simulation/production.dcd")
    said = frames_info(root)
    assert said["frame_times_ns"] == pytest.approx([0.005 * (k + 1) for k in range(FRAMES)])

    from fastmdxplora.gui.series import analysed_axis, of_the_played_trajectory

    assert of_the_played_trajectory(root)
    assert analysed_axis(root, FRAMES)[1] == pytest.approx(said["frame_times_ns"])


def test_another_trajectory_analysed_is_not_its_clock(tmp_path):
    root = _study(tmp_path / "s")
    _run_record(root)
    elsewhere = tmp_path / "other.dcd"
    elsewhere.write_bytes(b"")
    _analysed(root, interval_ps=5.0, read_at=str(elsewhere))
    assert frames_info(root)["frame_times_ns"][0] == pytest.approx(0.001)


def test_frames_written_before_are_given_their_times(tmp_path):
    root = _study(tmp_path / "s")
    first = frames_info(root, simulation_time_ns_total=0.009)
    assert first["frame_times_ns"][0] == 0
    _run_record(root)
    said = frames_info(root, simulation_time_ns_total=0.009)
    assert said["compiled_at"] == first["compiled_at"]
    assert said["frame_times_ns"][0] == pytest.approx(0.001)
    assert json.loads((root / "simulation" / FRAMES_INDEX).read_text())["frame_times_ns"][0] == 0
