"""A ligand is placed from the files and deposits people have, not only clean pairs.

Round 3's tests paired two MMFF conformers without noise, so file and
structure had the same bond lengths and angles, and the bars it refused on
were never met. A deposit has noise; a file may come from UFF, a bare ETKDG
embedding or a 2D depiction; a planar group may sit crowded against a ring.
Each case here was refused by the code before these tests, or placed with a
planar group turned out of its plane: a right diastereomer whose halves the
graph cannot tell apart, phosphates from a UFF or ETKDG file (P-O- drawn
1.69 A), a 2D benzamidinium (the paper's ligand), a coplanar primary amide
twisted 58 degrees. Where a case is placed, each atom is held to lie on its
own atom of the deposit, or one of its symmetry class.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("rdkit")
pytest.importorskip("mdtraj")

from rdkit import Chem  # noqa: E402
from rdkit.Chem import AllChem  # noqa: E402
from rdkit.Chem import rdMolTransforms  # noqa: E402

from fastmdxplora.setup.ligand import (  # noqa: E402
    HYDROGEN_OVERLAP_NM,
    LigandError,
    pose_from_structure,
)
from fastmdxplora.validation.ligand_mapping import molecule_from_file  # noqa: E402
from tests.test_hydrogens_follow_the_atom_they_are_bonded_to import _turned  # noqa: E402

TREHALOSE_AB = ("OC[C@H]1O[C@H](O[C@@H]2O[C@H](CO)[C@@H](O)[C@H](O)[C@H]2O)"
                "[C@H](O)[C@@H](O)[C@@H]1O")
TRANEXAMIC = "NC[C@H]1CC[C@H](C(=O)O)CC1"
MESO_TARTARIC = "O[C@@H]([C@H](O)C(=O)O)C(=O)O"
ATP = "Nc1ncnc2c1ncn2[C@@H]1O[C@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)[C@@H](O)[C@H]1O"
PHYTATE = ("O=P(O)(O)O[C@H]1[C@H](OP(=O)(O)O)[C@@H](OP(=O)(O)O)[C@H](OP(=O)(O)O)"
           "[C@@H](OP(=O)(O)O)[C@@H]1OP(=O)(O)O")


def _built(smiles: str, seed: int, how: str = "mmff") -> Chem.Mol:
    mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
    parameters = AllChem.ETKDGv3()
    parameters.randomSeed = seed
    assert AllChem.EmbedMolecule(mol, parameters) == 0
    if how == "mmff":
        AllChem.MMFFOptimizeMolecule(mol, maxIters=2000)
    elif how == "uff":
        AllChem.UFFOptimizeMolecule(mol, maxIters=2000)
    return mol


def _deposit(path: Path, mol: Chem.Mol, order: list[int], noise_a: float, seed: int) -> Path:
    """The heavy atoms of ``mol``, turned, in ``order``, with Gaussian noise
    of ``noise_a`` A on each coordinate."""
    xyz = _turned(mol.GetConformer().GetPositions() / 10.0) * 10.0
    xyz = xyz + np.random.default_rng(seed).normal(0.0, noise_a, xyz.shape)
    lines = []
    for serial, index in enumerate(order, start=1):
        symbol = mol.GetAtomWithIdx(index).GetSymbol()
        x, y, z = xyz[index]
        lines.append(f"HETATM{serial:5d} {symbol + str(serial):<4s} LIG A 999    "
                     f"{x:8.3f}{y:8.3f}{z:8.3f}  1.00  0.00          {symbol:>2s}")
    path.write_text("\n".join(lines) + "\nEND\n", encoding="utf-8")
    return path


def _order(held: Chem.Mol, seed: int) -> list[int]:
    """The order the deposit lists ``held``'s heavy atoms in."""
    heavy = [a.GetIdx() for a in held.GetAtoms() if a.GetAtomicNum() > 1]
    return [int(i) for i in np.random.default_rng(seed).permutation(heavy)]


def misplaced(file: Chem.Mol, held: Chem.Mol, record: dict, seed: int,
              deposit: Path | None = None, within_a: float = 0.05) -> list[int]:
    """File atoms laid on a deposit atom that is not chemically theirs: of
    another symmetry class of the molecule, bond orders, charges and
    stereochemistry counted. ``file`` and ``held`` are built from one SMILES,
    so atom k of each is the same atom of the molecule.

    With ``deposit``, a terminal pair the noise has made alike is not held
    to its truth: two oxygens on one atom (a COOH's C=O and C-OH) whose
    deposited lengths are within ``within_a`` of each other, or the other
    way round from the file's, which no reading of the deposit can tell."""
    ranks = list(Chem.CanonicalRankAtoms(file, breakTies=False))
    held_ranks = list(Chem.CanonicalRankAtoms(held, breakTies=False))
    order = _order(held, seed)
    alike: set[int] = set()
    if deposit is not None:
        lines = [line for line in deposit.read_text().splitlines() if line.startswith("HETATM")]
        xyz = {order[j]: np.array([float(line[30:38]), float(line[38:46]), float(line[46:54])])
               for j, line in enumerate(lines)}
        for atom in held.GetAtoms():
            ends = [n.GetIdx() for n in atom.GetNeighbors()
                    if n.GetSymbol() == "O" and n.GetDegree() - n.GetTotalNumHs(True) <= 1
                    and len([m for m in n.GetNeighbors() if m.GetAtomicNum() > 1]) == 1]
            if len(ends) >= 2:
                lengths = [float(np.linalg.norm(xyz[e] - xyz[atom.GetIdx()])) for e in ends]
                orders = [held.GetBondBetweenAtoms(atom.GetIdx(), e).GetBondTypeAsDouble()
                          for e in ends]
                # The double bond's length first: shortest first in truth.
                by_order = [x for _, x in sorted(zip(orders, lengths), reverse=True)]
                if max(lengths) - min(lengths) < within_a or by_order != sorted(by_order):
                    alike.update(ends)
    return [m["file_atom"] for m in record["mapping"]
            if ranks[m["file_atom"]] != held_ranks[order[m["structure_atom"]]]
            and m["file_atom"] not in alike]


def _place(tmp_path: Path, file: Chem.Mol, held: Chem.Mol, *, noise_a: float = 0.0,
           seed: int = 1):
    sdf = tmp_path / "file.sdf"
    sdf.write_text(Chem.MolToMolBlock(file), encoding="utf-8")
    molecule = molecule_from_file(sdf)
    order = _order(held, seed)
    structure = _deposit(tmp_path / "input.pdb", held, order, noise_a, seed)
    record: dict = {}
    placed, _ = pose_from_structure(molecule, structure, "LIG", required=True, record=record)
    return placed.conformers[0].m_as("nanometer"), record


@pytest.mark.parametrize("smiles", [TREHALOSE_AB, TRANEXAMIC],
                         ids=["alpha,beta-trehalose", "trans-tranexamic acid"])
@pytest.mark.parametrize("noise_a", [0.02, 0.03])
def test_a_right_isomer_is_placed_under_a_deposit_s_noise(
        tmp_path: Path, smiles: str, noise_a: float) -> None:
    """Over forty conformer pairs each, every atom on its own: of each
    parametrisation the code before this test refused some (trehalose 9 or
    13 of 40, tranexamic acid 2 or 3) as another stereoisomer or another
    molecule.
    Meso-tartaric acid and noiseless pairs it placed are not claimed here."""
    for seed in range(1, 41):
        folder = tmp_path / str(seed)
        folder.mkdir()
        file, held = _built(smiles, 100 + seed), _built(smiles, 200 + seed)
        _, record = _place(folder, file, held, noise_a=noise_a, seed=seed)
        assert record["stereochemistry"]["stated"] is True
        assert misplaced(file, held, record, seed, folder / "input.pdb") == [], seed


@pytest.mark.parametrize("smiles", [ATP, PHYTATE], ids=["ATP", "phytate"])
@pytest.mark.parametrize("how", ["uff", "etkdg"])
def test_a_file_of_a_weaker_force_field_is_placed(tmp_path: Path, smiles: str, how: str) -> None:
    """Its P-O- is 1.69 A where a deposit's is 1.51: the lengths rank the
    matches, and never say the deposit is another molecule."""
    for seed in range(1, 4):
        folder = tmp_path / str(seed)
        folder.mkdir()
        file, held = _built(smiles, 100 + seed, how), _built(smiles, 200 + seed)
        _, record = _place(folder, file, held, noise_a=0.03, seed=seed)
        assert misplaced(file, held, record, seed, folder / "input.pdb") == [], seed


def _depiction(smiles: str, *, wedges: bool = True, heavy_length_a: float | None = None
               ) -> Chem.Mol:
    mol = Chem.MolFromSmiles(smiles)
    if heavy_length_a is not None:
        # Heavy atoms drawn at a program's length, hydrogens added in the
        # page as a reader completing the file adds them.
        AllChem.Compute2DCoords(mol, bondLength=heavy_length_a)
        mol = Chem.AddHs(mol, addCoords=True)
    else:
        mol = Chem.AddHs(mol)
        AllChem.Compute2DCoords(mol)
    if wedges:
        Chem.WedgeMolBonds(mol, mol.GetConformer())
    return mol


@pytest.mark.parametrize("smiles", [
    "NC(=[NH2+])c1ccccc1", "[O-]S(=O)(=O)c1ccccc1", "COP(=O)([O-])[O-]",
    "CC(C)Cc1ccc(cc1)[C@@H](C)C(=O)[O-]",
    "Nc1ncnc2c1ncn2[C@@H]1O[C@H](COP(=O)([O-])OP(=O)([O-])OP(=O)([O-])[O-])"
    "[C@@H](O)[C@H]1O"],
    ids=["benzamidinium", "benzenesulfonate", "methyl phosphate", "ibuprofen anion", "ATP4-"])
def test_a_charged_2d_file_is_placed_with_ideal_hydrogens(tmp_path: Path, smiles: str) -> None:
    xyz, record = _place(tmp_path, _depiction(smiles), _built(smiles, 301))
    assert "flat" in record["hydrogens"]
    assert record["closest_hydrogen_nm"] >= HYDROGEN_OVERLAP_NM


@pytest.mark.parametrize("length_a", [1.0, 1.5, 1.54])
def test_a_2d_file_takes_its_stereochemistry_from_its_wedges_only(
        tmp_path: Path, length_a: float) -> None:
    """Glucose drawn with wedges at a program's bond length: read as 2D,
    whatever the length, so its handedness comes from the wedges, never from
    the page. On galactose, one centre the other way, it is refused."""
    glucose = "OC[C@H]1O[C@H](O)[C@H](O)[C@@H](O)[C@@H]1O"
    galactose = "OC[C@H]1O[C@H](O)[C@H](O)[C@@H](O)[C@H]1O"
    drawn = _depiction(glucose, heavy_length_a=length_a)
    _, record = _place(tmp_path, drawn, _built(glucose, 301))
    assert record["stereochemistry"]["source"] == "the file's stereo flags"
    with pytest.raises(LigandError) as refused:
        _place(tmp_path, drawn, _built(galactose, 301))
    assert refused.value.code == "setup.ligand.stereo_mismatch"


def _out_of_plane_degrees(xyz: np.ndarray, hydrogen: int, parent: int, plane: tuple) -> float:
    a, b, c = (xyz[i] for i in plane)
    normal = np.cross(b - a, c - a)
    normal /= np.linalg.norm(normal)
    bond = xyz[hydrogen] - xyz[parent]
    return float(np.degrees(np.arcsin(abs(np.dot(bond, normal)) / np.linalg.norm(bond))))


@pytest.mark.parametrize("torsion", [0.0])
def test_a_crowded_planar_group_keeps_its_plane(tmp_path: Path, torsion: float) -> None:
    """2,6-dimethylbenzamide with its amide coplanar with the ring, the NH2
    against the methyls: its hydrogens may flip within the amide's plane,
    never turn out of it."""
    smiles = "Cc1cccc(C)c1C(N)=O"
    held = _built(smiles, 201)
    ring_c, amide_c = held.GetSubstructMatch(Chem.MolFromSmarts("c-C(N)=O"))[:2]
    ortho = next(n.GetIdx() for n in held.GetAtomWithIdx(ring_c).GetNeighbors()
                 if n.GetIdx() != amide_c)
    nitrogen = next(n.GetIdx() for n in held.GetAtomWithIdx(amide_c).GetNeighbors()
                    if n.GetSymbol() == "N")
    rdMolTransforms.SetDihedralDeg(held.GetConformer(), ortho, ring_c, amide_c, nitrogen,
                                   torsion)
    file = _built(smiles, 101)
    xyz, _ = _place(tmp_path, file, held)
    n_file = file.GetSubstructMatch(Chem.MolFromSmarts("c-C(N)=O"))
    carbon, oxygen = n_file[1], n_file[3]
    amide_n = n_file[2]
    for hydrogen in (n.GetIdx() for n in file.GetAtomWithIdx(amide_n).GetNeighbors()
                     if n.GetAtomicNum() == 1):
        assert _out_of_plane_degrees(xyz, hydrogen, amide_n,
                                     (amide_n, carbon, oxygen)) < 12.0
