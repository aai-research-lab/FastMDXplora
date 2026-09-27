"""A pose taken from the structure goes atom for atom, by element and bond.

With the pose taken from the structure, each of the supplied file's heavy
atoms took the crystal position that came at the same place in the
structure's list. Only the count was checked. Two files listing the atoms in
different orders then put atoms on each other's positions: a C-C bond of 1.5
Angstroms came out at 3.0 and 4.5, with no error, since the clash check
measures the ligand against the protein and never against itself. Now each
atom goes to the crystal atom of the same element bonded to the same
neighbours, and a structure whose atoms cannot be matched that way is not
taken as the pose.

The crystal lists here are the file's own heavy atoms, turned, shifted and
reordered, so the right answer for every atom is known independently.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("rdkit")

from rdkit import Chem  # noqa: E402
from rdkit.Chem import AllChem  # noqa: E402

from fastmdxplora.setup.ligand import (  # noqa: E402
    LigandError,
    _heavy_atom_match,
    pose_from_structure,
)
from tests.test_hydrogens_follow_the_atom_they_are_bonded_to import (  # noqa: E402
    _Bond,
    _turned,
)
from tests.test_the_pose_comes_from_the_structure import _Atom, _Quantity  # noqa: E402

PARACETAMOL = "CC(=O)Nc1ccc(O)cc1"


class _Supplied:
    """A molecule in the shape the OpenFF one has here: atoms, bonds and a
    conformer in nanometres."""

    def __init__(self, smiles: str, seed: int = 11):
        rdkit = Chem.AddHs(Chem.MolFromSmiles(smiles))
        assert AllChem.EmbedMolecule(rdkit, randomSeed=seed) == 0
        AllChem.MMFFOptimizeMolecule(rdkit)
        self.rdkit = rdkit
        self.atoms = [_Atom(atom.GetAtomicNum()) for atom in rdkit.GetAtoms()]
        self.bonds = [_Bond(bond.GetBeginAtomIdx(), bond.GetEndAtomIdx())
                      for bond in rdkit.GetBonds()]
        self._conformers = [_Quantity(rdkit.GetConformer().GetPositions() / 10.0)]

    @property
    def conformers(self):
        return self._conformers

    @property
    def heavy(self) -> list[int]:
        return [a.GetIdx() for a in self.rdkit.GetAtoms() if a.GetAtomicNum() > 1]


def _deposit(tmp_path: Path, molecule: _Supplied, order: list[int],
             *, resname: str = "LIG", moved: dict[int, np.ndarray] | None = None,
             positions_of: _Supplied | None = None) -> Path:
    """The heavy atoms of ``positions_of`` (default: the molecule itself),
    turned, written in ``order``, as a deposited structure holds them."""
    source = positions_of or molecule
    positions = _turned(source.conformers[0].m_as("nanometer")) * 10.0
    for index, shift in (moved or {}).items():
        positions[index] = positions[index] + shift
    lines = []
    for serial, index in enumerate(order, start=1):
        symbol = source.rdkit.GetAtomWithIdx(index).GetSymbol()
        x, y, z = positions[index]
        lines.append(
            f"HETATM{serial:5d}  {symbol}{serial:<2d} {resname:>3s} A 999    "
            f"{x:8.3f}{y:8.3f}{z:8.3f}  1.00  0.00          {symbol:>2s}")
    lines.append("END")
    path = tmp_path / "input.pdb"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _bond_lengths(molecule: _Supplied, xyz: np.ndarray) -> np.ndarray:
    return np.array([np.linalg.norm(xyz[b.GetBeginAtomIdx()] - xyz[b.GetEndAtomIdx()])
                     for b in molecule.rdkit.GetBonds()])


@pytest.fixture
def shuffled(tmp_path: Path):
    molecule = _Supplied(PARACETAMOL)
    before = molecule.conformers[0].m_as("nanometer").copy()
    order = [int(i) for i in np.random.default_rng(3).permutation(molecule.heavy)]
    assert order != molecule.heavy
    structure = _deposit(tmp_path, molecule, order)
    placed, said = pose_from_structure(molecule, structure, "LIG", required=True)
    return molecule, before, placed.conformers[0].m_as("nanometer"), said


class TestTwoOrdersOfOneMolecule:

    def test_each_atom_lands_on_its_own_crystal_position(self, shuffled) -> None:
        molecule, before, after, _ = shuffled
        heavy = molecule.heavy
        assert np.allclose(after[heavy], _turned(before)[heavy], atol=2e-4)

    def test_every_bond_keeps_its_length(self, shuffled) -> None:
        molecule, before, after, _ = shuffled
        assert np.allclose(_bond_lengths(molecule, after),
                           _bond_lengths(molecule, before), atol=2e-3)

    def test_the_hydrogens_come_with_it(self, shuffled) -> None:
        _, before, after, _ = shuffled
        assert np.allclose(after, _turned(before), atol=2e-4)

    def test_it_says_the_orders_differed(self, shuffled) -> None:
        assert "different orders" in shuffled[3]

    def test_the_old_reading_would_have_torn_it(self, shuffled) -> None:
        """The same crystal list taken in file order, as it was: bonds of
        several Angstroms, which is the defect this test would catch."""
        molecule, before, _, _ = shuffled
        order = [int(i) for i in np.random.default_rng(3).permutation(molecule.heavy)]
        crystal = _turned(before)[order]
        by_position = before.copy()
        by_position[molecule.heavy] = crystal
        heavy_bonds = [b for b in molecule.rdkit.GetBonds()
                       if b.GetBeginAtom().GetAtomicNum() > 1
                       and b.GetEndAtom().GetAtomicNum() > 1]
        longest = max(np.linalg.norm(by_position[b.GetBeginAtomIdx()]
                                     - by_position[b.GetEndAtomIdx()])
                      for b in heavy_bonds)
        assert longest > 0.25


def test_one_order_is_taken_as_it_is(tmp_path: Path) -> None:
    molecule = _Supplied(PARACETAMOL)
    before = molecule.conformers[0].m_as("nanometer").copy()
    structure = _deposit(tmp_path, molecule, molecule.heavy)
    placed, said = pose_from_structure(molecule, structure, "LIG", required=True)
    assert np.allclose(placed.conformers[0].m_as("nanometer"), _turned(before), atol=2e-4)
    assert "different orders" not in said


def test_a_symmetric_molecule_is_matched(tmp_path: Path) -> None:
    """Benzamidine's ring has a mirror, so two answers are right; either
    places the same molecule with every bond intact."""
    molecule = _Supplied("NC(=N)c1ccccc1")
    before = molecule.conformers[0].m_as("nanometer").copy()
    order = list(reversed(molecule.heavy))
    placed, _ = pose_from_structure(molecule, _deposit(tmp_path, molecule, order),
                                    "LIG", required=True)
    after = placed.conformers[0].m_as("nanometer")
    assert np.allclose(_bond_lengths(molecule, after),
                       _bond_lengths(molecule, before), atol=2e-3)


class TestNotTheSameMolecule:

    def test_an_isomer_with_the_same_count_is_not_taken(self, tmp_path: Path) -> None:
        """3-acetamidophenol has paracetamol's atoms and not its bonds."""
        molecule = _Supplied(PARACETAMOL)
        isomer = _Supplied("CC(=O)Nc1cccc(O)c1")
        structure = _deposit(tmp_path, molecule, isomer.heavy, positions_of=isomer)
        before = molecule.conformers[0].m_as("nanometer").copy()
        placed, said = pose_from_structure(molecule, structure, "LIG")
        assert "do not match" in said
        assert np.allclose(placed.conformers[0].m_as("nanometer"), before)
        with pytest.raises(LigandError) as refused:
            pose_from_structure(molecule, structure, "LIG", required=True)
        assert refused.value.code == "setup.ligand.pose_unavailable"

    def test_a_broken_deposited_geometry_is_not_taken(self, tmp_path: Path) -> None:
        molecule = _Supplied(PARACETAMOL)
        ring_carbon = molecule.heavy[5]
        structure = _deposit(tmp_path, molecule, molecule.heavy,
                             moved={ring_carbon: np.array([3.0, 0.0, 0.0])})
        with pytest.raises(LigandError, match="do not match"):
            pose_from_structure(molecule, structure, "LIG", required=True)


class TestTheMatch:

    def test_a_long_chain_in_reverse_is_found_quickly(self) -> None:
        """Forty carbons in a row, listed backwards: the search follows the
        bonds rather than trying every ordering."""
        n = 40
        chain = [{j for j in (i - 1, i + 1) if 0 <= j < n} for i in range(n)]
        reverse = list(reversed(range(n)))
        backwards = [{reverse.index(j) for j in chain[reverse[i]]} for i in range(n)]
        found = _heavy_atom_match([6] * n, chain, [6] * n, backwards)
        assert found is not None
        assert all({found[j] for j in chain[i]} == backwards[found[i]] for i in range(n))

    def test_different_elements_never_match(self) -> None:
        graph = [{1}, {0}]
        assert _heavy_atom_match([6, 8], graph, [6, 7], graph) is None
