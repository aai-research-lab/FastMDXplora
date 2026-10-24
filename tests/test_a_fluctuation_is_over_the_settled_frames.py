"""A fluctuation is the spread of an ensemble, so it is taken after the run settles.

The RMSF analysis leaves out the frames its RMSD spends equilibrating. Two
analyses that report the same kind of number did not: the B-factor
comparison fitted every frame, so a loop relaxing 0.4 nm over the first fifth
of a run read 57% higher there than in `rmsf.dat`, and the ligand RMSF read
1.7 times the fluctuation of the settled frames for a ligand still settling
into its pocket.
"""

from __future__ import annotations

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")

from fastmdxplora.analysis.bfactor_comparison import BFactorComparison  # noqa: E402
from fastmdxplora.analysis.ligand_rmsf import LigandRMSF  # noqa: E402
from fastmdxplora.analysis.rmsf import RMSF  # noqa: E402

NOISE_NM = 0.03          # per axis, so each atom's RMSF is sqrt(3) x this
TRUE_RMSF = np.sqrt(3.0) * NOISE_NM
FRAMES = 500
RELAXED_BY = 100         # frames over which the relaxation runs


def _chain(n_res, ligand_atoms=0):
    top = md.Topology()
    chain = top.add_chain()
    previous = None
    for i in range(n_res):
        residue = top.add_residue("ALA", chain, resSeq=i + 1)
        n = top.add_atom("N", md.element.nitrogen, residue)
        ca = top.add_atom("CA", md.element.carbon, residue)
        cb = top.add_atom("CB", md.element.carbon, residue)
        top.add_bond(n, ca)
        top.add_bond(ca, cb)
        if previous is not None:
            top.add_bond(previous, n)
        previous = ca
    ligand = []
    if ligand_atoms:
        residue = top.add_residue("LIG", top.add_chain(), resSeq=1)
        previous = None
        for k in range(ligand_atoms):
            atom = top.add_atom(f"C{k}", md.element.carbon, residue)
            ligand.append(atom.index)
            if previous is not None:
                top.add_bond(previous, atom)
            previous = atom
    return top, ligand


def _relaxing(top, moving, seed):
    """Every atom jitters by NOISE_NM; ``moving`` also drifts 0.4 nm over the
    first RELAXED_BY frames and stays there."""
    rng = np.random.default_rng(seed)
    steps = rng.normal(size=(top.n_atoms, 3))
    steps /= np.linalg.norm(steps, axis=1)[:, None]
    base = np.cumsum(steps * 0.15, axis=0)
    xyz = np.repeat(base[None], FRAMES, axis=0)
    xyz += rng.normal(size=xyz.shape) * NOISE_NM
    xyz[:, moving, 0] += 0.4 * np.clip(np.arange(FRAMES) / RELAXED_BY, 0, 1)[:, None]
    return md.Trajectory(xyz.astype(np.float32), top,
                         time=(np.arange(FRAMES) + 1) * 10.0)


class TestTheBFactorComparisonReadsTheFramesTheRMSFReads:

    @staticmethod
    def _both(tmp_path, **options):
        top, _ = _chain(20)
        loop = [a.index for a in top.atoms if 8 <= a.residue.resSeq <= 12]
        traj = _relaxing(top, loop, seed=4)
        deposited = tmp_path / "deposited.pdb"
        traj[0].save_pdb(str(deposited),
                         bfactors=np.linspace(10.0, 40.0, top.n_atoms))
        compared = BFactorComparison(output_dir=str(tmp_path / "b"),
                                     structure=str(deposited), **options)
        rmsf = RMSF(output_dir=str(tmp_path / "r"), **options)
        return compared, compared.compute(traj), rmsf, rmsf.compute(traj)

    @pytest.mark.parametrize("start", ["equilibrated", 2.0])
    def test_its_simulated_column_is_the_rmsf(self, tmp_path, start):
        compared, table, rmsf, per_residue = self._both(tmp_path, start=start)
        assert compared.findings["frames"]["first_frame"] == \
            rmsf.findings["frames"]["first_frame"] > 0
        assert np.allclose(table[:, 1], per_residue[:, 1], atol=1e-6)
        assert table[9, 1] < 1.2 * TRUE_RMSF   # residue 10, in the loop

    def test_one_frame_is_refused_as_the_rmsf_refuses_it(self, tmp_path):
        top, _ = _chain(5)
        traj = _relaxing(top, [], seed=1)[:1]
        deposited = tmp_path / "deposited.pdb"
        traj.save_pdb(str(deposited), bfactors=np.linspace(10.0, 40.0, top.n_atoms))
        with pytest.raises(ValueError, match="one frame has none") as caught:
            BFactorComparison(output_dir=str(tmp_path / "b"), structure=str(deposited),
                              start=0).compute(traj)
        assert caught.value.refusal.code == "analysis.sampling.too_few_frames"

    def test_every_frame_still_on_request(self, tmp_path):
        compared, table, _, _ = self._both(tmp_path, start=0)
        assert compared.findings["frames"]["first_frame"] == 0
        assert table[9, 1] > 1.15 * TRUE_RMSF


def _ligand(seed=5, extra=None):
    """A protein and a six-carbon ring with its ring bonds, settling 0.4 nm
    over the first RELAXED_BY frames; ``extra(xyz, ring)`` changes it after."""
    top, ligand = _chain(15, ligand_atoms=6)
    atoms = [top.atom(i) for i in ligand]
    top.add_bond(atoms[-1], atoms[0])
    traj = _relaxing(top, ligand, seed=seed)
    centre = traj.xyz[:, ligand, :].mean(axis=1)
    angle = np.arange(6) * np.pi / 3
    ring = np.c_[0.139 * np.cos(angle), 0.139 * np.sin(angle), np.zeros(6)]
    rng = np.random.default_rng(seed + 1)
    traj.xyz[:, ligand, :] = (centre[:, None, :] + ring[None]
                              + rng.normal(size=(FRAMES, 6, 3)) * NOISE_NM)
    if extra is not None:
        extra(traj.xyz, ligand, ring, centre)
    return traj


class TestTheLigandRMSFIsTakenAfterThePoseSettles:

    @staticmethod
    def _run(tmp_path, traj, **options):
        analysis = LigandRMSF(output_dir=str(tmp_path), ligand_resname="LIG", **options)
        return analysis, analysis.compute(traj)[:, 1]

    def test_the_settling_is_left_out(self, tmp_path):
        """The receptor does not move here, so only the ligand's own RMSD in
        the receptor frame finds the settling."""
        traj = _ligand()
        analysis, per_atom = self._run(tmp_path / "a", traj)
        frames = analysis.findings["frames"]
        settling = frames["ligand_equilibration_frames_by_rmsd"]
        assert RELAXED_BY * 0.8 <= settling <= RELAXED_BY * 2
        assert frames["first_frame"] == settling
        assert not frames["equilibration_included"]
        assert not frames["more_than_a_third_left_out"]
        # The same frames alone: fitted onto their own first frame rather
        # than the run's, so near rather than equal.
        _, settled = self._run(tmp_path / "b", traj[settling:], start=0)
        assert np.allclose(per_atom, settled, rtol=0.03)
        assert frames["mean_rmsf_over_every_frame_nm"] > 1.5 * per_atom.mean()

    def test_every_frame_on_request_and_the_record_says_the_settling_is_in(
            self, tmp_path):
        analysis, per_atom = self._run(tmp_path, _ligand(), start=0)
        frames = analysis.findings["frames"]
        assert frames["first_frame"] == 0
        assert frames["ligand_equilibration_included"] and frames["equilibration_included"]
        included = [name for name, key in (("receptor's", "equilibration_frames_by_rmsd"),
                                           ("ligand's", "ligand_equilibration_frames_by_rmsd"))
                    if frames[key] > 0]
        listing = (f"the {' and the '.join(included)} "
                   + ("equilibrations are" if len(included) == 2 else "equilibration is")
                   + " among the frames read")
        assert listing in frames["said"]
        assert per_atom.mean() > 1.5 * TRUE_RMSF

    def test_a_ring_turning_onto_itself_is_not_settling(self, tmp_path):
        """A sixth of a turn at frame 300 relabels the ring and moves no atom
        from where an atom was: the ligand RMSD reads it as unmoved."""
        def turn(xyz, ligand, ring, centre):
            xyz[300:, ligand, :] = (centre[300:, None, :] + np.roll(ring, 1, axis=0)[None]
                                    + np.random.default_rng(9).normal(
                                        size=(FRAMES - 300, 6, 3)) * NOISE_NM)
        analysis, _ = self._run(tmp_path, _ligand(extra=turn))
        assert analysis.findings["frames"]["ligand_equilibration_frames_by_rmsd"] < 250

    def test_a_late_start_is_said_with_what_it_may_hide(self, tmp_path):
        """A hop to another pose at frame 330 puts the start near two
        thirds of the run, as a slow settling would."""
        def hop(xyz, ligand, ring, centre):
            xyz[330:, ligand, 1] += 0.6
        analysis, _ = self._run(tmp_path, _ligand(extra=hop))
        frames = analysis.findings["frames"]
        assert frames["more_than_a_third_left_out"]
        assert "a change of pose or an unbinding" in frames["said"]
        assert "ligand_rmsd" in frames["said"]
        assert frames["mean_rmsf_over_every_frame_nm"] > frames["mean_rmsf_nm"]

    def test_a_late_start_given_as_a_time_is_said_as_asked(self, tmp_path):
        analysis, _ = self._run(tmp_path, _ligand(), start=2.0)
        frames = analysis.findings["frames"]
        assert frames["more_than_a_third_left_out"]
        assert "(2 ns on)" in frames["said"]
        assert "as `start` asks" in frames["said"]
        assert "change of pose" not in frames["said"]

    def test_a_late_start_the_receptor_sets_is_the_receptor_s(self, tmp_path,
                                                             monkeypatch):
        """A receptor relaxing for 300 frames, the ligand settled by 130: the
        ligand is not blamed for the frames left out."""
        from fastmdxplora.analysis import starting_frame

        real = starting_frame.first_frame

        def receptor_relaxing(traj, atom_idx, start, ref=0):
            first, record = real(traj, atom_idx, start, ref)
            record.update(equilibration_frames_by_rmsd=300, first_frame=300,
                          n_frames_analysed=traj.n_frames - 300)
            return 300, record

        monkeypatch.setattr(starting_frame, "first_frame", receptor_relaxing)
        analysis, _ = self._run(tmp_path, _ligand())
        frames = analysis.findings["frames"]
        assert frames["first_frame"] == 300 > frames["ligand_equilibration_frames_by_rmsd"]
        assert "receptor's RMSD changed" in frames["said"]
        assert "change of pose" not in frames["said"]

    def test_one_frame_is_refused_as_the_rmsf_refuses_it(self, tmp_path):
        with pytest.raises(ValueError, match="one frame has none") as caught:
            self._run(tmp_path, _ligand()[:1], start=0)
        assert caught.value.refusal.code == "analysis.sampling.too_few_frames"
