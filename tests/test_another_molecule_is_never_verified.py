"""Another molecule is never verified, and no wrong isomer is held within noise.

Round 8 refused another order only where a carbon with three heavy
neighbours outside a terminal group changed its shape. So retinal's file on
retinol's deposits, a 5-ene steroid on a 4-ene's, p-menthene on limonene,
benzyl alcohol on benzaldehyde and alpha- on gamma-terpinene were all
"verified", and a bond whose length alone fitted another order was never
said. A stated centre without a hydrogen (a sulfoxide's S, a sulfonium's)
and a stated double bond, held within noise the other way, were placed as
deposited and relaxed to the other isomer, said "placed as stated". The
copy's noise fell back to a good deposit's under eight bonds. Free
nucleobases, renamed by the structure's reader, took the file's pose. And
the refusals and records named atoms by the file and spoke of "every match
of the bond graph".
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("rdkit")
pytest.importorskip("mdtraj")

from rdkit import Chem  # noqa: E402
from rdkit.Geometry import Point3D  # noqa: E402

from fastmdxplora.setup.ligand import LigandError, pose_from_structure  # noqa: E402
from fastmdxplora.validation.ligand_mapping import (  # noqa: E402
    check_ligand_mappings,
    molecule_from_file,
)
from tests.test_a_ligand_is_placed_from_rough_and_noisy_inputs import (  # noqa: E402
    _built,
    _deposit,
)
from tests.test_the_placed_molecule_is_the_file_s_molecule import (  # noqa: E402
    _file,
    _isomer,
    _refusal,
)
from tests.test_what_is_said_of_a_placed_ligand_is_what_the_structure_shows import (  # noqa: E402
    PLACED_AS_STATED,
    _study,
)

REAL = Path(__file__).parent / "data" / "ligands_real"


def _held(tmp_path: Path, smiles: str, seed: int, xyz: np.ndarray | None = None) -> Path:
    """A deposit of ``smiles``, its atoms as built (or at ``xyz``, A)."""
    mol = _built(smiles, 200 + seed)
    if xyz is not None:
        conformer = mol.GetConformer()
        for i, p in enumerate(xyz):
            conformer.SetAtomPosition(i, Point3D(*(float(v) for v in p)))
    return _deposit(tmp_path / f"held{seed}.pdb", mol, list(range(mol.GetNumAtoms())), 0.0, seed)


def _place(tmp_path: Path, mol: Chem.Mol, deposit: Path) -> tuple[np.ndarray, dict, str]:
    sdf = tmp_path / "file.sdf"
    sdf.write_text(Chem.MolToMolBlock(mol), encoding="utf-8")
    record: dict = {}
    placed, said = pose_from_structure(molecule_from_file(sdf), deposit, "LIG", required=True,
                                       record=record)
    return placed.conformers[0].m_as("nanometer"), record, said


@pytest.mark.parametrize("file, deposit", [
    ("CC1=CC[C@@H](CC1)C(C)C", "CC1=CC[C@@H](CC1)C(C)=C"),
    ("OCc1ccccc1", "O=Cc1ccccc1"),
    ("CC1=CC=C(C(C)C)CC1", "CC1=CCC(C(C)C)=CC1"),
    ("CC1=CCC(C(C)C)=CC1", "CC1=CC=C(C(C)C)CC1")],
    ids=["p-menthene on limonene", "benzyl alcohol on benzaldehyde",
         "alpha- on gamma-terpinene", "gamma- on alpha-terpinene"])
def test_another_molecule_s_file_is_refused(tmp_path: Path, file: str, deposit: str) -> None:
    """Each was verified: an isopropyl's carbon in a terminal group, a CH2OH
    with two heavy neighbours, and a double bond moved between carbons each
    sp2 in both molecules escaped the shape test."""
    for seed in (1, 2):
        refused = _refusal(tmp_path, _built(file, 100 + seed), _held(tmp_path, deposit, seed),
                           "LIG")
        assert refused.code == "setup.ligand.atoms_unmatched", seed
        said = refused.args[0]
        assert "deposited as a molecule other than the supplied file's" in said, seed
        assert "every match of the bond graph" not in said and "testosterone" not in said, seed


def test_retinal_s_file_on_retinol_s_deposit_is_not_verified(tmp_path: Path) -> None:
    """1KT6's retinol (1.10 A): C15-O1 1.43 A, a C-OH, where retinal's file
    has C15=O. It was verified."""
    with pytest.raises(LigandError) as refused:
        _study(tmp_path, _file("RET"), "1kt6_RTL.pdb", "RTL")
    assert "Its C15-O1 is 1.43 A, a C-O, where the file has C=O" in refused.value.args[0]


def test_a_5_ene_steroid_on_androstenedione_s_deposit_is_refused(tmp_path: Path) -> None:
    """Androst-5-ene-3,17-dione's file on 3S79's androst-4-ene-3,17-dione:
    C5-C6 a C-C where the file has C=C. It was verified, "5 stereocentres
    ... agree with the pose"."""
    mol = _built("C[C@]12CC[C@H]3[C@@H](CC=C4CC(=O)CC[C@@]43C)[C@@H]1CCC2=O", 5)
    with pytest.raises(LigandError) as refused:
        _study(tmp_path, mol, "3s79_ASD.pdb", "ASD")
    assert refused.value.code == "setup.ligand.atoms_unmatched"
    assert "C6-C5 is 1.51 A, a C-C, where the file has C=C" in refused.value.args[0]


def test_dhf_s_file_on_folate_s_deposit_is_not_verified(tmp_path: Path) -> None:
    """4PSS's folate (1.0 A): C6-C7 1.38 and C7-N8 1.34 A, the pterin's,
    where DHF's file has C7H2. It was verified; it is placed as stated, the
    bond said."""
    study, placement = _study(tmp_path, _file("DHF"), "4pss_FOL.pdb", "FOL")
    assert "C6-C7 1.38 A fits C=C, the file has C-C" in placement["returned"]
    [found] = check_ligand_mappings(study)
    assert found["status"] == PLACED_AS_STATED
    assert "C6-C7 1.38 A fits C=C, the file has C-C" in found["said"]


def test_folate_s_file_on_dhf_s_deposit_says_the_other_form(tmp_path: Path) -> None:
    """6CXK's DHF: C7-N8 1.46 A, single, where folate's file has the
    pterin aromatic. Placed, as the decision has it, but no longer verified."""
    study, placement = _study(tmp_path, _file("FOL"), "6cxk_DHF.pdb", "DHF")
    note = "C7-N8 1.46 A fits C-N, the file has it aromatic"
    assert note in placement["returned"]
    [found] = check_ligand_mappings(study)
    assert found["status"] == PLACED_AS_STATED
    assert note in found["said"]
    assert (placement.get("bonds_fitting_another_order") or [{}])[0].get("fits_order") == 1.0


@pytest.mark.parametrize("file, deposit, said", [
    ("Oc1ccccn1", "O=c1cccc[nH]1", "fits C=O, the file has C-OH"),
    ("OC1=CCCCC1", "O=C1CCCCC1", "fits C=O, the file has C-OH")],
    ids=["2-hydroxypyridine on 2-pyridone", "the enol on cyclohexanone"])
def test_a_tautomer_is_placed_and_said(tmp_path: Path, file: str, deposit: str, said: str) -> None:
    """Placed, as a deposit's C=O mostly shows the restraint dictionary's
    tautomer, but said, where it was placed without a word."""
    _, record, line = _place(tmp_path, _built(file, 101), _held(tmp_path, deposit, 1))
    assert said in line
    assert record.get("bonds_fitting_another_order")


@pytest.mark.parametrize("code, deposit", [("PP9", "2hre_PP9.pdb"), ("RIT", "1hxw_RIT.pdb")])
def test_a_length_no_shape_confirms_is_placed_as_stated(tmp_path: Path, code: str,
                                                       deposit: str) -> None:
    """2HRE's vinyls modelled 1.52 A long, a ritonavir N-methyl 1.33 A
    short: no carbon's shape says another molecule, so each is placed, said
    as a length, not verified."""
    study, placement = _study(tmp_path, _file(code), deposit, code)
    assert placement.get("bonds_fitting_another_order")
    [found] = check_ligand_mappings(study)
    assert found["status"] == PLACED_AS_STATED
    assert " fits " in found["said"]


def _leaned(smiles: str, seed: int, centre_element: str, end_element: str,
            target: float) -> tuple[np.ndarray, int]:
    """``smiles`` built, its stated centre (of ``centre_element``) made to
    lean the other way, its unit-vector volume ``target`` against the
    file's, by turning its terminal neighbour (of ``end_element``) about it,
    the bond kept."""
    from fastmdxplora.setup.ligand import _volume

    held = _built(smiles, 200 + seed)
    xyz = held.GetConformer().GetPositions()
    centre = next(a.GetIdx() for a in held.GetAtoms() if a.GetSymbol() == centre_element
                  and a.GetChiralTag() != Chem.ChiralType.CHI_UNSPECIFIED)
    around = sorted(n.GetIdx() for n in held.GetAtomWithIdx(centre).GetNeighbors()
                    if n.GetAtomicNum() > 1)
    end = next(n for n in around if held.GetAtomWithIdx(n).GetSymbol() == end_element
               and sum(1 for m in held.GetAtomWithIdx(n).GetNeighbors() if m.GetAtomicNum() > 1)
               == 1)
    ys = [n for n in around if n != end]
    sign = np.sign(_volume(xyz, centre, tuple(around)))
    radius = np.linalg.norm(xyz[end] - xyz[centre])
    a, b = (xyz[y] - xyz[centre] for y in ys)
    normal = np.cross(a, b)
    normal /= np.linalg.norm(normal)
    apart = -(a / np.linalg.norm(a) + b / np.linalg.norm(b))
    apart -= np.dot(apart, normal) * normal
    apart /= np.linalg.norm(apart)
    best = None
    for t in np.linspace(-1.2, 1.2, 2401):
        xyz[end] = xyz[centre] + radius * (np.cos(t) * apart + np.sin(t) * normal)
        off = abs(_volume(xyz, centre, tuple(around)) + sign * target)
        if best is None or off < best[0]:
            best = (off, t)
    xyz[end] = xyz[centre] + radius * (np.cos(best[1]) * apart + np.sin(best[1]) * normal)
    return xyz, end


@pytest.mark.parametrize("smiles, centre, end, target", [
    ("CC[S@](=O)C[C@H](N)C(=O)O", "S", "O", 0.22),
    ("C[S@@+](CC[C@H](N)C(=O)O)C[C@H]1O[C@@H](n2cnc3c(N)ncnc32)[C@H](O)[C@@H]1O", "S", "C", 0.15),
    ("C[S@@+](CC[C@H](N)C(=O)O)C[C@H]1O[C@@H](n2cnc3c(N)ncnc32)[C@H](O)[C@@H]1O", "S", "C", 0.22)],
    ids=["sulfoxide at -0.22", "SAM's sulfonium at -0.15", "SAM's sulfonium at -0.22"])
def test_a_centre_without_a_hydrogen_held_the_other_way_is_laid_the_file_s(
        tmp_path: Path, smiles: str, centre: str, end: str, target: float) -> None:
    """Kept as deposited, the ligand relaxed to the other isomer, said
    "placed as stated". Its terminal atom is laid on the file's side, said,
    and the ligand relaxes to the file's isomer."""
    want = Chem.MolToSmiles(Chem.MolFromSmiles(smiles))
    for seed in (1, 2):
        xyz, _ = _leaned(smiles, seed, centre, end, target)
        file = _built(smiles, 100 + seed)
        placed, record, line = _place(tmp_path, file, _held(tmp_path, smiles, seed, xyz))
        assert record.get("reflected_to_the_file_s_side"), seed
        assert "laid on the file's side of" in line, seed
        assert _isomer(file, placed, relaxed=True) == want, seed


def _turned(smiles: str, seed: int, degrees: float) -> np.ndarray:
    """``smiles`` built, the far side of its stated double bond turned."""
    held = _built(smiles, 200 + seed)
    xyz = held.GetConformer().GetPositions()
    bond = next(b for b in held.GetBonds() if b.GetBondType() == Chem.BondType.DOUBLE
                and b.GetStereo() != Chem.BondStereo.STEREONONE)
    i, j = bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()
    side, todo = {j}, [j]
    while todo:
        x = todo.pop()
        for n in held.GetAtomWithIdx(x).GetNeighbors():
            if n.GetIdx() not in side and n.GetIdx() != i:
                side.add(n.GetIdx())
                todo.append(n.GetIdx())
    axis = (xyz[j] - xyz[i]) / np.linalg.norm(xyz[j] - xyz[i])
    t = np.radians(degrees)
    k = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
    rotation = np.eye(3) + np.sin(t) * k + (1 - np.cos(t)) * k @ k
    for n in side - {j}:
        xyz[n] = xyz[j] + rotation @ (xyz[n] - xyz[j])
    return xyz


@pytest.mark.parametrize("smiles", ["OC/C=C/c1ccccc1", "C/C(=C\\c1ccccc1)C(=O)O"],
                         ids=["cinnamyl alcohol", "alpha-methylcinnamic acid"])
def test_a_double_bond_held_within_noise_the_other_way_is_refused(
        tmp_path: Path, smiles: str) -> None:
    """Turned 100 or 115 degrees, under 90 from Z: placed, relaxed Z, said
    "placed as stated"."""
    for degrees in (100, 115):
        refused = _refusal(tmp_path, _built(smiles, 101), _held(
            tmp_path, smiles, 1, _turned(smiles, 1, degrees)), "LIG")
        assert refused.code == "setup.ligand.stereo_mismatch", degrees
        assert "within noise on the other side" in refused.args[0], degrees


def test_a_double_bond_held_within_noise_is_named_once(tmp_path: Path) -> None:
    """Turned 60 degrees, on the file's side of square: said as a bond, not
    as two centres ("C3 held within noise; C5 held within noise")."""
    smiles = "OC/C=C/c1ccccc1"
    _, record, line = _place(tmp_path, _built(smiles, 101),
                             _held(tmp_path, smiles, 1, _turned(smiles, 1, 60)))
    names = {m["file_atom"]: m["structure_name"] for m in record["mapping"]}
    assert f"{names[2]}={names[3]} held within noise" in line or \
        f"{names[3]}={names[2]} held within noise" in line


def test_the_refusal_of_1i37_says_what_the_deposit_shows(tmp_path: Path) -> None:
    """1I37 is labelled DHT and modelled as testosterone's ring A. It read
    "DHT ... is another molecule than the supplied file's", gave a steroid
    example for every case, spoke of "every match of the bond graph" and
    advised supplying the file of the molecule the structure holds."""
    refused = _refusal(tmp_path, _file("DHT"), REAL / "1i37_DHT.pdb", "DHT")
    said = refused.args[0]
    assert "DHT in 1i37_DHT.pdb is deposited as a molecule other than the supplied file's" in said
    assert "Its C4-C5 is 1.31 A, a C=C, where the file has C-C, and its C5 is flat" in said
    assert "validation report" in said
    for gone in ("every match of the bond graph", "testosterone's C4=C5",
                 "Supply the file of the molecule the structure holds"):
        assert gone not in said


def test_the_record_s_lists_name_the_structure_s_atoms(tmp_path: Path) -> None:
    """1M7P's G3H: the record's not_judged said ["C3"], the file's name,
    beside the line's "C2 held within noise"."""
    _, placement = _study(tmp_path, _file("G3H"), "1m7p_G3H.pdb", "G3H")
    assert placement["stereochemistry"]["not_judged"] == ["C2"]


@pytest.mark.parametrize("code, deposit", [("ADE", "2qes_ADE.pdb"), ("URA", "4wpl_URA.pdb")])
def test_a_free_nucleobase_s_pose_comes_from_the_structure(tmp_path: Path, code: str,
                                                          deposit: str) -> None:
    """Read as A and U, a free adenine and uracil were not found under their
    deposited names, and the file's arbitrary pose was kept without a word."""
    _, placement = _study(tmp_path, _file(code), deposit, code)
    assert placement["pose"] == "structure"


def test_a_small_rough_copy_reads_its_own_noise(tmp_path: Path) -> None:
    """Glyceraldehyde 0.08 A off in each atom: under eight bonds it was held
    to a good deposit's noise, 0.025 A, however rough, and its wrong isomer
    placed as decided on such copies. It reads its own, from four bonds."""
    right = "O=C[C@H](O)CO"
    read = []
    for seed in (2, 3):
        held = _built(right, 500 + seed)
        deposit = _deposit(tmp_path / f"rough{seed}.pdb", held,
                           list(range(held.GetNumAtoms())), 0.08, seed)
        try:
            _, record, _ = _place(tmp_path, _built(right, 300 + seed), deposit)
        except LigandError:
            continue
        read.append(record["bond_noise_nm"])
    assert read and all(noise > 0.0025 for noise in read)


def test_the_check_holds_the_laid_atom_to_its_side(tmp_path: Path) -> None:
    """The sulfoxide at -0.22: the check read "placed as stated; S held
    within noise" of a ligand that relaxed to the other isomer. It holds the
    oxygen setup laid to the file's side to that side, and says so."""
    smiles = "CC[S@](=O)C[C@H](N)C(=O)O"
    xyz, _ = _leaned(smiles, 1, "S", "O", 0.22)
    deposit = _held(tmp_path, smiles, 1, xyz)
    study, placement = _study(tmp_path, _built(smiles, 101), str(deposit), "LIG")
    [found] = check_ligand_mappings(study)
    assert found["status"] == PLACED_AS_STATED
    assert "laid on the file's side of" in found["said"]


def test_a_short_c_o_is_never_read_as_a_triple_bond(tmp_path: Path) -> None:
    """5XU9's erythrose phosphate, its aldehyde C=O deposited 1.13 A: read
    as fitting C#O, as if carbon monoxide."""
    smiles = "O=CCCC"
    held = _built(smiles, 201)
    xyz = held.GetConformer().GetPositions()
    carbon = next(a.GetIdx() for a in held.GetAtoms() if a.GetSymbol() == "C"
                  and any(n.GetSymbol() == "O" for n in a.GetNeighbors()))
    oxygen = next(a.GetIdx() for a in held.GetAtoms() if a.GetSymbol() == "O")
    axis = (xyz[oxygen] - xyz[carbon]) / np.linalg.norm(xyz[oxygen] - xyz[carbon])
    xyz[oxygen] = xyz[carbon] + 1.13 * axis
    _, record, line = _place(tmp_path, _built(smiles, 101), _held(tmp_path, smiles, 1, xyz))
    assert "C#O" not in line
    assert all(m["fits_order"] != 3.0 for m in record.get("bonds_fitting_another_order", []))


def test_a_porphine_read_from_its_conjugated_bonds_keeps_its_nh_pair(tmp_path: Path) -> None:
    """Porphine 0.03 A off in each atom, every bond conjugated: read from
    four of them it reads 0.07 A, and with that noise its inner hydrogens
    were laid on the two nitrogens the deposit has bare."""
    smiles = "c1cc2cc3ccc(cc4ccc(cc5ccc(cc1n2)[nH]5)n4)[nH]3"
    held = _built(smiles, 1017)
    deposit = _deposit(tmp_path / "noisy.pdb", held, list(range(held.GetNumAtoms())), 0.03, 17)
    mol = _built(smiles, 2017, "etkdg")
    _, record, _ = _place(tmp_path, mol, deposit)

    def bearing(m: Chem.Mol) -> set[int]:
        return {a.GetIdx() for a in m.GetAtoms()
                if a.GetSymbol() == "N" and a.GetTotalNumHs(includeNeighbors=True)}

    laid = {m["structure_atom"] for m in record["mapping"] if m["file_atom"] in bearing(mol)}
    assert laid == bearing(held)


def test_a_c_n_with_a_double_bond_s_length_is_placed_and_said(tmp_path: Path) -> None:
    """3HEC's imatinib, its C46-N48 modelled 1.27 A beside a flat N: another
    protonation or tautomeric form of the file's molecule, not another
    molecule. It was refused."""
    study, placement = _study(tmp_path, _file("STI"), "3hec_STI.pdb", "STI")
    note = ("C46-N48 1.27 A fits C=N, the file has C-N (another protonation, "
            "tautomeric or redox form)")
    assert note in placement["returned"]
    [found] = check_ligand_mappings(study)
    assert found["status"] == PLACED_AS_STATED
    assert note in found["said"]


@pytest.mark.parametrize("code, deposit, said", [
    ("DQA", "3nnt_DQA.pdb", "Its C5-C6 is 1.34 A, a C=C, where the file has C-C, and its C6 is flat"),
    ("ISC", "5jxz_ISC.pdb",
     "Its C4-C5 is 1.52 A, a C-C, where the file has C=C, and its C4 is tetrahedral")],
    ids=["3NNT's dehydroquinate", "5JXZ's isochorismate"])
def test_a_deposit_modelled_as_another_molecule_is_refused_with_a_way_on(
        tmp_path: Path, code: str, deposit: str, said: str) -> None:
    """Each deposit's geometry is another molecule's, named by its bond.
    The refusal told a user who has checked the deposit and still wants it
    only to "supply one", without the setting's name: it says how to go on,
    by a setting that does, `setup.ligand_pose: file` placing the ligand from
    the file's coordinates, which it says must be in the binding site."""
    from fastmdxplora.setup.ligand import POSE_POLICIES, pose_by_policy

    with pytest.raises(LigandError) as refused:
        _study(tmp_path, _file(code), deposit, code)
    message = refused.value.args[0]
    assert refused.value.code == "setup.ligand.atoms_unmatched"
    assert said in message
    assert "another deposit of the file's molecule" in message
    assert "To go on" in message and "ligand_pose: file (setup.ligand_pose)" in message
    assert "must lie in the binding site" in message and "file" in POSE_POLICIES
    assert "applies to every ligand of the study" in message
    sdf = tmp_path / f"{code}.sdf"
    sdf.write_text(Chem.MolToMolBlock(_file(code)), encoding="utf-8")
    record: dict = {}
    placed, line = pose_by_policy(molecule_from_file(sdf), REAL / deposit, code,
                                  policy="file", record=record)
    assert record["pose"] == "file"
    assert placed.conformers and "ligand_pose: file" in line


def _never_verified(tmp_path: Path, file: Chem.Mol, deposit: Path) -> bool:
    """Whether another molecule's file on ``deposit`` is refused, or placed
    with a bond said: never "verified"."""
    try:
        _, record, _ = _place(tmp_path, file, deposit)
    except LigandError as refused:
        return refused.code == "setup.ligand.atoms_unmatched"
    return bool(record.get("bonds_fitting_another_order"))


def test_1_hexene_s_file_on_hexane_is_never_verified(tmp_path: Path) -> None:
    """Hexane's two ends tie, so the C=C laid on either was left out as laid
    differently; and where one end's CH2 angle was 113.5 degrees, just over
    the old 113 degree limit, nothing was said at all."""
    for seed in range(1, 7):
        assert _never_verified(tmp_path, _built("C=CCCCC", 100 + seed),
                               _held(tmp_path, "CCCCCC", seed)), seed


def test_alpha_terpinene_on_a_good_deposit_s_noise_is_never_verified(tmp_path: Path) -> None:
    """Gamma-terpinene 0.02 A off in each coordinate, a good deposit's
    noise: the moved C=C's own bonds raised the copy's noise as read (0.064
    A where the right file read 0.025), which then hid them."""
    for seed in range(1, 5):
        held = _built("CC1=CCC(C(C)C)=CC1", 200 + seed)
        noisy = _deposit(tmp_path / f"noisy{seed}.pdb", held, list(range(held.GetNumAtoms())),
                         0.02, seed)
        assert _never_verified(tmp_path, _built("CC1=CC=C(C(C)C)CC1", 100 + seed), noisy), seed


def test_a_length_that_leans_at_a_carbon_of_the_other_shape_is_said(tmp_path: Path) -> None:
    """Benzaldehyde's file on a benzyl alcohol whose C-O noise has left at
    1.33 A, past the midpoint by less than the noise, at a carbon of 110
    degrees: said nowhere, and verified."""
    held = _built("OCc1ccccc1", 201)
    xyz = held.GetConformer().GetPositions()
    oxygen = next(a.GetIdx() for a in held.GetAtoms() if a.GetSymbol() == "O")
    carbon = next(n.GetIdx() for n in held.GetAtomWithIdx(oxygen).GetNeighbors()
                  if n.GetSymbol() == "C")
    axis = (xyz[oxygen] - xyz[carbon]) / np.linalg.norm(xyz[oxygen] - xyz[carbon])
    xyz[oxygen] = xyz[carbon] + 1.33 * axis
    _, record, line = _place(tmp_path, _built("O=Cc1ccccc1", 101),
                             _held(tmp_path, "OCc1ccccc1", 1, xyz))
    assert record.get("bonds_fitting_another_order")
    assert "1.33 A leans to C-O, with its" in line and "tetrahedral as in C-O" in line


@pytest.mark.parametrize("file, deposit", [
    ("C1CC=NC1", "C1CCNC1"),
    ("C(c1ccccc1)C1=NCCN1", "C(c1ccccc1)C1NCCN1"),
    ("C1C=CNC=C1", "C1CCNCC1"),
    ("Clc1cccc(Cl)c1N=C1NCCN1", "Clc1cccc(Cl)c1NC1=NCCN1")],
    ids=["1-pyrroline on pyrrolidine", "tolazoline on its imidazolidine",
         "1,4-dihydropyridine on piperidine", "clonidine's imino tautomer on its amino"])
def test_a_misfit_every_tied_match_lays_on_a_like_bond_is_said(
        tmp_path: Path, file: str, deposit: str) -> None:
    """The ring's symmetry ties two matches, each laying the C=N on another
    C-N: the bond was left out as laid differently, and the tied pass
    looked only for refusals. Verified."""
    for seed in (1, 2):
        assert _never_verified(tmp_path, _built(file, 100 + seed),
                               _held(tmp_path, deposit, seed)), seed


def test_6v87_s_hydroxytamoxifen_is_placed_with_its_bond_said(tmp_path: Path) -> None:
    """6V87 chain A: C8-C7 1.49 A where the file has C7=C8. Placed saying
    only "C8=C7 held within noise", the length nowhere."""
    study, placement = _study(tmp_path, _file("OHT"), "6v87_OHT.pdb", "OHT")
    note = "C8-C7 1.49 A fits C-C, the file has C=C"
    assert note in placement["returned"]
    [found] = check_ligand_mappings(study)
    assert found["status"] == PLACED_AS_STATED
    assert note in found["said"]


def test_a_carbon_whose_angle_sp2_and_sp3_share_decides_nothing(tmp_path: Path) -> None:
    """1U0G's erythrose phosphate, its C1-O1 1.42 A under the file's C=O at
    a C1 of 116 degrees, where sp2 and sp3 carbons both lie: refused as
    another molecule, "its C1 is tetrahedral". It is placed as stated, the
    bond and the angle said."""
    study, placement = _study(tmp_path, _file("E4P"), "1u0g_E4P.pdb", "E4P")
    note = "C1-O1 1.42 A fits C-O, the file has C=O; C1's angle 116 degrees does not tell"
    assert note in placement["returned"]
    [found] = check_ligand_mappings(study)
    assert found["status"] == PLACED_AS_STATED
    assert note in found["said"]
