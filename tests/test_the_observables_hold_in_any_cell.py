"""Observables that hold whatever the cell and however the frames were given.

- g(r) and the coordination number stopped at half the shortest cell edge,
  which in a squashed cell (still a reduced one, as GROMACS and OpenMM
  define it) is past half the shortest lattice vector: g(r) of an ideal gas
  fell to 0.74 between the two.
- The centre-of-mass minimum image searched one cell out and was 0.115 nm
  long in that cell.
- The coordination number's `cutoff: rdf` took its noise from the spread of
  g(r) over the outer third, so a crystal's first shell was refused, and its
  first minimum was the lowest point anywhere after the peak.
- An analysis given a trajectory with molecules split across the box read
  the pieces: a rigid body wrapped atom by atom read an RMSD of 2.7 nm.
"""

from __future__ import annotations

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")

from fastmdxplora.analysis.coordination_number import (  # noqa: E402
    CoordinationNumber,
    _first_minimum,
)
from fastmdxplora.analysis.pair_distance import _unambiguous_reach  # noqa: E402
from fastmdxplora.analysis.rdf import RadialDistribution as RDF  # noqa: E402
from fastmdxplora.simulation.seeding import shortest_vector  # noqa: E402

#: Reduced by GROMACS's and OpenMM's rules, and squashed: its shortest
#: lattice vector is c - b, 1.62 nm, while its shortest edge is 2.21 nm.
SQUASHED = np.array([[3.0, 0.0, 0.0], [1.5, 2.7, 0.0], [1.5, 1.35, 0.9]])


def _atoms(n, name="AR"):
    top = md.Topology()
    chain = top.add_chain()
    for _ in range(n):
        top.add_atom(name, md.element.argon, top.add_residue(name, chain))
    return top


def _in_cell(xyz, cell):
    traj = md.Trajectory(xyz.astype(np.float32), _atoms(xyz.shape[1]))
    traj.unitcell_vectors = np.repeat(cell[None], len(xyz), axis=0).astype(np.float32)
    return traj


def _gas(cell, n_atoms=600, n_frames=20, seed=0):
    rng = np.random.default_rng(seed)
    return _in_cell(rng.random((n_frames, n_atoms, 3)) @ cell, cell)


class TestASquashedCell:

    def test_the_reach_is_half_the_shortest_lattice_vector(self):
        reach = float(_unambiguous_reach(SQUASHED[None])[0])
        assert reach == pytest.approx(0.5 * np.linalg.norm(SQUASHED[2] - SQUASHED[1]))
        assert reach < 0.5 * float(np.min(np.linalg.norm(SQUASHED, axis=1)))

    def test_an_ideal_gas_reads_one_up_to_where_the_rdf_stops(self, tmp_path):
        traj = _gas(SQUASHED)
        analysis = RDF(selection_a="all", selection_b="all", output_dir=str(tmp_path))
        table = analysis.compute(traj)
        radii, g = np.asarray(table[:, 0]), np.asarray(table[:, 1])
        assert radii.max() <= float(_unambiguous_reach(SQUASHED[None])[0]) + 1e-6
        outer = (radii > 0.5) & np.isfinite(g)
        assert abs(float(np.mean(g[outer])) - 1.0) < 0.03
        assert float(np.min(g[outer])) > 0.85

    def test_a_cutoff_past_it_is_refused(self, tmp_path):
        with pytest.raises(ValueError, match="shortest lattice vector"):
            CoordinationNumber(selection_a="all", selection_b="all", cutoff=1.0,
                               output_dir=str(tmp_path)).compute(_gas(SQUASHED, n_frames=2))

    def test_the_minimum_image_is_exact_there(self):
        rng = np.random.default_rng(1)
        delta = (rng.random((300, 3)) - 0.5) @ SQUASHED * 2.0
        cells = np.repeat(SQUASHED[None], len(delta), axis=0)
        found = np.linalg.norm(shortest_vector(delta, cells), axis=1)
        shifts = np.array([s for s in np.ndindex(7, 7, 7)], dtype=float) - 3.0
        images = delta[:, None, :] + (shifts @ SQUASHED)[None]
        exact = np.linalg.norm(images, axis=2).min(axis=1)
        assert np.allclose(found, exact, atol=1e-9)


def _fcc(n=6, a=0.5, sigma=0.02, frames=10, seed=0):
    base = np.array([[0, 0, 0], [0.5, 0.5, 0], [0.5, 0, 0.5], [0, 0.5, 0.5]])
    points = np.array([(np.array([i, j, k]) + b) * a for i in range(n)
                       for j in range(n) for k in range(n) for b in base])
    rng = np.random.default_rng(seed)
    xyz = points[None] + rng.normal(0.0, sigma, (frames, len(points), 3))
    return _in_cell(xyz, np.eye(3) * n * a)


class TestACrystalHasAFirstShell:

    def test_its_cutoff_is_read_and_counts_twelve(self, tmp_path):
        analysis = CoordinationNumber(selection_a="all", selection_b="all",
                                      output_dir=str(tmp_path))
        counts = analysis.compute(_fcc())
        cutoff = analysis.findings["shell"]["cutoff_from_rdf_nm"]
        # Between the first shell at a/sqrt(2) = 0.354 and the second at a.
        assert 0.36 < cutoff < 0.5
        assert float(np.mean(counts)) == pytest.approx(12.0, abs=0.1)

    @pytest.mark.parametrize("frames", [1, 5, 20])
    def test_a_gas_with_no_shell_is_refused(self, tmp_path, frames):
        """A selection paired with itself lists each pair twice, and the
        noise is of the pairs that are distinct: over 15 seeds, none taken."""
        accepted = 0
        for seed in range(15):
            try:
                CoordinationNumber(selection_a="all", selection_b="all",
                                   output_dir=str(tmp_path)).compute(
                    _gas(np.eye(3) * 3.0, n_atoms=300, n_frames=frames, seed=seed))
                accepted += 1
            except ValueError:
                pass
        assert accepted == 0

    def test_a_frozen_gas_counts_as_one_frame(self, tmp_path):
        """Fifty copies of one configuration are one sample, not fifty."""
        one = _gas(np.eye(3) * 3.0, n_atoms=300, n_frames=1, seed=4)
        frozen = _in_cell(np.repeat(one.xyz, 50, axis=0), np.eye(3) * 3.0)
        with pytest.raises(ValueError, match="no first shell resolved"):
            CoordinationNumber(selection_a="all", selection_b="all",
                               output_dir=str(tmp_path)).compute(frozen)


class TestTheFirstMinimumIsTheFirst:

    @staticmethod
    def _r():
        return np.arange(0.0, 1.5, 0.005) + 0.0025

    def test_a_low_peak_is_not_its_own_minimum(self):
        """Zero padding pulled the smoothed curve under bulk at the peak, so
        a peak under 1.5 was returned as the cutoff."""
        r = self._r()
        g = (1.0 + 0.35 * np.exp(-((r - 0.50) / 0.04) ** 2)
             - 0.2 * np.exp(-((r - 0.62) / 0.04) ** 2))
        g[r < 0.3] = 0.0
        cutoff, reason = _first_minimum(r, g, np.full_like(g, 0.01))
        assert not reason
        assert cutoff == pytest.approx(0.62, abs=0.015)

    def test_the_first_resolved_dip_not_a_lower_second(self):
        r = self._r()
        g = (1.0 + 1.8 * np.exp(-((r - 0.28) / 0.03) ** 2)
             - 0.12 * np.exp(-((r - 0.36) / 0.03) ** 2)
             - 0.2 * np.exp(-((r - 0.55) / 0.04) ** 2))
        g[r < 0.2] = 0.0
        cutoff, reason = _first_minimum(r, g, np.full_like(g, 0.005))
        assert not reason
        assert cutoff == pytest.approx(0.36, abs=0.02)

    def test_a_flat_trough_has_no_minimum_to_read(self):
        r = self._r()
        g = 1.0 + 1.8 * np.exp(-((r - 0.28) / 0.03) ** 2)
        trough = (r > 0.36) & (r < 0.6)
        g[trough] = 0.97
        ramp = (r >= 0.33) & (r <= 0.36)
        g[ramp] = np.linspace(1.3, 0.97, int(ramp.sum()))
        g[r < 0.2] = 0.0
        cutoff, reason = _first_minimum(r, g, np.full_like(g, 0.05))
        assert np.isnan(cutoff) and "no minimum to read" in reason

    def test_not_a_deeper_trough_past_the_second_shell(self):
        r = np.arange(0.0, 1.5, 0.005) + 0.0025
        g = (1.0 + 2.0 * np.exp(-((r - 0.28) / 0.03) ** 2)
             - 0.3 * np.exp(-((r - 0.34) / 0.03) ** 2)
             + 0.4 * np.exp(-((r - 0.45) / 0.04) ** 2)
             - 0.6 * np.exp(-((r - 0.62) / 0.04) ** 2))
        g[r < 0.2] = 0.0
        cutoff, reason = _first_minimum(r, g, np.full_like(g, 0.01))
        assert not reason
        assert cutoff == pytest.approx(0.34, abs=0.015)


def _rigid_body_wrapped(frames=12, seed=3, wrapped=True):
    """A bonded chain of 10 atoms tumbling rigidly in a 3 nm cube, stored
    wrapped atom by atom, as a plain `md.load` of another engine's file gives."""
    from scipy.spatial.transform import Rotation

    top = md.Topology()
    residue = top.add_residue("ALA", top.add_chain(), resSeq=1)
    atoms = [top.add_atom(f"C{i}", md.element.carbon, residue) for i in range(10)]
    for first, second in zip(atoms, atoms[1:]):
        top.add_bond(first, second)
    body = np.array([[0.15 * i, 0.05 * (i % 2), 0.0] for i in range(10)])
    body -= body.mean(axis=0)
    rng = np.random.default_rng(seed)
    centre = [2.9, 1.5, 1.5] if wrapped else [1.5, 1.5, 1.5]
    xyz = np.array([Rotation.random(random_state=rng).apply(body) + centre
                    for _ in range(frames)])
    if wrapped:
        xyz %= 3.0
    traj = md.Trajectory(xyz.astype(np.float32), top)
    traj.unitcell_vectors = np.repeat((np.eye(3) * 3.0)[None], frames, 0).astype(np.float32)
    return traj


class TestASplitMoleculeIsMadeWhole:

    def test_its_rmsd_is_the_molecule_s(self, tmp_path):
        from fastmdxplora.analysis.rmsd import RMSD

        traj = _rigid_body_wrapped()
        analysis = RMSD(selection="all", output_dir=str(tmp_path))
        result = analysis.run(traj)
        assert result.status == "ok"
        # float32 rounding of a rigid fit; read on the pieces it was over 0.5.
        assert float(np.max(np.asarray(result.data))) < 5e-3
        assert float(np.max(md.rmsd(traj, traj, 0))) > 0.5   # and left as given
        assert "made whole" in analysis.findings["made_whole"]

    def test_a_split_in_a_few_frames_is_found(self, tmp_path):
        """Straddling a face in 16 of 200 frames read a radius of gyration of
        2.88 nm against 0.91 when only ten frames were looked at."""
        from fastmdxplora.analysis.rg import Rg

        whole = _rigid_body_wrapped(frames=200, wrapped=False)
        expected = Rg(selection="all", output_dir=str(tmp_path / "w")).compute(whole)
        traj = _rigid_body_wrapped(frames=200, wrapped=False)
        traj.xyz[136:152] += np.float32([1.4, 0.0, 0.0])
        traj.xyz[136:152] %= np.float32(3.0)
        analysis = Rg(selection="all", output_dir=str(tmp_path / "s"))
        result = analysis.run(traj)
        assert np.allclose(np.asarray(result.data), expected, atol=1e-4)
        assert "made whole" in analysis.findings["made_whole"]

    def test_imaging_that_fails_is_said_and_not_tried_again(self, tmp_path, monkeypatch):
        from fastmdxplora.analysis import base, loading
        from fastmdxplora.analysis.rmsd import RMSD

        tried = []

        def fails(trajectory):
            tried.append(1)
            return trajectory

        monkeypatch.setattr(loading, "_made_whole", fails)
        traj = _rigid_body_wrapped()
        for name in ("a", "b"):
            analysis = RMSD(selection="all", output_dir=str(tmp_path / name))
            analysis.run(traj)
            assert "could not all be made whole" in analysis.findings["made_whole"]
        assert len(tried) == 1 and getattr(traj, base.IMAGING_FAILED)

    def test_a_whole_trajectory_is_left_as_it_is(self, tmp_path):
        from fastmdxplora.analysis.rmsd import RMSD

        traj = _rigid_body_wrapped(wrapped=False)
        analysis = RMSD(selection="all", output_dir=str(tmp_path))
        analysis.run(traj)
        assert "made_whole" not in analysis.findings

    def test_a_failed_run_still_says_it(self, tmp_path, monkeypatch):
        import json

        from fastmdxplora.analysis.rmsd import RMSD

        def broken(self, traj):
            raise ValueError("broken on purpose")

        monkeypatch.setattr(RMSD, "compute", broken)
        result = RMSD(selection="all", output_dir=str(tmp_path)).run(_rigid_body_wrapped())
        assert result.status == "error"
        written = json.loads(result.options_path.read_text())
        assert "made whole" in written["findings"]["made_whole"]


def _water_box(frames=4, seed=2, wrapped=True):
    """64 rigid waters in a 1.25 nm cube, stored wrapped atom by atom."""
    top = md.Topology()
    chain = top.add_chain()
    for _ in range(64):
        residue = top.add_residue("HOH", chain)
        o = top.add_atom("O", md.element.oxygen, residue)
        for name in ("H1", "H2"):
            top.add_bond(o, top.add_atom(name, md.element.hydrogen, residue))
    rng = np.random.default_rng(seed)
    grid = (np.array(list(np.ndindex(4, 4, 4))) + 0.5) * 0.3125
    shape = np.array([[0.0, 0.0, 0.0], [0.0957, 0.0, 0.0], [-0.024, 0.0927, 0.0]])
    xyz = np.repeat((grid[:, None, :] + shape[None]).reshape(1, -1, 3), frames, 0)
    xyz = xyz + rng.normal(0.0, 0.02, (frames, 64, 1, 3)).repeat(3, 2).reshape(frames, -1, 3)
    xyz += np.float64(0.12)              # pushes the last layer's hydrogens over a face
    if wrapped:
        xyz %= 1.25
    traj = md.Trajectory(xyz.astype(np.float32), top)
    traj.unitcell_vectors = np.repeat((np.eye(3) * 1.25)[None], frames, 0).astype(np.float32)
    return traj


class TestSolventAloneIsWhole:

    def test_the_loader_makes_it_whole(self, tmp_path):
        from fastmdxplora.analysis import base
        from fastmdxplora.analysis.loading import load_trajectory

        traj = _water_box()
        bonds, widths = base._bonds_and_widths(traj)
        assert base._split_frames(traj, bonds, widths).all()
        traj[0].save_pdb(str(tmp_path / "w.pdb"))
        traj.save_dcd(str(tmp_path / "w.dcd"))
        loaded = load_trajectory(tmp_path / "w.dcd", tmp_path / "w.pdb")
        assert not base._split_frames(loaded, bonds, widths).any()
        assert base._checked_whole(loaded)

    def test_a_whole_box_its_imaging_failed_on_is_not_said_to_be_split(self, tmp_path):
        from fastmdxplora.analysis import base
        from fastmdxplora.analysis.rg import Rg

        traj = _water_box(wrapped=False)
        setattr(traj, base.IMAGING_FAILED, True)
        analysis = Rg(selection="all", output_dir=str(tmp_path))
        assert analysis.run(traj).status == "ok"
        assert "made_whole" not in analysis.findings

    def test_what_the_loader_measured_is_not_measured_again(self, tmp_path, monkeypatch):
        from fastmdxplora.analysis import base
        from fastmdxplora.analysis.orchestrator import AnalysisOrchestrator  # noqa: F401
        from fastmdxplora.analysis.rg import Rg

        traj = _water_box(wrapped=False)
        base.mark_wholeness(traj)
        measured = []
        real = base._split_frames
        monkeypatch.setattr(base, "_split_frames",
                            lambda *a: measured.append(1) or real(*a))
        copy = base.carry_wholeness(traj, traj[:])
        Rg(selection="all", output_dir=str(tmp_path)).run(copy)
        assert measured == []


def _rock_salt(sigma, frames, n=5, a=0.564, seed=0):
    base = np.array([[0, 0, 0], [0.5, 0.5, 0], [0.5, 0, 0.5], [0, 0.5, 0.5]])
    na = np.array([(np.array([i, j, k]) + b) * a for i in range(n)
                   for j in range(n) for k in range(n) for b in base])
    points = np.vstack([na, na + [a / 2, 0.0, 0.0]])
    rng = np.random.default_rng(seed)
    xyz = (points[None] + rng.normal(0.0, sigma, (frames, len(points), 3))) % (n * a)
    top = md.Topology()
    chain = top.add_chain()
    for name in ["NA"] * len(na) + ["CL"] * len(na):
        top.add_atom(name, md.element.argon, top.add_residue(name, chain))
    traj = md.Trajectory(xyz.astype(np.float32), top)
    traj.unitcell_vectors = np.repeat((np.eye(3) * n * a)[None], frames, 0).astype(np.float32)
    return traj


class TestACrystalIsReadAcrossItsGap:
    """The flat stretch between a cold crystal's shells was refused as a
    trough with no minimum, though the count is the same anywhere in it."""

    @pytest.mark.parametrize("a,sigma,frames", [
        (0.5, 0.002, 1), (0.5, 0.005, 10), (0.6, 0.005, 10), (0.7, 0.01, 10)])
    def test_fcc_reads_twelve_from_the_middle_of_the_gap(self, tmp_path, a, sigma, frames):
        analysis = CoordinationNumber(selection_a="all", selection_b="all",
                                      output_dir=str(tmp_path))
        counts = analysis.compute(_fcc(a=a, sigma=sigma, frames=frames))
        shell = analysis.findings["shell"]
        low, high = shell["cutoff_range_nm"]
        assert a / np.sqrt(2) < low < shell["cutoff_nm"] < high < a
        assert shell["cutoff_nm"] == pytest.approx(0.5 * (low + high))
        assert float(np.mean(counts)) == pytest.approx(12.0, abs=1e-6)
        assert "level" in shell["said"]

    @pytest.mark.parametrize("sigma", [0.005, 0.01])
    def test_rock_salt_reads_six(self, tmp_path, sigma):
        analysis = CoordinationNumber(selection_a="name NA", selection_b="name CL",
                                      output_dir=str(tmp_path))
        counts = analysis.compute(_rock_salt(sigma, frames=4))
        assert float(np.mean(counts)) == pytest.approx(6.0, abs=1e-6)


class TestWhereTheMinimumCouldBeIsSaid:

    def test_the_range_and_the_halves_are_recorded(self, tmp_path):
        analysis = CoordinationNumber(selection_a="all", selection_b="all",
                                      output_dir=str(tmp_path))
        analysis.compute(_fcc(sigma=0.04, frames=10))
        shell = analysis.findings["shell"]
        low, high = shell["cutoff_range_nm"]
        assert low <= shell["cutoff_nm"] <= high
        first, second = shell["coordination_number_range"]
        assert first < second
        assert len(shell["cutoff_from_each_half_nm"]) == 2
        assert "Read from each half of the run" in shell["said"]
        from fastmdxplora.report.document import _findings_notes

        assert shell["said"] in _findings_notes(analysis.findings)

    def test_a_noisy_flat_trough_is_refused(self):
        """Level at 0.97 from 0.36 to 0.6 nm, with 0.01 of noise in each bin:
        a dip in the noise had been taken for the minimum 8 times in 50."""
        r = np.arange(0.0, 1.5, 0.005) + 0.0025
        clean = 1.0 + 1.8 * np.exp(-((r - 0.28) / 0.03) ** 2)
        clean[(r > 0.36) & (r < 0.6)] = 0.97
        ramp = (r >= 0.33) & (r <= 0.36)
        clean[ramp] = np.linspace(1.3, 0.97, int(ramp.sum()))
        refused = 0
        for seed in range(50):
            g = clean + np.random.default_rng(seed).normal(0.0, 0.01, r.size)
            g[r < 0.2] = 0.0
            refused += bool(_first_minimum(r, g, np.full_like(g, 0.01))[1])
        assert refused >= 47

    def test_a_dip_that_climbs_back_only_part_way_is_inside_the_trough(self):
        """A wiggle that stays below bulk is not the end of the first shell."""
        r = np.arange(0.0, 1.5, 0.005) + 0.0025
        g = (1.0 + 1.8 * np.exp(-((r - 0.28) / 0.03) ** 2)
             - 0.3 * np.exp(-((r - 0.36) / 0.02) ** 2)
             - 0.35 * np.exp(-((r - 0.43) / 0.02) ** 2))
        g[r < 0.2] = 0.0
        cutoff, reason = _first_minimum(r, g, np.full_like(g, 0.005))
        assert not reason
        assert cutoff == pytest.approx(0.43, abs=0.01)


    def test_coordinates_changed_after_loading_are_measured_again(self, tmp_path):
        """Wrapped into the cell by a caller after loading, the mark held and
        Rg read 1.265 nm against 1.336 with no note."""
        from fastmdxplora.analysis import base
        from fastmdxplora.analysis.rg import Rg

        traj = _rigid_body_wrapped(wrapped=False)
        base.mark_wholeness(traj)
        assert base._checked_whole(traj)
        expected = Rg(selection="all", output_dir=str(tmp_path / "w")).compute(traj)
        traj.xyz[:] = (traj.xyz + np.float32(1.4)) % np.float32(3.0)
        assert not base._checked_whole(traj)
        analysis = Rg(selection="all", output_dir=str(tmp_path / "s"))
        result = analysis.run(traj)
        assert np.allclose(np.asarray(result.data), expected, atol=1e-4)
        assert "made whole" in analysis.findings["made_whole"]


class TestTheHistogramIsMDTraj_s:

    @pytest.mark.parametrize("cell,width,r_max", [
        (np.eye(3) * 3.0, 0.005, 1.4), (SQUASHED, 0.0123, 0.8), (SQUASHED, 0.1, 0.5)])
    def test_g_is_compute_rdf_s(self, cell, width, r_max):
        from fastmdxplora.analysis.rdf import PairHistogram, _pairs_between

        traj = _gas(cell, n_atoms=300, n_frames=12, seed=7)
        traj.unitcell_vectors = (traj.unitcell_vectors
                                 * np.linspace(0.97, 1.03, 12)[:, None, None]).astype(np.float32)
        pairs, _ = _pairs_between(np.arange(300), np.arange(300), np.random.default_rng(0))
        radii, g = md.compute_rdf(traj, pairs, r_range=(0.0, r_max), bin_width=width)
        histogram = PairHistogram(traj, pairs, r_max, width)
        assert np.array_equal(radii, histogram.radii)
        assert np.array_equal(g, histogram.g())

    def test_its_scatter_is_the_spread_over_repeats(self):
        """An ideal gas: the scatter said against the scatter seen over 60
        independent repeats, bin by bin past 0.3 nm."""
        from fastmdxplora.analysis.rdf import PairHistogram, _pairs_between

        pairs, _ = _pairs_between(np.arange(200), np.arange(200), np.random.default_rng(0))
        curves, said = [], []
        for seed in range(60):
            histogram = PairHistogram(_gas(np.eye(3) * 3.0, n_atoms=200, n_frames=12,
                                           seed=100 + seed), pairs, 1.4, 0.05)
            curves.append(histogram.g())
            said.append(histogram.scatter())
        outer = histogram.radii > 0.3
        ratio = np.mean(np.asarray(said)[:, outer], axis=0) / np.std(curves, axis=0)[outer]
        assert 0.85 < float(np.median(ratio)) < 1.3

    def test_an_empty_cell_and_an_empty_bin_are_refused(self, tmp_path):
        from fastmdxplora.refusals import StudyError

        traj = _gas(np.eye(3) * 3.0, n_frames=3)
        cells = traj.unitcell_vectors
        cells[1] = 0.0
        traj.unitcell_vectors = cells
        with pytest.raises(StudyError, match="empty or not a number"):
            RDF(selection_a="all", selection_b="all", output_dir=str(tmp_path)).compute(traj)
        with pytest.raises(StudyError, match="empty or not a number"):
            CoordinationNumber(selection_a="all", selection_b="all", cutoff=0.4,
                               output_dir=str(tmp_path)).compute(traj)
        with pytest.raises(StudyError, match="positive"):
            RDF(selection_a="all", selection_b="all", bin_width=0.0)
        with pytest.raises(StudyError, match="less than one bin"):
            RDF(selection_a="all", selection_b="all", r_max=0.001,
                output_dir=str(tmp_path)).compute(_gas(np.eye(3) * 3.0, n_frames=2))


class TestTheHalvesOfTheRunAreHeard:

    @staticmethod
    def _cn(tmp_path, monkeypatch, halves):
        """A crystal read whole, its halves standing in for a run whose
        minimum moves, or which a half cannot resolve."""
        from fastmdxplora.analysis import coordination_number as cn

        real = cn._first_minimum
        calls = []

        def staged(radii, g, noise, smooth_noise=None, record=None):
            calls.append(1)
            if len(calls) == 1:
                return real(radii, g, noise, smooth_noise, record)
            value = halves[len(calls) - 2]
            return (float("nan"), "none here") if value is None else (value, "")

        monkeypatch.setattr(cn, "_first_minimum", staged)
        analysis = CoordinationNumber(selection_a="all", selection_b="all",
                                      output_dir=str(tmp_path))
        analysis.compute(_fcc(sigma=0.04, frames=10))
        return analysis.findings["shell"]

    def test_a_half_that_resolves_nothing_is_said(self, tmp_path, monkeypatch):
        shell = self._cn(tmp_path, monkeypatch, [None, 0.47])
        assert shell["cutoff_from_each_half_nm"] == [None, 0.47]
        assert "The first half of the run alone resolves no minimum" in shell["said"]

    def test_a_minimum_that_moves_with_the_frames_is_refused(self, tmp_path, monkeypatch):
        from fastmdxplora.refusals import StudyError

        with pytest.raises(StudyError, match="moves with the frames read"):
            self._cn(tmp_path, monkeypatch, [0.47, 0.62])

    def test_one_no_half_resolves_is_refused(self, tmp_path, monkeypatch):
        from fastmdxplora.refusals import StudyError

        with pytest.raises(StudyError, match="neither half of the run"):
            self._cn(tmp_path, monkeypatch, [None, None])
