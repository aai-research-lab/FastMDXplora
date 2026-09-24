"""A placed ligand's hydrogens turn with it, each from its own atom.

With the pose taken from the structure, the heavy atoms come from the crystal
and the hydrogens -- which a crystal structure does not have -- from the
supplied file. That file's conformer is in whatever frame it was drawn in, so
the hydrogens have to be carried by the motion that takes the file's heavy
atoms onto the structure's: rotated as well as shifted, and each from the
atom it is bonded to. A single shared shift leaves X-H bonds several
Angstroms long whenever the two frames are not already aligned, and nothing
downstream checks that minimisation put them back.

Benzamidine has hydrogens on ring carbons and on both amidine nitrogens, so
a hydrogen hung from the wrong atom, or not turned, shows in the geometry.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("rdkit")

from rdkit import Chem  # noqa: E402
from rdkit.Chem import AllChem  # noqa: E402

from fastmdxplora.setup.ligand import pose_from_structure  # noqa: E402
from tests.test_the_pose_comes_from_the_structure import (  # noqa: E402
    _Atom,
    _Quantity,
)


class _Bond:
    def __init__(self, first: int, second: int):
        self.atom1_index = first
        self.atom2_index = second


class _Benzamidine:
    """Benzamidine in the shape the OpenFF molecule has here: atoms, bonds
    and a conformer in nanometres."""

    def __init__(self, *, bonds: bool):
        rdkit = Chem.AddHs(Chem.MolFromSmiles("NC(=N)c1ccccc1"))
        assert AllChem.EmbedMolecule(rdkit, randomSeed=7) == 0
        self.rdkit = rdkit
        self.atoms = [_Atom(atom.GetAtomicNum()) for atom in rdkit.GetAtoms()]
        if bonds:
            self.bonds = [_Bond(bond.GetBeginAtomIdx(), bond.GetEndAtomIdx())
                          for bond in rdkit.GetBonds()]
        self._conformers = [
            _Quantity(rdkit.GetConformer().GetPositions() / 10.0)]

    @property
    def conformers(self):
        return self._conformers


def _turned(points: np.ndarray) -> np.ndarray:
    """A quarter turn about an oblique axis and a shift: a crystal frame
    that shares nothing with the one the file was drawn in."""
    axis = np.array([1.0, 2.0, 3.0]) / np.sqrt(14.0)
    cross = np.array([[0.0, -axis[2], axis[1]],
                      [axis[2], 0.0, -axis[0]],
                      [-axis[1], axis[0], 0.0]])
    rotation = np.eye(3) + cross + cross @ cross  # Rodrigues at 90 degrees
    return points @ rotation.T + np.array([2.0, -1.0, 3.0])


def _structure(tmp_path: Path, molecule: _Benzamidine) -> Path:
    """The heavy atoms, turned, as a deposited structure would hold them."""
    positions = molecule.conformers[0].m_as("nanometer")
    lines = []
    for serial, atom in enumerate(molecule.rdkit.GetAtoms(), start=1):
        if atom.GetAtomicNum() == 1:
            continue
        x, y, z = _turned(positions[atom.GetIdx()]) * 10.0
        name = f"{atom.GetSymbol()}{serial}"
        lines.append(
            f"HETATM{serial:5d}  {name:<3s} BEN A 999    "
            f"{x:8.3f}{y:8.3f}{z:8.3f}  1.00  0.00          "
            f"{atom.GetSymbol():>2s}")
    lines.append("END")
    path = tmp_path / "input.pdb"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _angle(a: np.ndarray, vertex: np.ndarray, b: np.ndarray) -> float:
    first, second = a - vertex, b - vertex
    cosine = first @ second / np.linalg.norm(first) / np.linalg.norm(second)
    return float(np.degrees(np.arccos(np.clip(cosine, -1.0, 1.0))))


@pytest.fixture(params=[True, False], ids=["bond graph", "nearest atom"])
def placed(request, tmp_path: Path):
    """Placed with the bond graph, and without one, where the heavy atom
    nearest each hydrogen in the file stands in for its bond."""
    molecule = _Benzamidine(bonds=request.param)
    before = molecule.conformers[0].m_as("nanometer").copy()
    structure = _structure(tmp_path, molecule)
    moved, said = pose_from_structure(molecule, structure, "BEN",
                                      required=True)
    assert "placed BEN" in said
    return molecule.rdkit, before, moved.conformers[0].m_as("nanometer")


def _bonds_to_hydrogen(rdkit):
    for atom in rdkit.GetAtoms():
        if atom.GetAtomicNum() == 1:
            (parent,) = atom.GetNeighbors()
            yield atom.GetIdx(), parent


class TestThePlacedLigandKeepsItsShape:
    def test_every_heavy_atom_lands_on_the_structure(self, placed) -> None:
        rdkit, before, after = placed
        heavy = [atom.GetIdx() for atom in rdkit.GetAtoms()
                 if atom.GetAtomicNum() > 1]
        # A PDB keeps a thousandth of an Angstrom: 1e-4 nm.
        assert np.allclose(after[heavy], _turned(before[heavy]), atol=2e-4)

    def test_every_bond_to_a_hydrogen_keeps_its_length(self, placed) -> None:
        rdkit, before, after = placed
        pairs = list(_bonds_to_hydrogen(rdkit))
        assert len(pairs) == 8 and len({p.GetIdx() for _, p in pairs}) == 7
        for hydrogen, parent in pairs:
            was = np.linalg.norm(before[hydrogen] - before[parent.GetIdx()])
            now = np.linalg.norm(after[hydrogen] - after[parent.GetIdx()])
            assert abs(now - was) < 0.002, (hydrogen, was, now)

    def test_every_angle_at_a_hydrogen_s_atom_is_kept(self, placed) -> None:
        """Length alone would pass a hydrogen spun about its atom; the
        angles to the atom's other neighbours pin its direction."""
        rdkit, before, after = placed
        checked = 0
        for hydrogen, parent in _bonds_to_hydrogen(rdkit):
            vertex = parent.GetIdx()
            for neighbour in parent.GetNeighbors():
                other = neighbour.GetIdx()
                if other == hydrogen:
                    continue
                was = _angle(before[hydrogen], before[vertex], before[other])
                now = _angle(after[hydrogen], after[vertex], after[other])
                assert abs(now - was) < 1.0, (hydrogen, other, was, now)
                checked += 1
        assert checked >= 8

    def test_the_whole_molecule_moves_as_one_body(self, placed) -> None:
        """The structure is the file's own heavy atoms turned, so the
        hydrogens should end where the same turn takes them -- which a
        mirror image, having the same lengths and angles, would not."""
        _, before, after = placed
        assert np.allclose(after, _turned(before), atol=2e-4)
