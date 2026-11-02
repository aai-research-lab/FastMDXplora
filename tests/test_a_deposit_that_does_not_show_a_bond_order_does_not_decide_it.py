"""A deposit that does not show a bond order does not decide the isomer.

Round 5 tied two matches on the file's bond orders where their scores
differed by no more than (0.025 A)^2 for each bond they assign differently.
Two matches that lay expected lengths e and e' on a bond of length L differ
by (e' - e)(2L - e - e'), which a deposit's noise moves linearly, so the tie
held only while a deposit's lengths agreed to about 0.005 A. And each bond
was held to the file's own length for it, so a bare embedding's equivalent
ring bonds (1.48 and 1.52 A) made a molecule's own symmetry cost something.
Right ligands were refused as another stereoisomer: tranexamic acid on 1CEB
from ETKDG files, meso and pseudo-symmetric molecules under 0.03 A of noise,
shikimate on 1ZUI, glyceraldehyde on 4PTN B and C and on 1T5E, and DHT on
1I37, whose C5 the deposit models flat.

Now each bond is held to one length for its symmetry class, two matches tie
within 2 |e - e'| x 0.025 A over the bonds they lay differently, a bond at no
length its elements have is no evidence, and where a tied match turns a
centre the file's isomer is placed and the record says the structure did
not decide.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("rdkit")
pytest.importorskip("mdtraj")

from rdkit import Chem  # noqa: E402
from rdkit.Chem import AllChem  # noqa: E402

from fastmdxplora.setup.ligand import LigandError, pose_from_structure  # noqa: E402
from fastmdxplora.validation.ligand_mapping import molecule_from_file  # noqa: E402
from tests.test_a_ligand_is_placed_from_rough_and_noisy_inputs import (  # noqa: E402
    _built,
    _place,
    misplaced,
)
from tests.test_a_ligand_s_bond_orders_rank_its_matches_first import (  # noqa: E402
    _epimers,
    _ideal,
    _versions,
    _wrong_names,
)

REAL = Path(__file__).parent / "data" / "ligands_real"


def _file(tmp_path: Path, mol: Chem.Mol, name: str = "file.sdf") -> Path:
    path = tmp_path / name
    path.write_text(Chem.MolToMolBlock(mol), encoding="utf-8")
    return path


def _on(tmp_path: Path, mol: Chem.Mol, deposit: str, code: str) -> dict:
    record: dict = {}
    pose_from_structure(molecule_from_file(_file(tmp_path, mol)), REAL / deposit, code,
                        required=True, record=record)
    return record


def _etkdg(mol: Chem.Mol, seed: int) -> Chem.Mol:
    """``mol`` embedded again by ETKDG with no force field after, its atoms
    listed in another order."""
    built = Chem.Mol(mol)
    parameters = AllChem.ETKDGv3()
    parameters.randomSeed = seed
    assert AllChem.EmbedMolecule(built, parameters) == 0
    order = [int(i) for i in np.random.default_rng(seed).permutation(built.GetNumAtoms())]
    return Chem.RenumberAtoms(built, order)


def test_tranexamic_acid_on_1ceb_is_placed_from_etkdg_files(tmp_path: Path) -> None:
    """Chain B, twelve ETKDG embeddings of the CCD file: 4 of 12 were
    refused, the ring's own reversal costing the difference of its
    equivalent bonds' lengths in the file."""
    mol = Chem.MolFromMolFile(str(REAL / "AMH_ideal.sdf"), removeHs=False)
    for k in range(12):
        record = _on(tmp_path, _etkdg(mol, 31 + 17 * k), "1ceb_AMH_B.pdb", "AMH")
        assert record["stereochemistry"]["stated"] is True, k


SYMMETRIC = {"alpha,beta-trehalose": "OC[C@H]1O[C@H](O[C@@H]2O[C@H](CO)[C@@H](O)[C@H](O)"
                                     "[C@H]2O)[C@H](O)[C@@H](O)[C@@H]1O",
             "trans-tranexamic acid": "NC[C@H]1CC[C@H](C(=O)O)CC1",
             "meso-tartaric acid": "OC(=O)[C@@H](O)[C@@H](O)C(O)=O",
             "meso-DAP": "N[C@@H](CCC[C@@H](N)C(=O)O)C(=O)O",
             "cis-1,2-cyclohexanediol": "O[C@@H]1CCCC[C@@H]1O",
             "myo-inositol": "O[C@H]1[C@H](O)[C@@H](O)[C@H](O)[C@H](O)[C@@H]1O"}


@pytest.mark.parametrize("name", list(SYMMETRIC))
def test_a_symmetric_ligand_from_an_etkdg_file_is_placed_under_noise(
        tmp_path: Path, name: str) -> None:
    """Twenty ETKDG files on MMFF deposits with 0.03 A of noise: 1 to 3 of
    20 of each were refused, every one as another stereoisomer."""
    smiles = SYMMETRIC[name]
    for seed in range(11, 31):
        folder = tmp_path / str(seed)
        folder.mkdir()
        file, held = _built(smiles, 100 + seed, "etkdg"), _built(smiles, 300 + seed)
        _, record = _place(folder, file, held, noise_a=0.03, seed=seed)
        assert misplaced(file, held, record, seed, folder / "input.pdb") == [], seed


def test_shikimate_on_1zui_is_placed_and_its_c8_epimer_never_as_decided(
        tmp_path: Path) -> None:
    """1ZUI models its ring without a double bond (C4=C10 1.56 A against
    C4-C5 1.54): the right file was refused in every version, and the C8
    epimer placed. The right file is placed on its names; the epimer is
    refused, or placed only with the record that the structure did not
    decide."""
    for name, mol in _versions(_ideal("SKM")):
        record = _on(tmp_path, mol, "1zui_SKM.pdb", "SKM")
        assert _wrong_names(mol, record) == [] or record["bond_orders_undecided"], name
    for smiles in _epimers(_ideal("SKM")):
        try:
            record = _on(tmp_path, _built(smiles, 7), "1zui_SKM.pdb", "SKM")
        except LigandError as refused:
            assert refused.code == "setup.ligand.stereo_mismatch"
            continue
        assert record["bond_orders_undecided"], smiles


@pytest.mark.parametrize("right, wrong, deposit", [
    ("GXV", "3GR", "4ptn_GXV_B.pdb"), ("GXV", "3GR", "4ptn_GXV_C.pdb"),
    ("3GR", "GXV", "1t5e_3GR.pdb")],
    ids=["4PTN B", "4PTN C", "1T5E C"])
def test_a_glyceraldehyde_its_deposit_does_not_resolve_is_placed_undecided(
        tmp_path: Path, right: str, wrong: str, deposit: str) -> None:
    """4PTN B and C model both C-O of glyceraldehyde 1.41 to 1.45 A, and
    1T5E's aldehyde is 0.97 A, no C-O's length: the right file was refused
    and the wrong one placed, by 0.03 A. The right file is placed with the
    record that the structure did not decide which arm is the aldehyde, the
    lengths given; the wrong one is never placed as decided."""
    for name, mol in _versions(_ideal(right)):
        record = _on(tmp_path, mol, deposit, right)
        [entry] = record["bond_orders_undecided"]
        assert "did not decide which arm carries the double bond" in entry["said"], name
        assert all(0.09 < b["length_nm"] < 0.15 for b in entry["bonds"]), name
    for name, mol in _versions(_ideal(wrong)):
        try:
            record = _on(tmp_path, mol, deposit, right)
        except LigandError:
            continue
        assert record["bond_orders_undecided"], name


def test_dht_on_1i37_whose_ring_a_is_testosterone_s_is_another_molecule(
        tmp_path: Path) -> None:
    """The deposit is labelled DHT but has C4=C5 1.31 A and C5 flat,
    testosterone's ring A: it was refused as another stereoisomer, no file
    of which could be supplied. It is refused as another molecule, the bond
    named."""
    mol = Chem.MolFromMolFile(str(REAL / "DHT_ideal.sdf"), removeHs=False)
    with pytest.raises(LigandError) as refused:
        _on(tmp_path, mol, "1i37_DHT.pdb", "DHT")
    assert refused.value.code == "setup.ligand.atoms_unmatched"
    assert "C4-C5 is 1.31 A, a C=C, where the file has C-C" in refused.value.args[0]


def test_coa_on_4ea7_records_its_phosphates_as_within_noise(tmp_path: Path) -> None:
    """Its phosphate oxygens at 1.52 and 1.54 A, 1.53 and 1.55: which is the
    P=O is the deposit's noise, and was recorded as decided."""
    mol = Chem.MolFromMolFile(str(REAL / "COA_ideal.sdf"), removeHs=False)
    record = _on(tmp_path, mol, "4ea7_COA.pdb", "COA")
    groups = record["assigned_within_noise"]
    assert len(groups) == 3
    for group in groups:
        lengths = [o["length_nm"] for o in group["given"]]
        assert max(lengths) - min(lengths) < 0.01
        assert sum(o["double"] for o in group["given"]) == 1


def test_a_phosphonate_s_oxygens_noise_apart_are_within_noise(tmp_path: Path) -> None:
    """Methyl methylphosphonic acid's P=O and P-OH deposited at 1.51 and
    1.53 A: recorded within noise; at 1.48 and 1.57, decided."""
    from rdkit.Chem import rdMolTransforms

    smiles = "COP(C)(=O)O"
    for lengths, within in (((1.51, 1.53), True), ((1.48, 1.57), False)):
        file, held = _built(smiles, 101), _built(smiles, 201)
        conformer = held.GetConformer()
        for oxygen, length in zip((4, 5), lengths):
            rdMolTransforms.SetBondLength(conformer, 2, oxygen, length)
        folder = tmp_path / str(lengths[0])
        folder.mkdir()
        _, record = _place(folder, file, held)
        assert bool(record["assigned_within_noise"]) is within, lengths


@pytest.mark.parametrize("pair", [(r"[H]/N=C/c1ccccc1", r"[H]/N=C\c1ccccc1"),
                                  (r"[H]/N=C(/C)c1ccccc1", r"[H]/N=C(\C)c1ccccc1")],
                         ids=["aldimine", "ketimine"])
def test_a_2d_file_s_imine_keeps_the_side_it_is_drawn_on(tmp_path: Path, pair: tuple) -> None:
    """Each of E and Z drawn in 2D: built at ideal geometry, the =N-H was
    laid trans to its reference atom whatever the depiction drew, so one of
    the two came out as the other isomer."""
    for smiles in pair:
        _drawn_side_kept(tmp_path / ("E" if smiles is pair[0] else "Z"), smiles)


def _drawn_side_kept(tmp_path: Path, smiles: str) -> None:
    from rdkit.Geometry import Point3D

    tmp_path.mkdir()
    drawn = Chem.AddHs(Chem.MolFromSmiles(smiles))
    AllChem.Compute2DCoords(drawn)
    drawn = Chem.MolFromMolBlock(Chem.MolToMolBlock(drawn), removeHs=False)
    want = Chem.MolToSmiles(drawn)
    for seed in range(1, 4):
        folder = tmp_path / str(seed)
        folder.mkdir()
        xyz, _ = _place(folder, drawn, _built(smiles, 200 + seed), seed=seed)
        placed = Chem.Mol(drawn)
        conformer = placed.GetConformer()
        for i, p in enumerate(np.asarray(xyz) * 10.0):
            conformer.SetAtomPosition(i, Point3D(*(float(v) for v in p)))
        conformer.Set3D(True)
        Chem.AssignStereochemistryFrom3D(placed)
        assert Chem.MolToSmiles(placed) == want, seed
