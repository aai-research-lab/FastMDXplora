"""Three hydrogen-bond counts, three criteria, each named.

`hbonds` uses Baker-Hubbard (H...A < 2.5 A), `pl_hbonds` Wernet-Nilsson and
`pl_interactions` a donor to acceptor distance of 3.5 A. They count
different bonds: an O-H...O 3.3 A apart at 180 degrees is a bond in
`pl_interactions` and not in `pl_hbonds`. Each records which it used and
names it on its axis, and the interactions rule no longer attributes its
3.5 A to Baker and Hubbard.
"""

from __future__ import annotations

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")

from fastmdxplora.analysis.hbonds import HBonds  # noqa: E402
from fastmdxplora.analysis.interactions import hydrogen_bonds  # noqa: E402
from fastmdxplora.analysis.pl_hbonds import ProteinLigandHBonds  # noqa: E402
from fastmdxplora.analysis.pl_interactions import ProteinLigandInteractions  # noqa: E402


def test_each_records_its_criterion() -> None:
    assert "Baker-Hubbard" in HBonds().options["criterion"]
    assert "2.5 A" in HBonds().options["criterion"]
    assert "Wernet-Nilsson" in HBonds(method="wernet_nilsson").options["criterion"]
    assert "Wernet-Nilsson" in ProteinLigandHBonds(ligand_resname="LIG").options["criterion"]
    assert "3.5 A" in ProteinLigandInteractions(
        ligand_resname="LIG").options["hydrogen_bond_criterion"]


def test_each_names_it_on_its_axis() -> None:
    assert "Baker-Hubbard" in HBonds().default_ylabel()
    assert "Wernet-Nilsson" in HBonds(method="wernet_nilsson").default_ylabel()
    assert "Wernet-Nilsson" in ProteinLigandHBonds(ligand_resname="LIG").default_ylabel()


def test_the_interactions_rule_does_not_credit_its_distance_to_baker_hubbard() -> None:
    doc = hydrogen_bonds.__doc__
    first = doc.split("\n\n")[1]
    assert "3.5 A" in first and "McDonald" in first
    assert "not Baker and Hubbard" in doc


def test_the_two_criteria_differ_where_the_records_say_they_do() -> None:
    """O-H...O at 3.3 A, straight: the 3.5 A rule finds it and
    Wernet-Nilsson does not."""
    topology = md.Topology()
    lig = topology.add_residue("LIG", topology.add_chain())
    for name, element in (("C1", "C"), ("O1", "O"), ("H1", "H")):
        topology.add_atom(name, md.element.get_by_symbol(element), lig)
    asn = topology.add_residue("ASN", topology.add_chain())
    for name, element in (("OD1", "O"), ("CG", "C")):
        topology.add_atom(name, md.element.get_by_symbol(element), asn)
    atoms = list(topology.atoms)
    topology.add_bond(atoms[0], atoms[1])
    topology.add_bond(atoms[1], atoms[2])
    topology.add_bond(atoms[3], atoms[4])
    xyz = np.array([[(-0.14, 0, 0), (0, 0, 0), (0.097, 0, 0),
                     (0.33, 0, 0), (0.45, 0.05, 0)]], dtype=float)
    traj = md.Trajectory(xyz, topology)
    traj.unitcell_lengths = np.array([[4.0, 4.0, 4.0]])
    traj.unitcell_angles = np.array([[90.0, 90.0, 90.0]])
    assert len(hydrogen_bonds(traj, [0, 1, 2], [3, 4])) == 1
    counted = ProteinLigandHBonds(ligand_resname="LIG",
                                  protein_selection="resname ASN").compute(traj)
    assert counted["n_hbonds"].tolist() == [0]
