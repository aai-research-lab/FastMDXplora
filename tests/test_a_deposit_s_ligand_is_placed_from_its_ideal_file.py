"""A real deposit's ligand is placed from its ideal component file.

The paper's case: the Chemical Component Dictionary's ideal SDF of a ligand,
placed on the residue a PDB entry deposited. A deposit's bonds lie up to
0.2 A from an ideal component's (erlotinib's O3-C13 at 1.52 against 1.36,
acetazolamide's N2-N3 at 1.43 against 1.26), and a poorly resolved sugar's
C1' can be nearly flat. Fixed bars on bond lengths (0.15 A) and on hydrogen
angles (15 degrees) refused 13 of 47 copies of right ligands that `main`
placed, calling them "not the same molecule". Now only another molecule, a
stereoisomer no match explains, or a hydrogen overlap left after the rotor
scan refuses; bond lengths and angles are recorded.

`tests/data/ligands_real/` holds each ligand's ideal SDF (files.rcsb.org)
and its residue from the entry (one copy, heavy atoms, the first altloc).
Ap5A is taken from 1AKE in `tests/data/assemblies`, its file an RDKit build.
"""

from __future__ import annotations

import gzip
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("rdkit")
pytest.importorskip("mdtraj")

from rdkit import Chem  # noqa: E402
from rdkit.Chem import AllChem  # noqa: E402

from fastmdxplora.setup.ligand import (  # noqa: E402
    HYDROGEN_OVERLAP_NM,
    LigandError,
    pose_from_structure,
)
from fastmdxplora.validation.ligand_mapping import molecule_from_file  # noqa: E402

DATA = Path(__file__).parent / "data"
REAL = DATA / "ligands_real"

#: Entry, component: erlotinib, dasatinib, acetazolamide, ritonavir, NADP,
#: NAD, tranexamic acid, protoporphyrin IX (its ideal file all zeros, a
#: depiction), benzamidine.
DEPOSITS = [("1m17", "AQ4"), ("2gqg", "1N1"), ("3hs4", "AZM"), ("1hxw", "RIT"),
            ("1rx2", "NAP"), ("1ldm", "NAD"), ("1ceb", "AMH"), ("2hre", "PP9"),
            ("3ptb", "BEN")]

AP5A = ("Nc1ncnc2c1ncn2[C@@H]1O[C@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)OP(=O)(O)OP(=O)(O)"
        "OC[C@H]2O[C@@H](n3cnc4c(N)ncnc43)[C@H](O)[C@@H]2O)[C@@H](O)[C@H]1O")


def _shuffled(path: Path, tmp_path: Path, seed: int) -> Path:
    """The file with its atoms listed in another order."""
    mol = Chem.MolFromMolFile(str(path), removeHs=False)
    order = [int(i) for i in np.random.default_rng(seed).permutation(mol.GetNumAtoms())]
    out = tmp_path / f"shuffled-{seed}.sdf"
    out.write_text(Chem.MolToMolBlock(Chem.RenumberAtoms(mol, order)), encoding="utf-8")
    return out


def _placed(sdf: Path, structure: Path, code: str):
    record: dict = {}
    placed, said = pose_from_structure(molecule_from_file(sdf), structure, code,
                                       required=True, record=record)
    return placed, said, record


def _ap5a_deposit(tmp_path: Path) -> Path:
    lines = [line[:16] + " " + line[17:] for line in
             gzip.open(DATA / "assemblies" / "1AKE.pdb.gz", "rt").read().splitlines()
             if line.startswith("HETATM") and line[17:20] == "AP5" and line[21] == "B"
             and line[16] in " A"]
    path = tmp_path / "1ake_AP5.pdb"
    path.write_text("\n".join(lines) + "\nEND\n", encoding="utf-8")
    return path


def _built(smiles: str, path: Path, seed: int = 7) -> Path:
    mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
    parameters = AllChem.ETKDGv3()
    parameters.randomSeed = seed
    assert AllChem.EmbedMolecule(mol, parameters) == 0
    AllChem.MMFFOptimizeMolecule(mol, maxIters=2000)
    path.write_text(Chem.MolToMolBlock(mol), encoding="utf-8")
    return path


@pytest.mark.parametrize("entry, code", DEPOSITS)
@pytest.mark.parametrize("order", ["as written", "shuffled"])
def test_a_right_ligand_is_placed_on_its_deposit(tmp_path: Path, entry: str, code: str,
                                                 order: str) -> None:
    sdf = REAL / f"{code}_ideal.sdf"
    if order == "shuffled":
        sdf = _shuffled(sdf, tmp_path, 5)
    placed, said, record = _placed(sdf, REAL / f"{entry}_{code}.pdb", code)
    assert said.startswith(f"placed {code}")
    # What the deposit departs by is recorded, not refused.
    assert record["worst_bond_nm"] >= 0.0
    assert record["closest_hydrogen_nm"] is None or \
        record["closest_hydrogen_nm"] >= HYDROGEN_OVERLAP_NM


def test_erlotinib_s_stretched_bond_is_recorded(tmp_path: Path) -> None:
    """The deposit's O3-C13 is 0.16 A longer than the ideal file's: it was
    refused as another molecule."""
    _, _, record = _placed(REAL / "AQ4_ideal.sdf", REAL / "1m17_AQ4.pdb", "AQ4")
    assert record["worst_bond_nm"] > 0.015


def test_ap5a_on_1ake_is_placed_though_its_c1_is_flattened(tmp_path: Path) -> None:
    """The deposit's anomeric carbons are nearly flat (their angles sum to
    356 degrees): no hydrogen there can keep the file's angles, and the
    worst is recorded."""
    sdf = _built(AP5A, tmp_path / "AP5.sdf")
    _, said, record = _placed(sdf, _ap5a_deposit(tmp_path), "AP5")
    assert said.startswith("placed AP5")
    assert record["worst_hydrogen_angle_deg"] is not None


def _with_a_centre_inverted(sdf: Path, out: Path, skip_phosphorus: bool = True) -> Path | None:
    """The file's molecule with its first stated stereocentre (not a P)
    inverted, built in 3D: a diastereomer, or for one centre the
    enantiomer."""
    mol = Chem.MolFromMolFile(str(sdf), removeHs=False)
    Chem.AssignStereochemistryFrom3D(mol)
    heavy = Chem.RemoveHs(mol)
    centres = [a.GetIdx() for a in heavy.GetAtoms()
               if a.GetChiralTag() != Chem.ChiralType.CHI_UNSPECIFIED
               and not (skip_phosphorus and a.GetAtomicNum() == 15)]
    if not centres:
        return None
    heavy.GetAtomWithIdx(centres[0]).InvertChirality()
    return _built(Chem.MolToSmiles(heavy), out)


@pytest.mark.parametrize("entry, code", [("1hxw", "RIT"), ("1rx2", "NAP"), ("1ldm", "NAD"),
                                         ("1ceb", "AMH")])
def test_a_wrong_stereoisomer_of_it_is_refused(tmp_path: Path, entry: str, code: str) -> None:
    wrong = _with_a_centre_inverted(REAL / f"{code}_ideal.sdf", tmp_path / "wrong.sdf")
    assert wrong is not None
    with pytest.raises(LigandError) as refused:
        _placed(wrong, REAL / f"{entry}_{code}.pdb", code)
    assert refused.value.code == "setup.ligand.stereo_mismatch"


def test_a_wrong_stereoisomer_of_ap5a_is_refused(tmp_path: Path) -> None:
    right = _built(AP5A, tmp_path / "AP5.sdf")
    wrong = _with_a_centre_inverted(right, tmp_path / "wrong.sdf")
    with pytest.raises(LigandError) as refused:
        _placed(wrong, _ap5a_deposit(tmp_path), "AP5")
    assert refused.value.code == "setup.ligand.stereo_mismatch"


def test_another_molecule_is_still_refused(tmp_path: Path) -> None:
    """A toluamidine where the deposit has benzamidine: another molecule."""
    other = _built("Cc1ccc(C(N)=N)cc1", tmp_path / "other.sdf")
    with pytest.raises(LigandError) as refused:
        _placed(other, REAL / "3ptb_BEN.pdb", "BEN")
    assert refused.value.code == "setup.ligand.atoms_unmatched"
