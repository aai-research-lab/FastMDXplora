"""The molecule placed is the file's molecule, its handedness the file's.

Round 7 refused another order only on a bond to a terminal atom at a centre
the deposit modelled flat, so DHT's file was placed on testosterone's
deposits (C4=C5 1.35 A, C5 flat), and on 1I37, whose ring A is
testosterone's, said only "the structure models C6 flat". At a centre the
deposit models flat or holds within noise the hydrogen took the side the
deposit's lean gave, so DHT on 1I37 was built as 5-beta-DHT and the cis diol
on a trans deposit as trans, said "placed as stated". The copy's noise was
read from the bonds that decide and against the file's own lengths, so a
wrong glyceraldehyde was placed on 5TG4 and a shikimate epimer on 3T39 from
ETKDG files. A centre held within noise was "verified". And a flat file's
hydrogens were measured against the construction that built them.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("rdkit")
pytest.importorskip("mdtraj")

from rdkit import Chem  # noqa: E402
from rdkit.Chem import AllChem  # noqa: E402
from rdkit.Geometry import Point3D  # noqa: E402

from fastmdxplora.setup.ligand import LigandError, pose_from_structure  # noqa: E402
from fastmdxplora.validation.ligand_mapping import (  # noqa: E402
    check_ligand_mappings,
    molecule_from_file,
)
from tests.test_a_ligand_is_placed_from_rough_and_noisy_inputs import (  # noqa: E402
    _built,
    _deposit,
    _order,
)
from tests.test_a_ligand_s_bond_orders_rank_its_matches_first import (  # noqa: E402
    _epimers,
    _ideal,
)
from tests.test_what_is_said_of_a_placed_ligand_is_what_the_structure_shows import (  # noqa: E402
    PLACED_AS_STATED,
    _study,
)

REAL = Path(__file__).parent / "data" / "ligands_real"


def _file(code: str) -> Chem.Mol:
    return Chem.MolFromMolFile(str(REAL / f"{code}_ideal.sdf"), removeHs=False)


def _refusal(tmp_path: Path, mol: Chem.Mol, deposit: Path, code: str) -> LigandError:
    sdf = tmp_path / "file.sdf"
    sdf.write_text(Chem.MolToMolBlock(mol), encoding="utf-8")
    with pytest.raises(LigandError) as refused:
        pose_from_structure(molecule_from_file(sdf), deposit, code, required=True)
    return refused.value


@pytest.mark.parametrize("file_code, deposit, code, said", [
    ("DHT", "1i37_DHT.pdb", "DHT", "C4-C5 is 1.31 A, a C=C, where the file has C-C"),
    ("DHT", "2am9_TES.pdb", "TES", "C4-C5 is 1.37 A, a C=C, where the file has C-C"),
    ("DHT", "1j96_TES.pdb", "TES", "C4-C5 is 1.35 A, a C=C, where the file has C-C"),
    ("DHT", "2q7k_TES.pdb", "TES", "C4-C5 is 1.34 A, a C=C, where the file has C-C"),
    ("DHT", "1afs_TES.pdb", "TES", "C4-C5 is 1.36 A, a C=C, where the file has C-C"),
    ("ASD", "2q7k_TES.pdb", "TES", "C17-O17 is 1.43 A, a C-O, where the file has C=O"),
    ("ASD", "1afs_TES.pdb", "TES", "C17-O17 is 1.43 A, a C-O, where the file has C=O"),
    ("TES", "1t7r_DHT.pdb", "DHT", "C4-C5 is 1.55 A, a C-C, where the file has C=C"),
    ("TES", "2ama_DHT.pdb", "DHT", "C4-C5 is 1.57 A, a C-C, where the file has C=C")],
    ids=["DHT on 1I37", "DHT on 2AM9", "DHT on 1J96", "DHT on 2Q7K", "DHT on 1AFS",
         "androstenedione on 2Q7K", "androstenedione on 1AFS", "testosterone on 1T7R",
         "testosterone on 2AMA"])
def test_a_steroid_s_file_on_another_steroid_s_deposit_is_refused(
        tmp_path: Path, file_code: str, deposit: str, code: str, said: str) -> None:
    """The same heavy graph, a bond the deposit models another order, its
    carbon shaped for it: DHT's file on testosterone's deposits was placed,
    said "the structure models C6 flat", and on 1I37, labelled DHT but
    modelled with testosterone's ring A, as 5-beta-DHT; androstenedione's
    on testosterone's and testosterone's on DHT's were verified."""
    refused = _refusal(tmp_path, _file(file_code), REAL / deposit, code)
    assert refused.code == "setup.ligand.atoms_unmatched"
    assert said in refused.args[0]


def test_each_steroid_on_its_own_deposit_is_placed(tmp_path: Path) -> None:
    """A guard, claiming no defect: each file on its own molecule's
    deposits, placed as decided."""
    for code, deposits in (("TES", ["2am9_TES.pdb", "1j96_TES.pdb", "2q7k_TES.pdb",
                                    "1afs_TES.pdb"]),
                           ("DHT", ["1t7r_DHT.pdb", "2ama_DHT.pdb"]),
                           ("ASD", ["1qyx_ASD.pdb"])):
        for deposit in deposits:
            sdf = tmp_path / "file.sdf"
            sdf.write_text(Chem.MolToMolBlock(_file(code)), encoding="utf-8")
            record: dict = {}
            pose_from_structure(molecule_from_file(sdf), REAL / deposit, code, required=True,
                                record=record)
            assert record["bond_orders_undecided"] == [], deposit


@pytest.mark.parametrize("alkane, alkene", [
    ("C[C@@H]1CCCC(=O)C1", "CC1=CC(=O)CCC1"),
    ("C[C@@H]1CC[C@@H](C(C)=C)CC1=O", "CC1=CC[C@@H](C(C)=C)CC1=O"),
    ("C[C@@H](CC)c1ccccc1", "C/C=C(/C)c1ccccc1")],
    ids=["3-methylcyclohexanone on its enone", "dihydrocarvone on carvone",
         "2-phenylbutane on 2-phenylbut-2-ene"])
def test_an_alkane_s_file_on_its_alkene_s_deposit_is_refused(
        tmp_path: Path, alkane: str, alkene: str) -> None:
    """An ene-reductase's product's file on its substrate's deposit: placed,
    the stated centre on the C=C said modelled flat."""
    for seed in (1, 2, 3):
        held = _built(alkene, 200 + seed)
        structure = _deposit(tmp_path / f"{seed}.pdb", held, _order(held, seed), 0.0, seed)
        refused = _refusal(tmp_path, _built(alkane, 100 + seed), structure, "LIG")
        assert refused.code == "setup.ligand.atoms_unmatched", seed
        assert ", a C=C, where the file has C-C" in refused.args[0], seed


def _isomer(mol: Chem.Mol, xyz_nm: np.ndarray, relaxed: bool = False) -> str:
    """The isomeric SMILES of ``mol`` read from coordinates, hydrogens and
    all; ``relaxed``, after the placed ligand alone is minimised with MMFF,
    as a simulation's first steps would."""
    placed = Chem.Mol(mol)
    conformer = placed.GetConformer()
    for i, p in enumerate(np.asarray(xyz_nm) * 10.0):
        conformer.SetAtomPosition(i, Point3D(*(float(v) for v in p)))
    conformer.Set3D(True)
    if relaxed:
        AllChem.MMFFOptimizeMolecule(placed, maxIters=2000)
    Chem.AssignStereochemistryFrom3D(placed)
    return Chem.MolToSmiles(Chem.RemoveHs(placed))


def _moved(mol: Chem.Mol, atom: int, to: np.ndarray) -> Chem.Mol:
    mol = Chem.Mol(mol)
    mol.GetConformer().SetAtomPosition(atom, Point3D(*(float(v) for v in to)))
    return mol


def _placed(tmp_path: Path, file: Chem.Mol, held: Chem.Mol, seed: int) -> np.ndarray:
    sdf = tmp_path / f"file{seed}.sdf"
    sdf.write_text(Chem.MolToMolBlock(file), encoding="utf-8")
    structure = _deposit(tmp_path / f"{seed}.pdb", held, _order(held, seed), 0.0, seed)
    placed, _ = pose_from_structure(molecule_from_file(sdf), structure, "LIG", required=True)
    return placed.conformers[0].m_as("nanometer")


def test_the_cis_diol_on_a_flattened_trans_deposit_is_built_cis(tmp_path: Path) -> None:
    """One carbinol carbon of a trans deposit laid in its neighbours' plane:
    its hydrogen took the side the deposit leaned to, and the cis file was
    built trans in 8 of 9, said "placed as stated"."""
    cis, trans = "O[C@@H]1CCCC[C@@H]1O", "O[C@@H]1CCCC[C@H]1O"
    want = Chem.MolToSmiles(Chem.MolFromSmiles(cis))
    for seed in (1, 2, 3):
        held = _built(trans, 200 + seed)
        xyz = held.GetConformer().GetPositions()
        centre = 1
        a, b, c = (xyz[n.GetIdx()] for n in held.GetAtomWithIdx(centre).GetNeighbors()
                   if n.GetAtomicNum() > 1)
        normal = np.cross(b - a, c - a)
        normal /= np.linalg.norm(normal)
        held = _moved(held, centre, xyz[centre] - np.dot(xyz[centre] - a, normal) * normal)
        file = _built(cis, 100 + seed)
        xyz = _placed(tmp_path, file, held, seed)
        assert _isomer(file, xyz) == want, seed
        assert _isomer(file, xyz, relaxed=True) == want, seed


@pytest.mark.parametrize("volume", [0.15, 0.22])
def test_a_centre_leaning_the_other_way_within_noise_is_built_as_the_file(
        tmp_path: Path, volume: float) -> None:
    """1-cyclohexylethanol's carbinol leaning the other way, its bonds'
    volume 0.15 or 0.22 (a tetrahedral one's 0.77): the hydrogen followed
    the lean, the other enantiomer was built and relaxed to, and the check
    said verified. Its hydrogen is laid on the file's side, and the ligand
    relaxes to the file's isomer."""
    from fastmdxplora.setup.ligand import _volume

    smiles = "C[C@@H](O)C1CCCCC1"
    want = Chem.MolToSmiles(Chem.MolFromSmiles(smiles))
    for seed in (1, 2):
        held = _built(smiles, 200 + seed)
        xyz = held.GetConformer().GetPositions()
        centre = 1
        around = tuple(n.GetIdx() for n in held.GetAtomWithIdx(centre).GetNeighbors()
                       if n.GetAtomicNum() > 1)
        middle = xyz[list(around)].mean(axis=0)
        start = xyz[centre].copy()
        sign = np.sign(_volume(xyz, centre, around))
        low, high = -1.0, 0.0
        for _ in range(50):
            t = (low + high) / 2
            xyz[centre] = middle + t * (start - middle)
            if np.sign(_volume(xyz, centre, around)) == sign or abs(
                    _volume(xyz, centre, around)) < volume:
                high = t
            else:
                low = t
        held = _moved(held, centre, xyz[centre])
        assert abs(_volume(held.GetConformer().GetPositions(), centre, around) - (-sign * volume)) \
            < 0.02, seed
        file = _built(smiles, 100 + seed)
        assert _isomer(file, _placed(tmp_path, file, held, seed), relaxed=True) == want, seed


@pytest.mark.parametrize("make", ["mmff", "etkdg"])
def test_a_stretched_copy_still_decides_its_aldehyde(tmp_path: Path, make: str) -> None:
    """5TG4's copy A303, every bond 0.05 to 0.11 A long, its C=O 1.28 A and
    C-OH 1.53: read as 0.13 A of noise from the very bonds that decide, the
    wrong glyceraldehyde was placed as undecided. The stretch is not its
    noise: 3GR is placed decided and GXV refused."""
    for code, right in (("3GR", True), ("GXV", False)):
        mol = Chem.Mol(_ideal(code))
        parameters = AllChem.ETKDGv3()
        parameters.randomSeed = 11
        assert AllChem.EmbedMolecule(mol, parameters) == 0
        if make == "mmff":
            AllChem.MMFFOptimizeMolecule(mol, maxIters=2000)
        sdf = tmp_path / f"{code}.sdf"
        sdf.write_text(Chem.MolToMolBlock(mol), encoding="utf-8")
        record: dict = {}
        if right:
            pose_from_structure(molecule_from_file(sdf), REAL / "5tg4_3GR.pdb", "3GR",
                                required=True, record=record)
            assert record["bond_orders_undecided"] == []
        else:
            with pytest.raises(LigandError) as refused:
                pose_from_structure(molecule_from_file(sdf), REAL / "5tg4_3GR.pdb", "3GR",
                                    required=True)
            assert refused.value.code == "setup.ligand.stereo_mismatch"


def test_the_c8_epimer_on_3t39_is_refused_from_every_kind_of_file(tmp_path: Path) -> None:
    """3T39's copy D, its C4=C10 1.34 A and C4-C5 1.51: the copy's noise was
    read against the file's own lengths, so an ETKDG or UFF file of the C8
    epimer read it noisier and was placed as undecided."""
    epimer = next(s for s in _epimers(_ideal("SKM"))
                  if Chem.MolToSmiles(Chem.MolFromSmiles(s)) != Chem.MolToSmiles(
                      Chem.RemoveHs(_ideal("SKM"))))
    base = Chem.AddHs(Chem.MolFromSmiles(epimer))
    for seed in (11, 12, 13):
        for make in ("etkdg", "uff", "mmff", "2d"):
            mol = Chem.Mol(base)
            if make == "2d":
                AllChem.Compute2DCoords(mol)
                Chem.WedgeMolBonds(mol, mol.GetConformer())
            else:
                parameters = AllChem.ETKDGv3()
                parameters.randomSeed = seed
                assert AllChem.EmbedMolecule(mol, parameters) == 0
                if make == "uff":
                    AllChem.UFFOptimizeMolecule(mol, maxIters=2000)
                elif make == "mmff":
                    AllChem.MMFFOptimizeMolecule(mol, maxIters=2000)
            sdf = tmp_path / "epimer.sdf"
            sdf.write_text(Chem.MolToMolBlock(mol), encoding="utf-8")
            try:
                record: dict = {}
                pose_from_structure(molecule_from_file(sdf), REAL / "3t39_SKM_D.pdb", "SKM",
                                    required=True, record=record)
            except LigandError as refused:
                assert refused.code == "setup.ligand.stereo_mismatch"
                continue
            raise AssertionError((seed, make, record.get("bond_orders_undecided")))


@pytest.mark.parametrize("flip", [False, True], ids=["G3H", "its C2 epimer"])
def test_a_centre_held_within_noise_is_placed_as_stated(tmp_path: Path, flip: bool) -> None:
    """1M7P's G3H, its C2 bonds' volume between flat and judged: both the
    file and its epimer were "verified ... 0 of them agree with the pose".
    Each is "placed as stated; C2 held within noise"."""
    mol = Chem.Mol(_file("G3H"))
    if flip:
        Chem.AssignStereochemistryFrom3D(mol)
        centre = next(i for i, _ in Chem.FindMolChiralCenters(mol, useLegacyImplementation=False))
        heavy = Chem.RemoveHs(mol)
        heavy.GetAtomWithIdx(centre).InvertChirality()
        mol = _built(Chem.MolToSmiles(heavy), 7)
    study, placement = _study(tmp_path, mol, "1m7p_G3H.pdb", "G3H")
    assert "C2 held within noise" in placement["returned"]
    [found] = check_ligand_mappings(study)
    assert found["status"] == PLACED_AS_STATED
    assert "C2 held within noise" in found["said"]


def test_a_flat_file_s_hydrogens_read_their_departure_from_ideal(tmp_path: Path) -> None:
    """Tetracycline from its CCD file, itself flat, on 2TRT: its C4 hydrogen
    makes 92.9 degrees with each of the bonds the deposit flattened. It read
    0.0, measured against the construction that laid it; it reads 16.5 from
    the tetrahedral 109.5."""
    sdf = REAL / "TAC_ideal.sdf"
    record: dict = {}
    pose_from_structure(molecule_from_file(sdf), REAL / "2trt_TAC.pdb", "TAC", required=True,
                        record=record)
    assert 14.0 < record["worst_hydrogen_angle_deg"] < 20.0


def test_an_undecided_copy_says_the_other_isomer_fits_and_its_noise(tmp_path: Path) -> None:
    """5TED's shikimate, its ring 0.05 to 0.16 A off any order: "placed as
    stated" did not say that its C8 epimer fits as well, nor the noise the
    copy was judged with."""
    study, placement = _study(tmp_path, _ideal("SKM"), "5ted_SKM_A.pdb", "SKM")
    # Its lengths lean to the epimer, within the copy's noise: said so.
    lean = "the lengths lean to the other isomer, within this copy's noise"
    assert lean in placement["returned"]
    assert "this copy's noise" in placement["returned"]
    [found] = check_ligand_mappings(study)
    assert found["status"] == PLACED_AS_STATED
    assert lean in found["said"]
    assert "this copy's noise" in found["said"]
    # By the structure's own atom names: the centre the deposit calls C8.
    assert "did not decide C8" in found["said"]


def test_a_decided_symmetric_ligand_does_not_say_the_other_isomer_fits(tmp_path: Path) -> None:
    """Prephenate on 1COM: its ring turned end to end is a tied match that
    turns both centres, the molecule's own symmetry. The structure decides
    them, yet setup said "agree with the pose; the other isomer fits too"."""
    study, placement = _study(tmp_path, _file("PRE"), "1com_PRE.pdb", "PRE")
    assert "agree with the pose" in placement["stereochemistry"]["said"]
    assert "other isomer" not in placement["stereochemistry"]["said"]
    assert "other isomer" not in placement["returned"]
    [found] = check_ligand_mappings(study)
    assert found["status"] == "verified"
    assert "other isomer" not in found["said"]


def test_a_copy_a_little_noisy_never_decides_the_wrong_isomer(tmp_path: Path) -> None:
    """Shikimate 0.03 A off in each atom reads 0.063 A a bond. Round 8 held
    it to a good deposit's noise so that its C8 epimer was refused; read as
    its own since round 9, the epimer may be left undecided, but is never
    placed as decided, and the right file is placed."""
    right = "O=C(O)C1=C[C@@H](O)[C@@H](O)[C@H](O)C1"
    wrong = "O=C(O)C1=C[C@@H](O)[C@H](O)[C@H](O)C1"
    held = _built(right, 602)
    deposit = _deposit(tmp_path / "noisy.pdb", held, list(range(held.GetNumAtoms())), 0.03, 102)
    for smiles, seed in ((wrong, 202), (right, 402)):
        sdf = tmp_path / f"{seed}.sdf"
        sdf.write_text(Chem.MolToMolBlock(_built(smiles, seed)), encoding="utf-8")
        record: dict = {}
        try:
            pose_from_structure(molecule_from_file(sdf), deposit, "LIG", required=True,
                                record=record)
        except LigandError:
            assert smiles == wrong
            continue
        if smiles == wrong:
            assert record["bond_orders_undecided"]


def test_a_conjugated_ring_s_lengths_are_not_read_as_noise(tmp_path: Path) -> None:
    """Porphine 0.03 A off in each atom: its bonds all lie between C-C and
    C=C, and read against those as noise (0.083 A a bond) they let the
    inner hydrogens be laid on the two nitrogens the deposit has bare."""
    smiles = "c1cc2cc3ccc(cc4ccc(cc5ccc(cc1n2)[nH]5)n4)[nH]3"
    held = _built(smiles, 1003)
    deposit = _deposit(tmp_path / "noisy.pdb", held, list(range(held.GetNumAtoms())), 0.03, 3)
    mol = _built(smiles, 2003, "etkdg")
    sdf = tmp_path / "porphine.sdf"
    sdf.write_text(Chem.MolToMolBlock(mol), encoding="utf-8")
    record: dict = {}
    pose_from_structure(molecule_from_file(sdf), deposit, "LIG", required=True, record=record)
    assert record["bond_noise_nm"] == pytest.approx(0.0025)

    def bearing(m: Chem.Mol) -> set[int]:
        return {a.GetIdx() for a in m.GetAtoms()
                if a.GetSymbol() == "N" and a.GetTotalNumHs(includeNeighbors=True)}

    laid = {m["structure_atom"] for m in record["mapping"] if m["file_atom"] in bearing(mol)}
    assert laid == bearing(held)
