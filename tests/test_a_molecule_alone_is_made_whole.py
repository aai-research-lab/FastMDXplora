"""A molecule alone in a periodic box is made whole.

MDTraj images around anchor molecules it chooses by size: those larger than
the one a tenth of the way down a ranking. A molecule alone in the box is
that molecule, never larger than itself, so no anchor was found and imaging
raised. The benchmark helper and both seeding calls asked MDTraj to choose,
and failed there. `image_whole` falls back to the largest molecule where
MDTraj's choice is empty, and otherwise passes the anchors it would have
chosen, so a solvated system is imaged exactly as before.
"""

from __future__ import annotations

import numpy as np
import pytest

from tests.test_cross_tool_survives_the_move import _a_chain_split_across_the_box

md = pytest.importorskip("mdtraj")


def _longest_bond(frames, bonds) -> float:
    return float(md.compute_distances(frames, bonds, periodic=False).max())


def test_a_molecule_alone_in_the_box_is_made_whole():
    from fastmdxplora.analysis.loading import image_whole

    alone, bonds = _a_chain_split_across_the_box(box=4.0, waters=0)
    with pytest.raises(ValueError, match="anchor molecules"):
        alone.image_molecules(inplace=False)       # what every caller used to hit
    assert _longest_bond(alone, bonds) > 1.0
    assert _longest_bond(image_whole(alone, inplace=False), bonds) < 0.2


def test_a_solvated_system_is_imaged_exactly_as_mdtraj_would():
    from fastmdxplora.analysis.loading import image_whole

    solvated, _ = _a_chain_split_across_the_box(box=4.0, ligand=True)
    # Two anchors, as a protein and its ligand are: always anchoring on the
    # largest would move the ligand relative to the protein.
    assert len(solvated.topology.guess_anchor_molecules()) == 2
    ours = image_whole(solvated, inplace=False)
    theirs = solvated.image_molecules(inplace=False)
    assert np.array_equal(ours.xyz, theirs.xyz)


def test_the_benchmark_heals_a_molecule_alone(tmp_path):
    from fastmdxplora.validation.cross_tool import healed_trajectory

    alone, bonds = _a_chain_split_across_the_box(box=4.0, waters=0)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    raw, top = run_dir / "production.dcd", run_dir / "topology.pdb"
    alone.save_dcd(str(raw))
    alone[0].save_pdb(str(top))
    healed = md.load(str(healed_trajectory(run_dir, raw, top)), top=str(top))
    assert _longest_bond(healed, bonds) < 0.2
