"""A time axis is the run's clock, the frame number, or nothing.

Every one of these fails with the fix reverted, and the first one fails by
a factor of fifty on the trajectory this package actually writes.

The defect they close: MDTraj's DCD reader discards the timestep in the
file header and returns ``time`` equal to the frame index in picoseconds.
``frame_axis`` took that, divided by 1000, and labelled it "Time (ns)", so a
100 ns run saved every 50 ps drew an axis running to 2 ns. Audited across
one workstation, 75 of 75 trajectories carried a fabricated clock. The guard
that should have caught it asked whether the clock *varied*, and a frame
index varies perfectly.
"""

from __future__ import annotations

import json

import mdtraj as md
import numpy as np
import pytest

from fastmdxplora.analysis.loading import load_trajectory

# Imported inside the tests that need it rather than here. A module-scope
# import of a symbol a fix introduces turns "the fix is missing" into a
# collection error that takes the whole file down and names no assertion --
# the failure mode patch 0009 recorded when `tomllib` aborted 3,414 tests.


def _topology(n_atoms: int = 4) -> md.Topology:
    top = md.Topology()
    chain = top.add_chain()
    residue = top.add_residue("ALA", chain)
    for index in range(n_atoms):
        top.add_atom(f"C{index}", md.element.carbon, residue)
    return top


def _topology_pdb(tmp_path) -> str:
    """A real PDB on disk, because load_trajectory takes a path."""
    path = tmp_path / "topology.pdb"
    _trajectory(1, 1.0)[0].save_pdb(str(path))
    return str(path)


def _trajectory(n_frames: int, interval_ps: float) -> md.Trajectory:
    top = _topology()
    xyz = np.random.RandomState(0).rand(
        n_frames, top.n_atoms, 3).astype(np.float32)
    traj = md.Trajectory(xyz, top)
    traj.time = (np.arange(n_frames, dtype=np.float64) + 1.0) * interval_ps
    traj.unitcell_lengths = np.tile([5.0, 5.0, 5.0], (n_frames, 1))
    traj.unitcell_angles = np.tile([90.0, 90.0, 90.0], (n_frames, 1))
    return traj


class TestDCDLosesTheClockAndWeReplaceIt:
    """The format drops it; the run's own record puts it back."""

    def test_mdtraj_returns_the_frame_index_for_a_dcd(self, tmp_path):
        """The premise, asserted rather than assumed.

        If MDTraj ever starts honouring the DCD header this test fails, and
        that is the point: the workaround below would then be unnecessary
        and should be reconsidered rather than left in place.
        """
        traj = _trajectory(2000, 50.0)
        path = tmp_path / "production.dcd"
        traj.save_dcd(str(path))

        back = md.load(str(path), top=_topology())

        assert back.n_frames == 2000
        # 100 ns written, 2 ns read back.
        assert back.time[-1] - back.time[0] == pytest.approx(1999.0)

    def test_the_interval_puts_the_real_clock_back(self, tmp_path):
        traj = _trajectory(2000, 50.0)
        path = tmp_path / "production.dcd"
        traj.save_dcd(str(path))

        loaded = load_trajectory(
            path, top=_topology_pdb(tmp_path), saving_interval_ps=50.0)

        assert loaded.n_frames == 2000
        # 2000 frames x 50 ps = 100 ns, and the first frame is at one
        # interval rather than at zero because a reporter fires after its
        # first interval.
        assert float(loaded.time[0]) == pytest.approx(50.0)
        assert float(loaded.time[-1]) == pytest.approx(100_000.0)

    def test_without_an_interval_the_clock_is_absent_not_invented(
            self, tmp_path):
        traj = _trajectory(500, 0.2)
        path = tmp_path / "production.dcd"
        traj.save_dcd(str(path))

        loaded = load_trajectory(path, top=_topology_pdb(tmp_path))

        assert np.all(np.isnan(loaded.time)), (
            "A frame index is not a clock. Where the interval is unknown the "
            "time must be marked absent, so a figure draws frames rather "
            "than inventing nanoseconds.")

    def test_a_format_that_carries_its_own_clock_is_left_alone(self, tmp_path):
        """XTC round-trips the clock, so nothing here should touch it."""
        traj = _trajectory(200, 50.0)
        path = tmp_path / "production.xtc"
        traj.save_xtc(str(path))

        loaded = load_trajectory(path, top=_topology_pdb(tmp_path))

        assert float(loaded.time[-1]) == pytest.approx(10_000.0, rel=1e-4)
        assert np.all(np.isfinite(loaded.time))

    def test_stride_stretches_the_interval(self, tmp_path):
        traj = _trajectory(1000, 10.0)
        path = tmp_path / "production.dcd"
        traj.save_dcd(str(path))

        loaded = load_trajectory(
            path, top=_topology_pdb(tmp_path), stride=10,
            saving_interval_ps=10.0)

        assert loaded.n_frames == 100
        # Every tenth frame of a 10 ps series is 100 ps apart.
        gaps = np.diff(np.asarray(loaded.time, dtype=float))
        assert np.allclose(gaps, 100.0)

    def test_the_marker_survives_slicing(self, tmp_path):
        """NaN is the carrier because it is the only one that survives.

        An attribute set on the Trajectory does not survive ``traj[a:b]``,
        which analyses do routinely when they drop relaxation frames.
        """
        traj = _trajectory(50, 1.0)
        path = tmp_path / "production.dcd"
        traj.save_dcd(str(path))

        loaded = load_trajectory(path, top=_topology_pdb(tmp_path))

        assert np.all(np.isnan(loaded[10:20].time))
        assert np.all(np.isnan(loaded.atom_slice([0, 1]).time))


class TestFrameAxisRefusesAClockItCannotTrust:

    def _axis(self, traj):
        from fastmdxplora.analysis.base import Analysis

        class _Probe(Analysis):
            name = "probe"

            def compute(self):  # pragma: no cover - not exercised
                return None

            def plot(self, result, ax):  # pragma: no cover - not exercised
                return None

        probe = _Probe.__new__(_Probe)
        probe._user_xunit = None
        return Analysis.frame_axis(probe, traj)

    def test_a_nan_clock_gives_a_frame_axis(self):
        traj = _trajectory(20, 1.0)
        traj.time = np.full(20, np.nan, dtype=np.float32)

        values, label = self._axis(traj)

        assert label == "Frame"
        assert np.array_equal(values, np.arange(20))

    def test_a_real_clock_still_gives_nanoseconds(self):
        traj = _trajectory(20, 50.0)

        values, label = self._axis(traj)

        assert label == "Time (ns)"
        assert float(values[-1]) == pytest.approx(1.0)


class TestTheIntervalIsReadFromTheRunsOwnRecord:

    @staticmethod
    def _interval(root):
        from fastmdxplora.analysis.analyze import _saving_interval_ps
        return _saving_interval_ps(root)

    def _write(self, root, payload):
        (root / "simulation").mkdir(parents=True, exist_ok=True)
        (root / "simulation" / "simulation_parameters.json").write_text(
            json.dumps(payload), encoding="utf-8")

    def test_the_reporter_interval_is_preferred(self, tmp_path):
        self._write(tmp_path, {
            "parameters": {
                "trajectory_interval_steps": 25000, "timestep_fs": 2.0},
            "n_production_frames": 2000,
            "duration_ns_actual": 100.0,
        })
        assert self._interval(tmp_path) == pytest.approx(50.0)

    def test_the_summary_fields_are_the_fallback(self, tmp_path):
        self._write(tmp_path, {
            "parameters": {},
            "n_production_frames": 2000,
            "duration_ns_actual": 100.0,
        })
        assert self._interval(tmp_path) == pytest.approx(50.0)

    def test_a_run_without_a_record_gets_nothing(self, tmp_path):
        assert self._interval(tmp_path) is None

    def test_a_partial_record_gets_nothing_rather_than_a_guess(self, tmp_path):
        self._write(tmp_path, {
            "parameters": {"timestep_fs": 2.0},
            "n_production_frames": 2000,
        })
        assert self._interval(tmp_path) is None


class TestReweightingRefusesAClockItCannotUse:
    """The number the fabricated clock corrupted, not just the axis.

    ``reweight`` places each frame in the deposition history by comparing
    this clock against PLUMED's, which is real simulated time. On a 100 ns
    run read as 2 ns every frame was matched against the first 2% of the
    hills, so the reweighting under-corrected a fully biased ensemble --
    and left the effective sample size looking healthy, so nothing
    complained.
    """

    def test_a_nan_clock_is_refused_with_a_reason(self, tmp_path):
        from fastmdxplora.analysis.reweighted_averages import reweight_results

        run = tmp_path / "analysis"
        run.mkdir()
        # What marks a run as biased is the generated PLUMED input beside it,
        # which is what `biasing_method` looks for.
        (tmp_path / "simulation").mkdir()
        (tmp_path / "simulation" / "metadynamics.plumed").write_text(
            "METAD ARG=cv PACE=500 HEIGHT=1.2\n", encoding="utf-8")

        record = reweight_results(
            {}, {}, n_frames=40,
            frame_times_ps=np.full(40, np.nan), output_dir=run)

        assert record is not None and record["applies"] is False
        assert "saving interval" in record["reason"]
        assert "biased ensemble" in record["reason"]

    def test_an_unbiased_run_is_not_given_a_refusal_it_did_not_earn(
            self, tmp_path):
        """The regression this guard caused when it sat too far upstream.

        ``test_analysis_layer::test_include_filter`` caught it: an ordinary
        MD run with a NaN clock acquired a "reweighted" result saying a bias
        could not be undone, when there had been no bias. The check belongs
        beside this module's own bias detector, not ahead of it.
        """
        from fastmdxplora.analysis.reweighted_averages import reweight_results

        run = tmp_path / "analysis"
        run.mkdir()

        record = reweight_results(
            {}, {}, n_frames=40,
            frame_times_ps=np.full(40, np.nan), output_dir=run)

        assert record is None
