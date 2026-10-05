"""An acceptor is an atom with a lone pair free to take a hydrogen bond.

Every nitrogen, oxygen and sulphur was taken as an acceptor, so a ligand
NH3+ pointing at a lysine NZ (three hydrogens, no lone pair) was reported as
a hydrogen bond at 0.30 nm and 180 degrees between two cations, a ligand
O-H pointing at a backbone amide N was another, and a C-Cl aimed at a
lysine NZ was a halogen bond. The amide, ammonium and conjugated nitrogens
are left out now; oxygens, amine and pyridine-type nitrogens, Met SD and a
cysteine SG without its hydrogen are kept.
"""

from __future__ import annotations

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")
pytest.importorskip("rdkit.Chem")

from rdkit import Chem  # noqa: E402

from fastmdxplora.analysis.interactions import (  # noqa: E402
    donors_and_acceptors,
    halogen_bonds,
    hydrogen_bonds,
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
            self.xyz.append(list(np.asarray(xyz, dtype=float)))
        return list(range(first, len(self.xyz)))

    def bond(self, i, j):
        atoms = list(self.topology.atoms)
        self.topology.add_bond(atoms[i], atoms[j])

    def trajectory(self):
        return md.Trajectory(np.array([self.xyz], dtype=float), self.topology)


def _ammonium_at_lysine():
    """A ligand CH3-NH3+ whose H1 points straight at a lysine NZ(+) 0.30 nm
    from its N."""
    b = _Builder()
    lig = b.residue("LIG", [
        ("C1", "C", (-0.147, 0, 0)), ("N1", "N", (0, 0, 0)),
        ("H1", "H", (0.034, 0.096, 0)), ("H2", "H", (0.034, -0.048, 0.083)),
        ("H3", "H", (0.034, -0.048, -0.083))])
    for k in (0, 2, 3, 4):
        b.bond(lig[1], lig[k])
    d = np.array(b.xyz[lig[2]]) / np.linalg.norm(b.xyz[lig[2]])
    nz = 0.30 * d
    lys = b.residue("LYS", [
        ("CE", "C", nz + 0.147 * d), ("NZ", "N", nz),
        ("HZ1", "H", nz + (0, 0, 0.1)), ("HZ2", "H", nz + (0, 0, -0.1)),
        ("HZ3", "H", nz + (0.1, 0, 0))], chain=True)
    for k in (0, 2, 3, 4):
        b.bond(lys[1], lys[k])
    return b.trajectory(), lig, lys


class TestNoLonePairNoAcceptor:
    def test_two_cations_do_not_hydrogen_bond(self) -> None:
        traj, lig, lys = _ammonium_at_lysine()
        assert hydrogen_bonds(traj, lig, lys, periodic=False) == []

    def test_a_backbone_amide_n_does_not_accept(self) -> None:
        b = _Builder()
        lig = b.residue("LIG", [("C1", "C", (-0.14, 0, 0)), ("O1", "O", (0, 0, 0)),
                                ("H1", "H", (0.097, 0, 0))])
        b.bond(lig[0], lig[1]); b.bond(lig[1], lig[2])
        pro = b.residue("ALA", [("N", "N", (0.30, 0, 0)), ("H", "H", (0.35, 0.087, 0)),
                                ("CA", "C", (0.37, -0.13, 0)), ("C", "C", (0.25, 0, 0.13))],
                        chain=True)
        b.bond(pro[0], pro[1]); b.bond(pro[0], pro[2])
        assert hydrogen_bonds(b.trajectory(), lig, pro, periodic=False) == []

    def test_a_halogen_does_not_bond_to_a_lysine_nz(self) -> None:
        b = _Builder()
        lig = b.residue("LIG", [("C1", "C", (-0.18, 0, 0)), ("CL1", "Cl", (0, 0, 0))])
        b.bond(0, 1)
        lys = b.residue("LYS", [
            ("NZ", "N", (0.32, 0, 0)), ("HZ1", "H", (0.36, 0.09, 0)),
            ("HZ2", "H", (0.36, -0.045, 0.08)), ("HZ3", "H", (0.36, -0.045, -0.08)),
            ("CE", "C", (0.47, 0, 0))], chain=True)
        for k in (1, 2, 3, 4):
            b.bond(lys[0], lys[k])
        chemistry = ResolvedChemistry(mol=Chem.MolFromSmiles("CCl"), source="supplied",
                                      detail="", resname="LIG", n_atoms=2)
        assert halogen_bonds(b.trajectory(), chemistry, lig, lys, periodic=False) == []


class TestWhatStillAccepts:
    def test_the_acceptors_a_protein_has(self) -> None:
        b = _Builder()
        # Asn: amide O accepts, amide N does not.
        asn = b.residue("ASN", [("CG", "C", (0, 0, 0)), ("OD1", "O", (0.1, 0, 0)),
                                ("ND2", "N", (0, 0.1, 0)), ("HD21", "H", (0, 0.2, 0)),
                                ("HD22", "H", (-0.1, 0.1, 0))])
        b.bond(asn[0], asn[1]); b.bond(asn[0], asn[2]); b.bond(asn[2], asn[3]); b.bond(asn[2], asn[4])
        # Met SD: two carbons, accepts weakly.
        met = b.residue("MET", [("CG", "C", (1, 0, 0)), ("SD", "S", (1.1, 0, 0)),
                                ("CE", "C", (1.2, 0, 0))])
        b.bond(met[0], met[1]); b.bond(met[1], met[2])
        # Cys with its thiol hydrogen: donates, does not accept.
        cys = b.residue("CYS", [("CB", "C", (2, 0, 0)), ("SG", "S", (2.1, 0, 0)),
                                ("HG", "H", (2.2, 0, 0))])
        b.bond(cys[0], cys[1]); b.bond(cys[1], cys[2])
        # A neutral histidine (HIE): NE2-H does not accept, ND1 does.
        his = b.residue("HIS", [("CG", "C", (3, 0, 0)), ("ND1", "N", (3.1, 0, 0)),
                                ("CE1", "C", (3.2, 0, 0)), ("NE2", "N", (3.2, 0.1, 0)),
                                ("CD2", "C", (3.1, 0.1, 0)), ("HE2", "H", (3.3, 0.15, 0))])
        for i, j in ((0, 1), (1, 2), (2, 3), (3, 4), (4, 0), (3, 5)):
            b.bond(his[i], his[j])
        _donors, acceptors = donors_and_acceptors(b.topology, range(b.topology.n_atoms))
        names = {f"{b.topology.atom(i).residue.name}:{b.topology.atom(i).name}" for i in acceptors}
        assert names == {"ASN:OD1", "MET:SD", "HIS:ND1"}

    def test_a_ligand_amine_and_pyridine_accept_and_an_amide_n_does_not(self) -> None:
        b = _Builder()
        # CH3-NH2: an amine, its N bonded to an sp3 carbon.
        amine = b.residue("LIG", [("C1", "C", (0, 0, 0)), ("N1", "N", (0.15, 0, 0)),
                                  ("H1", "H", (0.2, 0.1, 0)), ("H2", "H", (0.2, -0.1, 0)),
                                  ("H3", "H", (-0.05, 0.1, 0)), ("H4", "H", (-0.05, -0.1, 0)),
                                  ("H5", "H", (-0.05, 0, 0.1))])
        for k in (2, 3):
            b.bond(amine[1], amine[k])
        for k in (1, 4, 5, 6):
            b.bond(amine[0], amine[k])
        # H-C(=O)-NH2 written with a 3-bonded carbon: the amide N.
        amide = b.residue("LIG", [("C2", "C", (1, 0, 0)), ("O2", "O", (1.1, 0.1, 0)),
                                  ("N2", "N", (1.1, -0.1, 0)), ("H6", "H", (1.2, -0.1, 0)),
                                  ("H7", "H", (1.1, -0.2, 0)), ("H8", "H", (0.9, 0, 0))])
        for i, j in ((0, 1), (0, 2), (2, 3), (2, 4), (0, 5)):
            b.bond(amide[i], amide[j])
        # A pyridine-type N: two neighbours.
        ring = b.residue("LIG", [("N3", "N", (2, 0, 0)), ("C3", "C", (2.1, 0.1, 0)),
                                 ("C4", "C", (2.1, -0.1, 0))])
        b.bond(ring[0], ring[1]); b.bond(ring[0], ring[2])
        _donors, acceptors = donors_and_acceptors(b.topology, range(b.topology.n_atoms))
        assert {b.topology.atom(i).name for i in acceptors} == {"N1", "O2", "N3"}
