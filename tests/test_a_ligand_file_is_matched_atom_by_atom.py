"""A ligand's chemistry file is matched to the trajectory atom by atom.

The file's atoms were taken in file order and checked by count alone, so an
acetate whose SDF lists C, H, H, H, C, O, O (the order RDKit and the
Chemical Component Dictionary often write) put the carboxylate on two methyl
hydrogens of a trajectory written C, C, O, O, H, H, H, and the salt bridge
to an arginine 0.44 nm away was lost. The file's graph of elements and
bonds is now matched to the topology's, and a file that does not match is
not used.

The dictionary route called ``fetch_chemistry(resname)`` against a
signature that needs the entry, chain and residue number, so it raised
every time and the error was swallowed. It is gone, and nothing here
reaches for the network.
"""

from __future__ import annotations

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")
pytest.importorskip("rdkit.Chem")

from rdkit import Chem  # noqa: E402
from rdkit.Chem import AllChem  # noqa: E402

from fastmdxplora.analysis.interactions import (  # noqa: E402
    ligand_charged_groups,
    salt_bridges,
)
from fastmdxplora.analysis.ligand_chemistry import resolve_ligand_chemistry  # noqa: E402


def _acetate_beside_arginine(*, bonded: bool = True):
    """Acetate written heavy atoms first, as tleap and GROMACS write it, and
    an arginine's guanidinium 0.44 nm from the carboxylate's centre."""
    topology = md.Topology()
    chain = topology.add_chain()
    lig = topology.add_residue("LIG", chain)
    atoms = [("C1", "C", (-0.3, 0, 0)), ("C2", "C", (-0.15, 0, 0)),
             ("O1", "O", (0, 0.11, 0)), ("O2", "O", (0, -0.11, 0)),
             ("H1", "H", (-0.34, 0.1, 0)), ("H2", "H", (-0.34, -0.05, 0.09)),
             ("H3", "H", (-0.34, -0.05, -0.09))]
    xyz = []
    for name, element, position in atoms:
        topology.add_atom(name, md.element.get_by_symbol(element), lig)
        xyz.append(position)
    arg = topology.add_residue("ARG", topology.add_chain())
    for name, element, position in (("NE", "N", (0.40, 0.1, 0)),
                                    ("NH1", "N", (0.40, -0.1, 0)),
                                    ("NH2", "N", (0.52, 0, 0)),
                                    ("CZ", "C", (0.44, 0, 0))):
        topology.add_atom(name, md.element.get_by_symbol(element), arg)
        xyz.append(position)
    if bonded:
        listed = list(topology.atoms)
        for i, j in ((0, 1), (1, 2), (1, 3), (0, 4), (0, 5), (0, 6)):
            topology.add_bond(listed[i], listed[j])
    return md.Trajectory(np.array([xyz], dtype=float), topology), list(range(7)), [7, 8, 9]


def _sdf_with_hydrogens_interleaved(path):
    mol = Chem.AddHs(Chem.MolFromSmiles("CC(=O)[O-]"))
    AllChem.EmbedMolecule(mol, randomSeed=1)
    mol = Chem.RenumberAtoms(mol, [0, 4, 5, 6, 1, 2, 3])   # C, H, H, H, C, O, O
    writer = Chem.SDWriter(str(path))
    writer.write(mol)
    writer.close()
    return path


class TestTheFileIsMatchedByItsBonds:
    def test_hydrogens_listed_elsewhere_still_land_on_the_right_atoms(self, tmp_path):
        traj, lig, arg = _acetate_beside_arginine()
        chemistry = resolve_ligand_chemistry(
            traj, "LIG", lig, supplied=_sdf_with_hydrogens_interleaved(tmp_path / "a.sdf"),
            allow_fetch=False)
        assert chemistry.source == "supplied"
        _positive, negative = ligand_charged_groups(chemistry, lig)
        assert [[traj.topology.atom(i).name for i in group] for group in negative] \
            == [["O1", "O2"]]
        found = salt_bridges(traj, chemistry, lig, arg, periodic=False)
        assert len(found) == 1
        assert found[0].distance_nm == pytest.approx(0.44, abs=0.01)

    def test_without_bonds_the_elements_must_agree_in_order(self, tmp_path):
        """Nothing to match a graph against, so a file in another order is
        not used rather than mapped by position."""
        traj, lig, _arg = _acetate_beside_arginine(bonded=False)
        chemistry = resolve_ligand_chemistry(
            traj, "LIG", lig, supplied=_sdf_with_hydrogens_interleaved(tmp_path / "a.sdf"),
            net_charge=-1, allow_fetch=False)
        assert chemistry.source == "perceived"


class TestNothingIsFetched:
    def test_the_dictionary_is_not_called(self, tmp_path, monkeypatch):
        """The call could never succeed: it needs an entry, a chain and a
        residue number a trajectory does not have."""
        import fastmdxplora.setup.ccd as ccd

        calls = []
        monkeypatch.setattr(ccd, "fetch_chemistry",
                            lambda *args, **kwargs: calls.append(args))
        traj, lig, _arg = _acetate_beside_arginine()
        chemistry = resolve_ligand_chemistry(traj, "LIG", lig, net_charge=-1)
        assert chemistry.source == "perceived"
        assert calls == []
