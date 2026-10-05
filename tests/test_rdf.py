"""g(r), checked against the one distribution whose answer is known exactly."""

from __future__ import annotations

import mdtraj as md
import numpy as np
import pytest

from fastmdxplora.analysis.rdf import RadialDistribution


def _gas(n_a=40, n_b=400, box=4.0, frames=25, seed=3):
    """Points placed at random in a periodic box: no structure at all."""
    rng = np.random.default_rng(seed)
    top = md.Topology()
    solute = top.add_chain()
    for i in range(n_a):
        res = top.add_residue("ALA", solute, resSeq=i + 1)
        top.add_atom("CA", md.element.carbon, res)
    water = top.add_chain()
    for i in range(n_b):
        res = top.add_residue("HOH", water, resSeq=i + 1)
        top.add_atom("O", md.element.oxygen, res)
    xyz = rng.random((frames, n_a + n_b, 3)) * box
    traj = md.Trajectory(xyz.astype(np.float32), top)
    traj.unitcell_lengths = np.tile([box, box, box], (frames, 1))
    traj.unitcell_angles = np.tile([90.0, 90.0, 90.0], (frames, 1))
    return traj


class TestTheNormalisation:
    """The part of an RDF that is easy to get subtly wrong.

    A histogram of distances is trivial; dividing it by what the same
    histogram would have been without interactions is where the volume
    element, the pair count and the box all enter. Randomly placed points
    have no structure, so their g(r) is one at every separation, and that
    is an exact answer rather than a comparison against another
    implementation.
    """

    def test_random_points_give_one_everywhere(self):
        result = RadialDistribution(
            selection_a="name CA", selection_b="name O",
            bin_width=0.05).compute(_gas())
        radii, g = result[:, 0], result[:, 1]
        middle = (radii > 0.4) & (radii < 1.9)

        assert abs(float(np.mean(g[middle])) - 1.0) < 0.02
        assert float(np.max(np.abs(g[middle] - 1.0))) < 0.1


class TestTheHalfBoxLimit:
    """Past L/2 the shell is incomplete and g(r) sags for no physical reason.

    It sags smoothly, so the curve looks like structure rather than like an
    artefact, which is why the range is capped rather than explained in a
    caption.
    """

    def test_the_range_stops_at_half_the_box(self):
        analysis = RadialDistribution(
            selection_a="name CA", selection_b="name O", r_max=5.0)
        result = analysis.compute(_gas(box=4.0))

        assert analysis.findings["rdf"]["r_max_nm"] == pytest.approx(2.0)
        assert float(np.max(result[:, 0])) <= 2.0
        assert "half the smallest box dimension" in (
            analysis.findings["rdf"]["capped"])

    def test_a_request_inside_the_box_is_left_alone(self):
        analysis = RadialDistribution(
            selection_a="name CA", selection_b="name O", r_max=1.0)
        analysis.compute(_gas(box=4.0))

        assert analysis.findings["rdf"]["r_max_nm"] == pytest.approx(1.0)
        assert "capped" not in analysis.findings["rdf"]


class TestWhatItRefuses:
    def test_a_trajectory_without_a_box(self):
        traj = _gas()
        bare = md.Trajectory(traj.xyz, traj.topology)
        with pytest.raises(ValueError, match="no unit cell"):
            RadialDistribution(
                selection_a="name CA", selection_b="name O").compute(bare)

    def test_a_selection_that_matched_nothing(self):
        with pytest.raises(ValueError, match="matched no atoms"):
            RadialDistribution(
                selection_a="name CA",
                selection_b="resname NOPE").compute(_gas())


class TestItJoinsTheRegisters:
    def test_the_schema_names_it(self):
        from fastmdxplora.config.schema import ANALYSIS_NAMES

        assert "rdf" in ANALYSIS_NAMES

    def test_it_declares_that_it_ignores_the_scope_selection(self):
        """It carries two selections of its own.

        A third, naming neither group, would silently describe a different
        pairing than the one asked for.
        """
        assert RadialDistribution.honours_selection is False

    @staticmethod
    def _plan(traj):
        import tempfile
        from pathlib import Path

        from fastmdxplora.analysis.orchestrator import AnalysisOrchestrator

        orchestrator = AnalysisOrchestrator.__new__(AnalysisOrchestrator)
        orchestrator.traj = traj
        orchestrator.ligand_resname = None
        orchestrator.output_dir = Path(tempfile.mkdtemp())
        return orchestrator._build_plan(None, None)

    def test_it_is_left_out_of_a_system_with_no_box(self):
        """Not planned and then refused: the system poses no question.

        The same category as water sites on a run with no water, and the
        distinction the orchestrator's other gates were written for.
        """
        boxed = _gas()
        assert "rdf" in self._plan(boxed)

        bare = md.Trajectory(boxed.xyz, boxed.topology)
        assert "rdf" not in self._plan(bare)


class TestTheOutputs:
    def test_it_plots_against_the_unstructured_line(self):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        analysis = RadialDistribution(
            selection_a="name CA", selection_b="name O", bin_width=0.05)
        result = analysis.compute(_gas())
        _fig, ax = plt.subplots()
        analysis.plot(result, ax)

        assert ax.lines, "the curve is drawn"
        assert any(line.get_linestyle() == ":" for line in ax.lines), (
            "g(r) = 1 is drawn, because it is what the curve means")
        assert analysis.default_xlabel() == "r (nm)"
        assert analysis.default_ylabel() == "g(r)"
        plt.close("all")

    def test_the_first_peak_is_reported(self):
        analysis = RadialDistribution(
            selection_a="name CA", selection_b="name O", bin_width=0.05)
        analysis.compute(_gas())
        record = analysis.findings["rdf"]
        assert "first_peak_nm" in record
        assert record["first_peak_nm"] > 0.15


def _every_pair_then_a_draw(a, b, limit, seed=0):
    """The pairs as the analysis listed them before: every (i, j) with
    i != j, a-major, then ``limit`` positions drawn by the same generator."""
    pairs = np.array([(int(i), int(j)) for i in a for j in b if int(i) != int(j)],
                     dtype=int)
    if len(pairs) > limit:
        keep = np.random.default_rng(seed).choice(len(pairs), limit, replace=False)
        pairs = pairs[np.sort(keep)]
    return pairs


class TestThePairsAreDrawnWithoutListingThemAll:
    """A protein against every water oxygen is tens of millions of pairs,
    and they were listed one Python tuple at a time before 400,000 were
    drawn: about 25 s and gigabytes for 3e7 pairs."""

    @pytest.mark.parametrize("a, b", [
        (np.arange(30), np.arange(30)),                 # one selection twice
        (np.arange(30), np.arange(20, 70)),             # overlapping
        (np.arange(0, 60, 2), np.arange(100, 150)),     # apart
        (np.array([5, 3, 9]), np.array([9, 1, 5, 7])),  # unsorted
    ])
    @pytest.mark.parametrize("limit", [10, 50, 10_000])
    def test_the_same_pairs_as_the_full_list(self, a, b, limit):
        from fastmdxplora.analysis.rdf import _pairs_between

        pairs, available = _pairs_between(a, b, np.random.default_rng(0), limit)
        expected = _every_pair_then_a_draw(a, b, limit)
        assert available == sum(1 for i in a for j in b if i != j)
        np.testing.assert_array_equal(pairs, expected)

    def test_a_protein_against_ten_thousand_waters_is_quick(self, monkeypatch):
        """3,000 atoms against 10,000 oxygens, built through the analysis."""
        import time

        from fastmdxplora.analysis import rdf as rdf_module

        calls = []

        def counted(traj, pairs, **kwargs):
            calls.append(np.asarray(pairs))
            return np.array([0.1]), np.array([1.0])

        monkeypatch.setattr(rdf_module.md, "compute_rdf", counted)
        traj = _gas(n_a=3000, n_b=10000, frames=1)
        analysis = RadialDistribution(selection_a="name CA", selection_b="name O")
        started = time.perf_counter()
        analysis.compute(traj)
        took = time.perf_counter() - started
        record = analysis.findings["rdf"]
        assert record["subsampled"] and record["pairs"] == rdf_module.MAX_PAIRS
        assert "of the 30000000 available" in record["subsampled_note"]
        assert len(calls[0]) == rdf_module.MAX_PAIRS
        assert len({tuple(p) for p in calls[0][:1000]}) == 1000
        assert took < 5.0, f"{took:.1f} s to choose the pairs"

    def test_one_selection_against_itself_has_no_self_pair(self):
        from fastmdxplora.analysis.rdf import _pairs_between

        same = np.arange(2000)
        pairs, available = _pairs_between(same, same, np.random.default_rng(0), 50_000)
        assert available == 2000 * 1999
        assert not np.any(pairs[:, 0] == pairs[:, 1])
