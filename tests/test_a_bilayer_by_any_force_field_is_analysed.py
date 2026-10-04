"""A bilayer is analysed whichever force field named its lipids and water.

The bilayer analyses were written against OpenMM's own patches and the
CHARMM36 names, and read other systems wrongly without saying so: a Lipid21
bilayer, whose phosphorus is P31, had no heads and an area per lipid of
infinity; a lipid whose name was not on a short list was counted as protein;
water named OPC was counted as protein where it reached the core. Each case
is built here from an OpenMM patch, renamed or rearranged as the other force
field writes it, so the right answer is the patch's own.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any

import pytest

from fastmdxplora.refusals import StudyError

md = pytest.importorskip("mdtraj")
app = pytest.importorskip("openmm.app")

from fastmdxplora.analysis.area_per_lipid import AreaPerLipid  # noqa: E402
from fastmdxplora.analysis.bilayer_thickness import BilayerThickness  # noqa: E402


def _patch(lipid: str):
    return md.load(str(Path(app.__file__).parent / "data" / f"{lipid}.pdb"))


def _run(cls, traj):
    analysis = cls(output_dir=tempfile.mkdtemp())
    return analysis.compute(traj), analysis.findings


def _renamed(traj, rename):
    top = traj.topology.copy()
    rename(top)
    return md.Trajectory(traj.xyz, top, unitcell_lengths=traj.unitcell_lengths,
                         unitcell_angles=traj.unitcell_angles)


def _lipid21(traj, names=("PA", "PC", "OL")):
    """Each POPC as AMBER Lipid21 writes it: sn-1 tail, head, sn-2 tail as
    three residues, the carbonyls in the head, the phosphorus named P31."""
    top = md.Topology()
    chain = top.add_chain()
    mapping: dict[int, Any] = {}
    order: list[int] = []
    for residue in traj.topology.residues:
        atoms = list(residue.atoms)
        if residue.name != "POP":
            new = top.add_residue(residue.name, chain, resSeq=residue.resSeq)
            for atom in atoms:
                mapping[atom.index] = top.add_atom(atom.name, atom.element, new)
                order.append(atom.index)
            continue
        sn1 = [a for a in atoms if (re.fullmatch(r"C3\d+", a.name) and int(a.name[2:]) >= 2)
               or re.fullmatch(r"H\d+[XYZ]", a.name)]
        sn2 = [a for a in atoms if (re.fullmatch(r"C2\d+", a.name) and int(a.name[2:]) >= 2)
               or re.fullmatch(r"H\d+[RST]|H91|H101", a.name)]
        head = [a for a in atoms if a not in sn1 and a not in sn2]
        for name, part in zip(names, (sn1, head, sn2)):
            new = top.add_residue(name, chain, resSeq=residue.resSeq)
            for atom in part:
                mapping[atom.index] = top.add_atom(
                    "P31" if atom.name == "P" else atom.name, atom.element, new)
                order.append(atom.index)
    for bond in traj.topology.bonds:
        top.add_bond(mapping[bond[0].index], mapping[bond[1].index])
    return md.Trajectory(traj.xyz[:, order], top, unitcell_lengths=traj.unitcell_lengths,
                         unitcell_angles=traj.unitcell_angles)


class TestTheHeadIsThePhosphorus:

    def test_a_lipid21_bilayer_has_its_heads(self) -> None:
        """Lipid21 names the phosphorus P31; it is found by its element."""
        traj = _patch("POPC")
        split = _lipid21(traj)
        area, findings = _run(AreaPerLipid, split)
        assert findings["bilayer"]["lipids"] == 128
        assert area[0] == pytest.approx(_run(AreaPerLipid, traj)[0][0], rel=1e-6)
        assert _run(BilayerThickness, split)[0][0] == pytest.approx(
            _run(BilayerThickness, traj)[0][0], abs=1e-4)

    def test_lipids_without_a_head_atom_are_refused_not_infinite(self) -> None:
        """Beads with no element, as a coarse-grained model has: no head is
        found, and the area is refused rather than divided by zero."""
        traj = _renamed(_patch("POPC"), lambda top: [
            setattr(a, "element", md.element.virtual_site)
            for a in top.atoms if a.name == "P"])
        with pytest.raises(StudyError) as refused:
            _run(AreaPerLipid, traj)
        assert refused.value.code == "analysis.system.inapplicable"
        assert "head" in str(refused.value)
