"""Which groups are charged, from the chemistry rather than from a name.

OpenMM and PDBFixer write every protonation variant under its parent's
residue name: a histidine carrying HD1 and HE2 is HIS, an aspartate
carrying HD2 is ASP, a lysine with two hydrogens on NZ is LYS. Charges
keyed on the name therefore missed every doubly protonated histidine and
counted every neutral lysine and protonated aspartate as charged. The
hydrogens present are the protonation the setup phase chose, so they
decide.
"""

from __future__ import annotations

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")
pytest.importorskip("rdkit.Chem")

from rdkit import Chem  # noqa: E402

from fastmdxplora.analysis.interactions import (  # noqa: E402
    protein_charged_groups,
    salt_bridges,
)
from fastmdxplora.analysis.ligand_chemistry import ResolvedChemistry  # noqa: E402


class _Builder:
    def __init__(self) -> None:
        self.topology = md.Topology()
        self._chain = self.topology.add_chain()
        self.xyz: list[list[float]] = []

    def residue(self, name, atoms, *, chain=False):
        if chain:
            self._chain = self.topology.add_chain()
        residue = self.topology.add_residue(name, self._chain)
        first = len(self.xyz)
        for atom_name, element, xyz in atoms:
            self.topology.add_atom(atom_name, md.element.get_by_symbol(element), residue)
            self.xyz.append(list(xyz))
        return list(range(first, len(self.xyz)))

    def bond(self, i, j):
        atoms = list(self.topology.atoms)
        self.topology.add_bond(atoms[i], atoms[j])

    def trajectory(self):
        return md.Trajectory(np.array([self.xyz], dtype=float), self.topology)


def _chemistry(smiles: str) -> ResolvedChemistry:
    mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
    return ResolvedChemistry(mol=mol, source="supplied", detail="test",
                             resname="LIG", n_atoms=mol.GetNumAtoms())


def _as_openmm_names_them():
    """Acetate beside three residues named as OpenMM names their variants:
    HIS with HD1 and HE2 (HIP), ASP with HD2 (ASH), LYS with HZ1 and HZ2
    only (LYN)."""
    b = _Builder()
    lig = b.residue("LIG", [
        ("C1", "C", (-0.3, 0, 0)), ("C2", "C", (-0.15, 0, 0)),
        ("O1", "O", (0, 0.05, 0)), ("O2", "O", (0, -0.05, 0)),
        ("H1", "H", (-0.4, 0.1, 0)), ("H2", "H", (-0.4, -0.1, 0)),
        ("H3", "H", (-0.4, 0, 0.1))])
    his = b.residue("HIS", [
        ("ND1", "N", (0.35, 0.06, 0)), ("NE2", "N", (0.35, -0.06, 0)),
        ("HD1", "H", (0.45, 0.1, 0)), ("HE2", "H", (0.45, -0.1, 0))], chain=True)
    asp = b.residue("ASP", [
        ("OD1", "O", (2, 0, 0)), ("OD2", "O", (2, 0.1, 0)),
        ("HD2", "H", (2.08, 0.15, 0))], chain=True)
    lys = b.residue("LYS", [
        ("NZ", "N", (-0.15, 0.0, 0.4)), ("HZ1", "H", (-0.15, 0.1, 0.45)),
        ("HZ2", "H", (-0.15, -0.1, 0.45))], chain=True)
    for i, j in ((his[0], his[2]), (his[1], his[3]), (asp[1], asp[2]),
                 (lys[0], lys[1]), (lys[0], lys[2])):
        b.bond(i, j)
    return b.trajectory(), lig, his, asp, lys


class TestTheProteinSide:
    def test_variants_named_by_their_parent_are_charged_by_their_hydrogens(self):
        traj, _lig, his, asp, lys = _as_openmm_names_them()
        positive, negative = protein_charged_groups(traj.topology, his + asp + lys)
        assert positive == [[his[0], his[1]]]   # HIS + HD1 + HE2 is a cation
        assert negative == []                   # ASP + HD2 is neutral
        # and LYS with two hydrogens on NZ is neutral: not in `positive`

    def test_the_salt_bridge_is_to_the_histidine_not_the_lysine(self):
        traj, lig, his, asp, lys = _as_openmm_names_them()
        found = salt_bridges(traj, _chemistry("CC(=O)[O-]"), lig,
                             his + asp + lys, periodic=False)
        partners = {traj.topology.atom(c.protein_atom).residue.name for c in found}
        assert partners == {"HIS"}

    @pytest.mark.parametrize("name,hydrogens,charged", [
        ("LYS", ("HZ1", "HZ2", "HZ3"), True),
        ("ARG", ("HE", "HH11", "HH12", "HH21", "HH22"), True),
        ("ARG", ("HE", "HH11", "HH21", "HH22"), False),
        ("GLU", ("HB2",), True),
        ("GLU", ("HE2",), False),
        ("HIS", ("HE2",), False),
    ])
    def test_each_family_reads_its_own_hydrogens(self, name, hydrogens, charged):
        heavy = {"LYS": ("NZ",), "ARG": ("NE", "NH1", "NH2"),
                 "GLU": ("OE1", "OE2"), "HIS": ("ND1", "NE2")}[name]
        b = _Builder()
        idx = b.residue(name, [(n, n[0], (0.1 * k, 0, 0)) for k, n in enumerate(heavy)]
                        + [(h, "H", (0.1 * k, 0.1, 0)) for k, h in enumerate(hydrogens)])
        positive, negative = protein_charged_groups(b.topology, idx)
        assert bool(positive or negative) is charged

    def test_a_residue_without_hydrogens_falls_back_to_its_name(self):
        b = _Builder()
        lys = b.residue("LYS", [("NZ", "N", (0, 0, 0))])
        lyn = b.residue("LYN", [("NZ", "N", (1, 0, 0))])
        ash = b.residue("ASH", [("OD1", "O", (2, 0, 0)), ("OD2", "O", (2.1, 0, 0))])
        positive, negative = protein_charged_groups(b.topology, lys + lyn + ash)
        assert positive == [lys] and negative == []
