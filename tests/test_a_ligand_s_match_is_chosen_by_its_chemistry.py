"""Of the heavy-atom matches, the one the file's chemistry says, placed soundly.

The match was the one that laid the file's heavy atoms best, rigidly, on the
structure's, and then had to agree with the file's stereochemistry. Where
the file's conformer is not the structure's (an ideal component against a
deposit), that took the mirror half-swap of a meso compound, or the ring's
reversal at a trans-1,4-cyclohexyl centre, about half the time, and refused
the right isomer as another. It put a ketone's C=O on an alcohol's C-OH, or
a vinyl on a propyl, where the graph alone cannot tell them apart, and read a
phosphate's oxygens as a stereocentre, refusing ATP and NAD. Every match was
enumerated, so phytate and suramin stopped at the step limit. The hydrogens,
laid by one rotation per neighbourhood, kept their lengths and not their
angles: an N-methyl's H-C-N at 76 degrees, an isobutyl's H-C-C at 71, ATP's
P-O-H at 24.

Now the matches are ranked by the file's own internal geometry (each bond's
length for its order, its 1-3 distances), then by its stereochemistry, then
by fit; interchangeable terminal atoms (a CF3's F, a PO3's O) are given to
the structure's by their lengths and positions rather than searched; and each
hydrogen is built from internal coordinates, its rotor turned clear.

Each case is the file as one conformer of the molecule and the structure the
heavy atoms of another, listed in a shuffled order.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("rdkit")
pytest.importorskip("mdtraj")

from rdkit import Chem  # noqa: E402
from rdkit.Chem import AllChem  # noqa: E402

from fastmdxplora.setup.ligand import (  # noqa: E402
    HYDROGEN_CLEARANCE_NM,
    LigandError,
    pose_from_structure,
)
from fastmdxplora.validation.ligand_mapping import molecule_from_file  # noqa: E402
from tests.test_a_ligand_is_placed_as_its_own_isomer import (  # noqa: E402
    closest_beyond_two_bonds,
    worst_angle_change,
)
from tests.test_a_pose_is_matched_atom_for_atom import _deposit, _Supplied  # noqa: E402

CASES = {
    "meso-tartaric acid": "O[C@@H]([C@H](O)C(=O)O)C(=O)O",
    "trans-tranexamic acid": "NC[C@H]1CC[C@H](C(=O)O)CC1",
    "trans-1,4-dimethylcyclohexane": "C[C@H]1CC[C@@H](C)CC1",
    "4-hydroxycyclohexanone": "OC1CCC(=O)CC1",
    "4-allyl-1-propylbenzene": "C=CCc1ccc(CCC)cc1",
    "ATP4-": ("Nc1ncnc2c1ncn2[C@@H]1O[C@H](COP(=O)([O-])OP(=O)([O-])OP(=O)([O-])[O-])"
              "[C@@H](O)[C@H]1O"),
    "ATP": "Nc1ncnc2c1ncn2[C@@H]1O[C@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)[C@@H](O)[C@H]1O",
    "NAD+": ("NC(=O)c1ccc[n+](c1)[C@@H]1O[C@H](COP([O-])(=O)OP(=O)(O)OC[C@H]2O[C@H]"
             "([C@H](O)[C@@H]2O)n2cnc3c(N)ncnc23)[C@@H](O)[C@H]1O"),
    "phytate": ("O=P(O)(O)O[C@H]1[C@H](OP(=O)(O)O)[C@@H](OP(=O)(O)O)[C@H](OP(=O)(O)O)"
                "[C@@H](OP(=O)(O)O)[C@@H]1OP(=O)(O)O"),
    "suramin": ("Cc1ccc(cc1NC(=O)c1cccc(NC(=O)Nc2cccc(c2)C(=O)Nc2cc(C)ccc2C(=O)Nc2ccc"
                "(c3cc(cc(c23)S(=O)(=O)O)S(=O)(=O)O)S(=O)(=O)O)c1)C(=O)Nc1ccc(c2cc(cc"
                "(c12)S(=O)(=O)O)S(=O)(=O)O)S(=O)(=O)O"),
    "imatinib": "Cc1ccc(NC(=O)c2ccc(CN3CCN(C)CC3)cc2)cc1Nc1nccc(-c2cccnc2)n1",
    "ibuprofen": "CC(C)Cc1ccc(cc1)[C@@H](C)C(=O)O",
    "CF3/tBu": "CC(C)(C)c1cc(C(F)(F)F)cc(C(F)(F)F)c1",
}


def _placed(tmp_path: Path, smiles: str, seed: int, *, read_from_3d: bool = False):
    """The file one conformer of ``smiles``, the structure another's heavy
    atoms, shuffled; the placed molecule, its record, the order written.
    ``read_from_3d`` tags the file's stereocentres from its coordinates, as
    a 3D SDF is read (and as OpenFF tags them), a phosphate's P among them."""
    file = _Supplied(smiles, seed=100 + seed)
    if read_from_3d:
        Chem.AssignStereochemistryFrom3D(file.rdkit)
    held = _Supplied(smiles, seed=200 + seed)
    order = [int(i) for i in np.random.default_rng(seed).permutation(file.heavy)]
    structure = _deposit(tmp_path, file, order, positions_of=held)
    before = file.conformers[0].m_as("nanometer").copy()
    record: dict = {}
    placed, _ = pose_from_structure(file, structure, "LIG", required=True, record=record)
    return file, before, placed.conformers[0].m_as("nanometer"), record, order


def _the_molecules_own(smiles: str, record: dict, order: list[int]) -> bool:
    """Whether each file atom went to an atom of the same chemistry: every
    bond kept with its order, every atom with its charge and hydrogens. A
    charged phosphate's or sulfate's =O and O-, which no structure tells
    apart, are taken as one."""
    mol = Chem.MolFromSmiles(smiles)
    to = {m["file_atom"]: order[m["structure_atom"]] for m in record["mapping"]}

    def kind(atom) -> tuple:
        resonant = (atom.GetAtomicNum() == 8 and atom.GetDegree() == 1
                    and atom.GetNeighbors()[0].GetAtomicNum() in (15, 16)
                    and atom.GetTotalNumHs() == 0)
        return (atom.GetAtomicNum(), atom.GetTotalNumHs(),
                "resonant" if resonant else atom.GetFormalCharge())

    for bond in mol.GetBonds():
        i, j = bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()
        there = mol.GetBondBetweenAtoms(to[i], to[j])
        if there is None:
            return False
        if bond.GetBondType() != there.GetBondType() and not (
                kind(mol.GetAtomWithIdx(i))[2] == "resonant"
                or kind(mol.GetAtomWithIdx(j))[2] == "resonant"):
            return False
    return all(kind(mol.GetAtomWithIdx(i)) == kind(mol.GetAtomWithIdx(to[i])) for i in to)


@pytest.mark.parametrize("name", [
    "meso-tartaric acid", "trans-tranexamic acid", "trans-1,4-dimethylcyclohexane"])
@pytest.mark.parametrize("seed", range(1, 9))
def test_the_right_isomer_is_placed_whichever_symmetry_turns_a_centre(
        tmp_path: Path, name: str, seed: int) -> None:
    """A meso compound, a ring's cis/trans centres: one of the matches turns
    a centre over, and another places the same molecule."""
    smiles = CASES[name]
    _, _, _, record, order = _placed(tmp_path, smiles, seed)
    assert record["stereochemistry"]["stated"] is True
    assert _the_molecules_own(smiles, record, order)


@pytest.mark.parametrize("name", ["4-hydroxycyclohexanone", "4-allyl-1-propylbenzene"])
@pytest.mark.parametrize("seed", range(1, 9))
def test_a_bond_s_order_decides_what_the_graph_cannot(
        tmp_path: Path, name: str, seed: int) -> None:
    """A C=O beside a C-OH on mirror ring positions, a vinyl beside a
    propyl: the C=O goes on the structure's C=O."""
    smiles = CASES[name]
    _, _, _, record, order = _placed(tmp_path, smiles, seed)
    assert _the_molecules_own(smiles, record, order)


@pytest.mark.parametrize("name", ["ATP4-", "ATP", "NAD+"])
@pytest.mark.parametrize("seed", range(1, 7))
def test_a_cofactor_s_phosphates_are_not_read_as_stereocentres(
        tmp_path: Path, name: str, seed: int) -> None:
    smiles = CASES[name]
    file, before, after, record, order = _placed(tmp_path, smiles, seed, read_from_3d=True)
    assert _the_molecules_own(smiles, record, order)
    # Each P read as a stereocentre is said not counted: its handedness is
    # only which of its interchangeable oxygens is which.
    assert any(n.startswith("P") for n in record["stereochemistry"]["not_checked"])
    assert worst_angle_change(file, before, after) <= 15.0


@pytest.mark.parametrize("name, seeds", [("phytate", 3), ("suramin", 1)])
def test_a_ligand_of_many_symmetric_groups_is_placed_inside_the_limit(
        tmp_path: Path, name: str, seeds: int) -> None:
    smiles = CASES[name]
    for seed in range(1, seeds + 1):
        folder = tmp_path / str(seed)
        folder.mkdir()
        file, before, after, record, order = _placed(folder, smiles, seed)
        assert _the_molecules_own(smiles, record, order)
        assert closest_beyond_two_bonds(file, after) >= HYDROGEN_CLEARANCE_NM


@pytest.mark.parametrize("name", ["imatinib", "ibuprofen", "ATP", "CF3/tBu"])
@pytest.mark.parametrize("seed", range(1, 7))
def test_hydrogens_keep_their_angles_in_another_conformer(
        tmp_path: Path, name: str, seed: int) -> None:
    """Imatinib's N-methyl, ibuprofen's isobutyl, ATP's P-O-H: each H-X-Y
    angle within 15 degrees of the file's, each hydrogen clear of atoms
    three or more bonds away."""
    file, before, after, record, _ = _placed(tmp_path, CASES[name], seed)
    assert worst_angle_change(file, before, after) <= 15.0
    assert record["worst_hydrogen_angle_deg"] <= 15.0
    assert closest_beyond_two_bonds(file, after) >= HYDROGEN_CLEARANCE_NM


@pytest.mark.parametrize("smiles", ["NC(=N)c1ccccc1", CASES["imatinib"], CASES["CF3/tBu"]])
def test_a_flat_file_gets_ideal_hydrogens(tmp_path: Path, smiles: str) -> None:
    """A 2D depiction: its X-H drawn 1.5 A long and in the page. Built from
    ideal geometry instead, at the X-H length for the element."""
    drawn = Chem.AddHs(Chem.MolFromSmiles(smiles))
    AllChem.Compute2DCoords(drawn)
    path = tmp_path / "drawn.sdf"
    path.write_text(Chem.MolToMolBlock(drawn), encoding="utf-8")
    file = molecule_from_file(path)
    held = _Supplied(smiles, seed=7)
    heavy = [i for i, a in enumerate(file.atoms) if a.atomic_number > 1]
    structure = _deposit(tmp_path, held, [int(i) for i in
                                          np.random.default_rng(2).permutation(heavy)])
    record: dict = {}
    placed, _ = pose_from_structure(file, structure, "LIG", required=True, record=record)
    xyz = placed.conformers[0].m_as("nanometer")
    assert "flat" in record["hydrogens"]
    assert record["stereochemistry"]["said"] == "stereochemistry not stated in the file"
    for atom in file.rdkit.GetAtoms():
        if atom.GetAtomicNum() == 1:
            (x,) = [n.GetIdx() for n in atom.GetNeighbors()]
            assert 0.095 <= np.linalg.norm(xyz[atom.GetIdx()] - xyz[x]) <= 0.11
    assert closest_beyond_two_bonds(file, xyz) >= HYDROGEN_CLEARANCE_NM


def test_a_flipped_sulfoxide_is_still_refused(tmp_path: Path) -> None:
    """A stereocentre whose neighbours are not interchangeable is held."""
    file, held = _Supplied("C[S@](=O)c1ccccc1"), _Supplied("C[S@@](=O)c1ccccc1")
    structure = _deposit(tmp_path, file, list(reversed(file.heavy)), positions_of=held)
    with pytest.raises(LigandError) as refused:
        pose_from_structure(file, structure, "LIG", required=True)
    assert refused.value.code == "setup.ligand.stereo_mismatch"
