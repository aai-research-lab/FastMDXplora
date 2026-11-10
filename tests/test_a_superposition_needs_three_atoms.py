"""A rotation cannot be fitted to one point.

RMSD and RMSF align on `name CA` by default. Alanine dipeptide has one CA, so
MDTraj's C extension printed "UNCONVERGED ROTATION MATRIX. RETURNING IDENTITY"
once per frame -- a window's log filled with thousands of lines of it -- and
returned distances measured against no alignment at all. Those are not large
errors; they are a column of numbers that looks like results.

Reported as inapplicable rather than attempted, the way a chain too short to
have a fold already is.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import inspect

import mdtraj as md
import numpy as np
import pytest

from fastmdxplora.analysis.base import Analysis
from fastmdxplora.analysis.rmsd import RMSD
from fastmdxplora.analysis.rmsf import RMSF


def _trajectory(n_ca: int, frames: int = 3) -> md.Trajectory:
    """A trajectory whose protein carries `n_ca` alpha carbons."""
    top = md.Topology()
    chain = top.add_chain()
    for _ in range(n_ca):
        residue = top.add_residue("ALA", chain)
        for name, element in (("N", md.element.nitrogen),
                              ("CA", md.element.carbon),
                              ("C", md.element.carbon),
                              ("O", md.element.oxygen)):
            top.add_atom(name, element, residue)
    xyz = np.random.default_rng(0).random((frames, top.n_atoms, 3)).astype(np.float32)
    return md.Trajectory(xyz, top)


class TestTheThresholdIsDeclared:
    def test_the_superposing_analyses_ask_for_three(self) -> None:
        assert RMSD.min_atoms_to_align == 3
        assert RMSF.min_atoms_to_align == 3

    def test_every_analysis_that_superposes_is_covered(self) -> None:
        """The first version gated `rmsd` and `rmsf` -- the two being looked
        at -- and left `cluster` and `dimred`, which align the same way. A
        real run then clustered a capped alanine on identity rotations, found
        one distinct cluster where five were asked for, and reported ok.

        Asked of the source rather than listed by hand, so an analysis that
        starts superposing later cannot quietly join them.
        """
        from pathlib import Path as _Path

        from fastmdxplora.analysis.orchestrator import _REGISTRY

        # The ligand analyses are left out, and not because they are safe.
        # Their `default_selection` is None -- what they align on is resolved
        # at run time from the ligand's residue name -- so the gate, which
        # runs at plan time against a static selection, cannot evaluate them.
        # Setting a threshold on them would be inert and would read as
        # coverage. Gating a dynamic selection needs the check moved to where
        # the selection is resolved, which is its own change.
        resolved_at_run_time = {"ligand_rmsd", "ligand_rmsf"}

        missing = []
        for name, cls in _REGISTRY.items():
            if name in resolved_at_run_time:
                continue
            source = _Path(inspect.getfile(cls)).read_text(encoding="utf-8")
            superposes = (".superpose(" in source
                          or "_superposed_coordinates(" in source)
            if superposes and int(getattr(cls, "min_atoms_to_align", 0)) < 3:
                missing.append(name)
        assert not missing, f"these superpose and are not gated: {missing}"

    def test_analyses_that_do_not_align_ask_for_nothing(self) -> None:
        """The gate must not exclude an analysis that never superposes."""
        from fastmdxplora.analysis.rg import Rg

        assert Analysis.min_atoms_to_align == 0
        assert getattr(Rg, "min_atoms_to_align", 0) == 0


class TestAMoleculeTooSmallToAlignIsSkipped:
    @staticmethod
    def _plan(traj: md.Trajectory) -> list[str]:
        from fastmdxplora.analysis.orchestrator import AnalysisOrchestrator

        orchestrator = AnalysisOrchestrator.__new__(AnalysisOrchestrator)
        orchestrator.traj = traj
        orchestrator.ligand_resname = None
        # A directory that holds nothing, so the method-gated analyses find
        # no biased run beside them and leave themselves out.
        orchestrator.output_dir = Path(tempfile.mkdtemp())
        return orchestrator._build_plan(None, None)

    def test_one_alpha_carbon_leaves_them_out(self) -> None:
        """Alanine dipeptide is the real case: one CA, and the aligner
        silently gave up on every frame."""
        plan = self._plan(_trajectory(1))
        assert "rmsd" not in plan and "rmsf" not in plan

    def test_two_are_still_not_enough(self) -> None:
        """Two points fix an axis and leave a rotation about it free."""
        plan = self._plan(_trajectory(2))
        assert "rmsd" not in plan and "rmsf" not in plan

    def test_three_are(self) -> None:
        plan = self._plan(_trajectory(3))
        assert "rmsd" in plan and "rmsf" in plan

    def test_a_real_protein_is_untouched(self) -> None:
        plan = self._plan(_trajectory(20))
        assert "rmsd" in plan and "rmsf" in plan

    def test_the_analyses_that_do_not_align_still_run(self) -> None:
        """A molecule too small to superpose still has a radius of gyration
        and hydrogen bonds; the gate is about alignment, not about size."""
        plan = self._plan(_trajectory(1))
        assert "rg" in plan


class TestRunOnOneAtomItIsRefused:
    """The plan leaves them out by their default selection, but an analysis
    named in `include` (as the GUI writes a study's analyses) was run: on
    alanine dipeptide's one CA every RMSD read 0, and clustering found one
    cluster, said as results, while dimred refused with "nothing to
    decompose"."""

    @pytest.mark.parametrize("name", ["rmsd", "cluster", "dimred"])
    def test_it_is_refused_with_the_selection_that_works(self, name) -> None:
        from fastmdxplora.analysis.orchestrator import get_analysis_class
        from fastmdxplora.refusals import StudyError, refusal_of

        # Frames enough for five clusters.
        traj = _trajectory(1, frames=20)
        with pytest.raises(StudyError) as raised:
            get_analysis_class(name)().compute(traj)
        refusal = refusal_of(raised.value)
        assert refusal.code == "analysis.selection.arity"
        said = str(raised.value)
        # What to set first, with the atoms it matches here.
        assert said.startswith("Set `selection: backbone`"), said
        assert "matches 4 atoms here" in said and "matches 1 atom here" in said, said

    def test_run_whole_it_fails_and_says_why_and_its_selection_works(self, tmp_path) -> None:
        import re

        traj = _trajectory(1)
        result = RMSD(output_dir=tmp_path).run(traj)
        assert result.status == "error"
        assert not (tmp_path / "rmsd" / "rmsd.dat").exists()
        named = re.search(r"`selection: ([^`]+)`", result.message)
        assert named, result.message
        rmsd = RMSD(selection=named.group(1)).compute(traj)
        assert rmsd[0] == 0.0 and np.all(rmsd[1:] > 0.0)
