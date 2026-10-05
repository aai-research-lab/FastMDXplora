"""`pl_contacts` counts residues whose heavy atoms are near the ligand's.

The threshold, 0.4 nm, is a heavy-atom one, and the documentation says
heavy atoms; the count used every atom, so a leucine whose nearest carbon
was 0.48 nm from the ligand was in contact through two hydrogens 0.22 nm
apart. And it counts the residues force fields write under names MDTraj's
`protein` did not know (HIE, HID, HSP, CYX, ASH), which are now part of
`protein`.
"""

from __future__ import annotations

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")

import fastmdxplora.analysis  # noqa: E402,F401  (makes `protein` whole)
from fastmdxplora.analysis.contacts import Contacts  # noqa: E402


def _topology_with(residues):
    topology = md.Topology()
    chain = topology.add_chain()
    xyz = []
    for name, atoms, resseq in residues:
        if name == "LIG":
            chain = topology.add_chain()
        residue = topology.add_residue(name, chain, resSeq=resseq)
        for atom_name, element, position in atoms:
            topology.add_atom(atom_name, md.element.get_by_symbol(element), residue)
            xyz.append(position)
    return topology, xyz


def test_hydrogens_do_not_make_a_contact(tmp_path) -> None:
    topology, xyz = _topology_with([
        ("LEU", [("CD1", "C", (0.48, 0, 0)), ("HD11", "H", (0.371, 0, 0)),
                 ("N", "N", (0.48, 0.5, 0)), ("CA", "C", (0.6, 0.4, 0))], 10),
        ("LIG", [("C1", "C", (0, 0, 0)), ("H1", "H", (0.109, 0, 0))], 1),
    ])
    atoms = list(topology.atoms)
    topology.add_bond(atoms[0], atoms[1])
    topology.add_bond(atoms[4], atoms[5])
    traj = md.Trajectory(np.array([xyz], dtype=float), topology)
    traj.unitcell_lengths = np.array([[5.0, 5.0, 5.0]])
    traj.unitcell_angles = np.array([[90.0, 90.0, 90.0]])
    analysis = Contacts(ligand_resname="LIG", output_dir=str(tmp_path))
    assert analysis.compute(traj)["n_contacts"].tolist() == [0]


def test_every_amber_residue_name_is_counted(tmp_path) -> None:
    """Seven residues around a ligand, five named as AMBER names them: seven
    contacts (B-D2)."""
    names = ["ALA", "HIE", "HID", "HSP", "CYX", "ASH", "CYS"]
    residues = [(name, [("CA", "C", (0.3 * np.cos(k), 0.3 * np.sin(k),
                                      0.0 if k % 2 else 0.05))], k + 1)
                for k, name in enumerate(names)]
    residues.append(("LIG", [("C1", "C", (0, 0, 0)), ("O1", "O", (0.1, 0, 0))], 1))
    topology, xyz = _topology_with(residues)
    traj = md.Trajectory(np.array([xyz], dtype=float), topology)
    traj.unitcell_lengths = np.array([[5.0, 5.0, 5.0]])
    traj.unitcell_angles = np.array([[90.0, 90.0, 90.0]])
    analysis = Contacts(ligand_resname="LIG", output_dir=str(tmp_path))
    assert analysis.compute(traj)["n_contacts"].tolist() == [7]
