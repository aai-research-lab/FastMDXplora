"""A ligand placed on the structure's pose is the file's molecule, soundly.

The pose was matched to the file by element and bond graph alone. That graph
is the same for an enantiomer, a diastereomer and an E/Z pair, so each was
placed as the file's molecule and simulated as the structure's. Of several
symmetric matches only the first 256 were ranked, and the hydrogens were
laid on with one rotation for the whole molecule, so a reordered ligand with
two CF3 groups and a tBu, or a flexible one whose file conformer is not the
crystal's, had hydrogens 0.3 A from other atoms. A structure whose residue
carried its hydrogens was refused as another molecule. Now: the file's
stereochemistry is held to the pose's (`setup.ligand.stereo_mismatch`),
every match is ranked, each hydrogen is placed from its own atom's
neighbourhood and none may come within 0.9 A of another atom
(`setup.ligand.hydrogens_overlap`), a residue's hydrogens are left out of
the match, a mismatch names its atoms (`setup.ligand.atoms_unmatched`), and
what was placed is recorded.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("rdkit")
pytest.importorskip("mdtraj")

from rdkit import Chem  # noqa: E402

from fastmdxplora.setup.ligand import (  # noqa: E402
    HYDROGEN_CLEARANCE_NM,
    LigandError,
    _hydrogen_parents,
    pose_from_structure,
)
from tests.test_a_pose_is_matched_atom_for_atom import (  # noqa: E402
    PARACETAMOL,
    _deposit,
    _Supplied,
)
from tests.test_hydrogens_follow_the_atom_they_are_bonded_to import (  # noqa: E402
    _Bond,
    _turned,
)
from tests.test_the_pose_comes_from_the_structure import _Atom, _Quantity  # noqa: E402

IMATINIB = "Cc1ccc(NC(=O)c2ccc(CN3CCN(C)CC3)cc2)cc1Nc1nccc(-c2cccnc2)n1"
#: Two CF3 groups and a tBu on a ring: 864 heavy-atom matches.
CF3_TBU = "CC(C)(C)c1cc(C(F)(F)F)cc(C(F)(F)F)c1"


class _Renumbered:
    """A molecule's file with its atoms listed in another order, at the same
    coordinates: atom k is the original's atom ``order[k]``."""

    def __init__(self, original: _Supplied, order: list[int]):
        self.rdkit = Chem.RenumberAtoms(original.rdkit, order)
        self.atoms = [_Atom(a.GetAtomicNum()) for a in self.rdkit.GetAtoms()]
        self.bonds = [_Bond(b.GetBeginAtomIdx(), b.GetEndAtomIdx())
                      for b in self.rdkit.GetBonds()]
        self._conformers = [_Quantity(self.rdkit.GetConformer().GetPositions() / 10.0)]

    @property
    def conformers(self):
        return self._conformers


def _placed(tmp_path: Path, file: _Supplied, order: list[int], *,
            positions_of: _Supplied | None = None, record: dict | None = None):
    structure = _deposit(tmp_path, file, order, positions_of=positions_of)
    return pose_from_structure(file, structure, "LIG", required=True, record=record)


def _reversed(molecule: _Supplied) -> list[int]:
    return list(reversed(molecule.heavy))


class TestAnotherStereoisomerIsRefused:

    @pytest.mark.parametrize("file, structure", [
        ("C[C@H](N)C(=O)Nc1ccccc1", "C[C@@H](N)C(=O)Nc1ccccc1"),
        ("C[C@H](O)[C@@H](N)C(=O)O", "C[C@H](O)[C@H](N)C(=O)O"),
        ("C/C=C/C(=O)Nc1ccccc1", "C/C=C\\C(=O)Nc1ccccc1"),
    ], ids=["enantiomer", "diastereomer", "E against Z"])
    def test_with_its_code_and_atoms(self, tmp_path: Path, file: str,
                                     structure: str) -> None:
        supplied, held = _Supplied(file), _Supplied(structure)
        with pytest.raises(LigandError, match="another stereoisomer") as refused:
            _placed(tmp_path, supplied, _reversed(supplied), positions_of=held)
        assert refused.value.code == "setup.ligand.stereo_mismatch"
        assert refused.value.refusal.details["atoms"]

    @pytest.mark.parametrize("smiles", [
        "C[C@H](N)C(=O)Nc1ccccc1", "C[C@H](O)[C@@H](N)C(=O)O", "C/C=C/C(=O)Nc1ccccc1"])
    def test_the_right_isomer_is_placed(self, tmp_path: Path, smiles: str) -> None:
        """In another conformer, listed in another order."""
        supplied = _Supplied(smiles)
        record: dict = {}
        placed, said = _placed(tmp_path, supplied, _reversed(supplied),
                               positions_of=_Supplied(smiles, seed=29), record=record)
        assert said.startswith("placed LIG")
        stereo = record["stereochemistry"]
        assert stereo["stated"] is True and "agree with the pose" in stereo["said"]
        assert stereo["centres"] or stereo["double_bonds"]


def test_a_3d_file_of_a_molecule_without_stereocentres_says_so(tmp_path: Path) -> None:
    """Its 3D coordinates state the geometry in full; the molecule has no
    stereocentre to state, which is not "not stated"."""
    supplied = _Supplied(PARACETAMOL)
    record: dict = {}
    _placed(tmp_path, supplied, _reversed(supplied), record=record)
    assert record["stereochemistry"] == {
        "stated": False, "said": "no stereocentre or stereo double bond in the molecule"}


def worst_angle_change(molecule, before: np.ndarray, after: np.ndarray) -> float:
    """The largest change, in degrees, of any H-X-Y angle of ``molecule``
    (its RDKit bonds) from ``before`` to ``after``."""
    def angle(p: np.ndarray, h: int, x: int, y: int) -> float:
        u, v = p[h] - p[x], p[y] - p[x]
        return float(np.degrees(np.arccos(np.clip(
            np.dot(u, v) / np.linalg.norm(u) / np.linalg.norm(v), -1, 1))))

    worst = 0.0
    for atom in molecule.rdkit.GetAtoms():
        if atom.GetAtomicNum() != 1:
            continue
        h = atom.GetIdx()
        (x,) = [n.GetIdx() for n in atom.GetNeighbors()]
        for y in (n.GetIdx() for n in molecule.rdkit.GetAtomWithIdx(x).GetNeighbors()):
            if y != h and molecule.rdkit.GetAtomWithIdx(y).GetAtomicNum() != 1:
                worst = max(worst, abs(angle(after, h, x, y) - angle(before, h, x, y)))
    return worst


def closest_beyond_two_bonds(molecule, xyz: np.ndarray) -> float:
    """The closest any hydrogen comes to an atom three or more bonds away (nm)."""
    from rdkit.Chem import GetDistanceMatrix

    bonds = GetDistanceMatrix(molecule.rdkit)
    closest = np.inf
    for atom in molecule.rdkit.GetAtoms():
        if atom.GetAtomicNum() == 1:
            h = atom.GetIdx()
            far = bonds[h] >= 3
            if far.any():
                closest = min(closest, float(np.linalg.norm(xyz[far] - xyz[h], axis=1).min()))
    return closest


def _x_h_lengths(molecule: _Supplied, xyz: np.ndarray) -> np.ndarray:
    return np.array([np.linalg.norm(xyz[h] - xyz[p]) for h, p in
                     sorted(_hydrogen_parents(molecule, xyz, molecule.heavy).items())])


class TestHydrogens:

    def test_a_flexible_ligand_in_another_conformer_keeps_its_hydrogens_clear(
            self, tmp_path: Path) -> None:
        """Imatinib's file conformer and the structure's differ, as an ideal
        component's and a deposit's always do."""
        supplied = _Supplied(IMATINIB, seed=11)
        supplied_before = supplied.conformers[0].m_as("nanometer").copy()
        before = _x_h_lengths(supplied, supplied_before)
        record: dict = {}
        placed, _ = _placed(tmp_path, supplied, _reversed(supplied),
                            positions_of=_Supplied(IMATINIB, seed=3), record=record)
        after = placed.conformers[0].m_as("nanometer")
        assert closest_beyond_two_bonds(supplied, after) >= HYDROGEN_CLEARANCE_NM
        assert record["closest_hydrogen_nm"] >= HYDROGEN_CLEARANCE_NM
        assert np.allclose(_x_h_lengths(supplied, after), before, atol=1e-6)
        # Lengths survive any rotation; the angles are what show a hydrogen
        # laid off its own bond.
        assert worst_angle_change(supplied, supplied_before, after) <= 15.0

    @pytest.mark.parametrize("seed", range(1, 9))
    def test_a_reordered_ligand_of_many_symmetries_is_placed_exactly(
            self, tmp_path: Path, seed: int) -> None:
        """Two CF3 groups and a tBu, the file renumbered, on a rigid copy of
        its own conformer: every hydrogen lands where the turned file has it.
        Four of these orders took a wrong match when only 256 were ranked;
        the conformer case is in `test_a_ligand_s_match_is_chosen_by_its_chemistry`."""
        original = _Supplied(CF3_TBU)
        n = original.rdkit.GetNumAtoms()
        file = _Renumbered(original, [int(i) for i in np.random.default_rng(seed).permutation(n)])
        before = file.conformers[0].m_as("nanometer").copy()
        record: dict = {}
        structure = _deposit(tmp_path, original, original.heavy)
        placed, _ = pose_from_structure(file, structure, "LIG", required=True,
                                        record=record)
        assert np.allclose(placed.conformers[0].m_as("nanometer"), _turned(before),
                           atol=2e-4)


def test_a_residue_with_its_hydrogens_is_matched_on_its_heavy_atoms(
        tmp_path: Path) -> None:
    supplied = _Supplied("NC(=N)c1ccccc1")
    every = list(range(supplied.rdkit.GetNumAtoms()))
    before = supplied.conformers[0].m_as("nanometer").copy()
    placed, said = _placed(tmp_path, supplied, list(reversed(every)))
    assert said.startswith("placed LIG")
    assert np.allclose(placed.conformers[0].m_as("nanometer"), _turned(before), atol=2e-4)


def test_an_isomer_is_refused_naming_the_atoms(tmp_path: Path) -> None:
    supplied = _Supplied(PARACETAMOL)
    isomer = _Supplied("CC(=O)Nc1cccc(O)c1")
    with pytest.raises(LigandError, match="Not matched") as refused:
        _placed(tmp_path, supplied, isomer.heavy, positions_of=isomer)
    assert refused.value.code == "setup.ligand.atoms_unmatched"
    details = refused.value.refusal.details
    assert details["file_atoms"] or details["structure_atoms"]


def test_what_is_placed_is_recorded(tmp_path: Path) -> None:
    supplied = _Supplied(PARACETAMOL)
    order = _reversed(supplied)
    record: dict = {}
    _placed(tmp_path, supplied, order, record=record)
    assert record["pose"] == "structure"
    assert record["residue"]["name"] == "LIG" and record["residue"]["copy"] == 1
    # Each file heavy atom to the structure atom written at its place.
    written = {index: serial for serial, index in enumerate(order)}
    assert {m["file_atom"]: m["structure_atom"] for m in record["mapping"]} == written
