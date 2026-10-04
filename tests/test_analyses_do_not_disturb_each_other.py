"""One analysis must not change what another measures.

Found on a 20 ns trypsin-benzamidine benchmark. `pl_interactions`
reported 252 hydrophobic contacts when it ran after rmsf and ligand_rmsd,
and 10 when it ran alone, on the same trajectory in the same container on
the same day. Every recorded setting was identical, because what differed
was not a setting: mdtraj's `superpose` rotates coordinates in place and
returns the same object, so an analysis that aligned left every later
analysis reading rotated coordinates.

The second fault compounded it. Rotation does not rotate
`unitcell_vectors`, so minimum-image distances taken afterwards map atoms
through a box that no longer describes the frame. The same ligand-protein
pair measured 1.64 nm before alignment and 1.83 nm after. The number in
company was the wrong one.

Ten mechanisms were proposed and eliminated before this was found --
platform, library versions, container image, our own code by direct diff,
bond topology, ligand chemistry, atom typing, frame counts, atom ordering,
determinism. None of them was it, and none of them could have been seen
in `options.json`, because "which other analyses ran" is not a setting.
"""

from __future__ import annotations

import mdtraj as md
import numpy as np


def _system(n_frames: int = 60, seed: int = 0):
    """A ligand near a protein, in a box, drifting a little."""
    rng = np.random.RandomState(seed)
    top = md.Topology()
    chain = top.add_chain()
    positions = []
    for i in range(12):
        res = top.add_residue("ALA", chain, resSeq=i + 1)
        for name in ("N", "CA", "C", "CB"):
            top.add_atom(name, md.element.carbon, res)
            positions.append([i * 0.35, 0.1 * len(positions) % 0.4, 0.0])
    lig = top.add_chain()
    res = top.add_residue("BEN", lig, resSeq=99)
    for name in ("C1x", "C2x", "C3x"):
        top.add_atom(name, md.element.carbon, res)
        positions.append([1.0, 0.45, 0.0])

    xyz = np.tile(np.array(positions)[None], (n_frames, 1, 1))
    # A slow rigid drift, so superposition has something to remove.
    xyz += np.linspace(0, 0.6, n_frames)[:, None, None] * np.array([1.0, 0, 0])
    xyz += rng.normal(scale=0.01, size=xyz.shape)
    traj = md.Trajectory(xyz.astype(np.float32), top)
    traj.unitcell_lengths = np.tile([4.0, 4.0, 4.0], (n_frames, 1))
    traj.unitcell_angles = np.tile([90.0, 90.0, 90.0], (n_frames, 1))
    return traj


class TestSuperpositionIsNotContagious:
    def test_aligning_leaves_the_original_untouched(self):
        """The whole bug, in one assertion."""
        from fastmdxplora.analysis.base import superposed

        traj = _system()
        before = traj.xyz.copy()
        superposed(traj, frame=0, atom_indices=traj.topology.select("name CA"))

        assert np.array_equal(traj.xyz, before), (
            "superposing must not rotate the caller's trajectory")

    def test_mdtraj_alone_would_have(self):
        """Stated so the helper is not mistaken for ceremony.

        `traj.superpose(...)` mutates and returns the same object. Anything
        that calls it directly reintroduces the defect.
        """
        traj = _system()
        before = traj.xyz.copy()
        traj.superpose(traj, frame=0,
                       atom_indices=traj.topology.select("name CA"))

        assert not np.array_equal(traj.xyz, before)

    def test_the_box_is_dropped_rather_than_left_stale(self):
        """A rotated frame's box describes nothing.

        Left in place, minimum-image distances are computed through a cell
        that no longer corresponds to the coordinates, which does not fail
        -- it answers wrongly. Absent is better than stale, because stale
        is the one a caller cannot detect.
        """
        from fastmdxplora.analysis.base import superposed

        traj = _system()
        aligned = superposed(traj, frame=0,
                             atom_indices=traj.topology.select("name CA"))

        assert traj.unitcell_vectors is not None
        assert aligned.unitcell_vectors is None

    def test_fitted_to_another_structure_its_atoms_matched(self):
        """The Viewer fits frames to the structure a study started from or
        to the deposited one by the same function: the reference's atoms
        named one by one against the trajectory's, neither changed."""
        from fastmdxplora.analysis.base import superposed

        traj = _system()
        alpha = traj.topology.select("name CA")
        reference = md.Trajectory(traj.xyz[5:6, alpha] + 1.0, traj.topology.subset(alpha))
        before, kept = traj.xyz.copy(), reference.xyz.copy()
        aligned = superposed(traj, atom_indices=alpha, reference=reference,
                             ref_atom_indices=np.arange(len(alpha)))
        assert np.array_equal(traj.xyz, before) and np.array_equal(reference.xyz, kept)
        assert aligned.unitcell_vectors is None
        # Frame 5 is the reference moved, so it is fitted onto it exactly.
        assert np.abs(aligned.xyz[5, alpha] - reference.xyz[0]).max() < 1e-4


class TestAnAnalysisGivesTheSameAnswerInCompany:
    """The behavioural test, at the level a user would notice."""

    @staticmethod
    def _contacts(traj, names):
        from fastmdxplora.analysis.orchestrator import AnalysisOrchestrator

        import tempfile
        from pathlib import Path

        ao = AnalysisOrchestrator.__new__(AnalysisOrchestrator)
        ao.traj = traj
        ao.ligand_resname = "BEN"
        ao.output_dir = Path(tempfile.mkdtemp())
        ao.results = {}
        ao.scope_selection = None
        ao.selection = None
        return ao

    def test_alone_and_after_an_aligning_analysis_agree(self, tmp_path):
        """`pl_interactions` after `rmsf` must equal `pl_interactions` alone.

        This is the failure as a user met it: same trajectory, same
        settings, different answer depending on the company it kept.
        """
        from fastmdxplora.analysis.pl_interactions import (
            ProteinLigandInteractions,
        )
        from fastmdxplora.analysis.rmsf import RMSF

        traj = _system()

        alone = ProteinLigandInteractions(
            ligand_resname="BEN", kinds=("hydrophobic",), periodic=True,
            output_dir=tmp_path / "a").compute(traj[:])

        shared = traj[:]
        RMSF(output_dir=tmp_path / "b").compute(shared)
        after = ProteinLigandInteractions(
            ligand_resname="BEN", kinds=("hydrophobic",), periodic=True,
            output_dir=tmp_path / "c").compute(shared)

        assert len(alone) == len(after), (
            f"pl_interactions found {len(alone)} contacts alone and "
            f"{len(after)} after an analysis that superposes")

    def test_the_orchestrator_hands_out_copies(self):
        """Checked in the source, because the failure is invisible in the
        result until two analyses are combined."""
        import inspect
        from pathlib import Path

        from fastmdxplora.analysis import orchestrator

        text = Path(inspect.getfile(orchestrator)).read_text(encoding="utf-8")
        assert "analysis.run(self.traj[:])" in text, (
            "each analysis must receive its own trajectory copy")
        assert "analysis.run(self.traj)" not in text.replace(
            "analysis.run(self.traj[:])", ""), (
            "the shared trajectory must not be handed to any analysis")
