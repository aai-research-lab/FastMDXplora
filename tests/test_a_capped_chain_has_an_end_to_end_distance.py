"""A capped peptide's end-to-end distance runs between its alpha carbons.

Once the NHE and NH2 caps were counted as protein (protein_names.py), a
peptide ending in either had the cap as its last residue, which has no
alpha carbon, and end_to_end refused it. The ends are now the first and last
residues holding the named atom, so the caps are passed over, as an ACE or
NME cap is too (one was refused before).
"""

from __future__ import annotations

import mdtraj as md
import numpy as np
import pytest

import fastmdxplora  # noqa: F401  (NHE and NH2 are protein)
from fastmdxplora.analysis.end_to_end import EndToEndDistance

SPACING = 0.38


def _capped(first: str | None, last: str | None, residues: int = 5) -> md.Trajectory:
    top = md.Topology()
    chain = top.add_chain()
    xyz = []
    if first:
        cap = top.add_residue(first, chain, resSeq=0)
        top.add_atom("C", md.element.carbon, cap)
        xyz.append([-SPACING, 0.0, 0.0])
    for i in range(residues):
        residue = top.add_residue("ALA", chain, resSeq=i + 1)
        for name, element, dx in (("N", "N", -0.1), ("CA", "C", 0.0), ("C", "C", 0.1)):
            top.add_atom(name, md.element.get_by_symbol(element), residue)
            xyz.append([i * SPACING + dx, 0.0, 0.0])
    if last:
        cap = top.add_residue(last, chain, resSeq=residues + 1)
        top.add_atom("N", md.element.nitrogen, cap)
        xyz.append([residues * SPACING, 0.0, 0.0])
    frames = np.repeat(np.array([xyz], dtype=np.float32), 6, axis=0)
    return md.Trajectory(frames, top)


@pytest.mark.parametrize("first,last", [(None, "NHE"), (None, "NH2"), ("ACE", "NME"),
                                        ("ACE", None), (None, None)])
def test_the_ends_are_the_first_and_last_alpha_carbons(first, last) -> None:
    distance = EndToEndDistance().compute(_capped(first, last))
    assert np.allclose(distance, 4 * SPACING, atol=1e-5)


def test_a_chain_with_one_alpha_carbon_is_refused() -> None:
    from fastmdxplora.refusals import StudyError

    with pytest.raises(StudyError) as refused:
        EndToEndDistance().compute(_capped("ACE", "NHE", residues=1))
    assert refused.value.code == "analysis.selection.empty"
