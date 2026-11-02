"""What setup and the check say of a placed ligand is what the structure shows.

Round 6 placed a ligand whose deposit did not decide a centre, or modelled it
flat, and setup's record and the check then said "agree with the pose" and
"verified" in the same words as for a centre the deposit decided, the wrong
glyceraldehyde on 4PTN B included. A flat or 2D file's hydrogens were
measured against the construction that built them, so 0.0 degrees was
recorded of a methine hydrogen 77 and 140 degrees from two of its bonds. An
alcohol's file on its ketone's deposit was placed as "modelled flat". A
badly modelled copy (5TED, its ring 0.12 A off any order) was judged with a
good deposit's noise. And one bond no bond's length had made the whole
comparison of two matches a tie, the other bonds' evidence thrown away.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("rdkit")
pytest.importorskip("mdtraj")

from rdkit import Chem  # noqa: E402
from rdkit.Chem import AllChem  # noqa: E402

from fastmdxplora.setup.ligand import LigandError, pose_by_policy, pose_from_structure  # noqa: E402
from fastmdxplora.validation.ligand_mapping import (  # noqa: E402
    VERIFIED,
    check_ligand_mappings,
    molecule_from_file,
)
from tests.test_a_ligand_is_placed_from_rough_and_noisy_inputs import _built, _place  # noqa: E402
from tests.test_a_ligand_s_bond_orders_rank_its_matches_first import (  # noqa: E402
    _epimers,
    _ideal,
    _versions,
)
from tests.test_a_prepared_ligand_s_mapping_is_checked import _topology  # noqa: E402

REAL = Path(__file__).parent / "data" / "ligands_real"

#: The check's statuses for a ligand the structure did not decide, or
#: whose hydrogen lies far from where it should (named here so this file
#: reads on the code before them).
PLACED_AS_STATED = "placed as stated"
AGREES = "agrees"


class _Held:
    def __init__(self, mol: Chem.Mol):
        self.rdkit = mol


def _study(tmp_path: Path, mol: Chem.Mol, deposit: str, code: str) -> tuple[Path, dict]:
    """A study prepared as setup prepares it from ``mol`` on ``deposit``."""
    setup = tmp_path / "study" / "setup"
    (setup / "ligands").mkdir(parents=True)
    structure = setup / "input.pdb"
    shutil.copy(REAL / deposit, structure)
    ligand = setup / "ligands" / f"{code}.sdf"
    ligand.write_text(Chem.MolToMolBlock(mol), encoding="utf-8")
    placement: dict = {}
    placed, said = pose_by_policy(molecule_from_file(ligand), structure, code, policy="auto",
                                  record=placement)
    _topology(setup / "topology.pdb", _Held(mol), placed.conformers[0].m_as("nanometer"), code)
    (setup / "setup_parameters.json").write_text(json.dumps({
        "phase": "setup", "parameters": {"ligand_pose": "auto"},
        "resolved_forcefield": {"ligand": {"files": [str(ligand)], "name": code}},
        "ligand_poses": [{"name": code, "copy": 1, "file": str(ligand),
                          "sha256": hashlib.sha256(ligand.read_bytes()).hexdigest(),
                          **placement}]}), encoding="utf-8")
    return tmp_path / "study", {**placement, "returned": said}


@pytest.mark.parametrize("right, file_code, deposit, centre", [
    (True, "GXV", "4ptn_GXV_B.pdb", "C3"), (False, "3GR", "4ptn_GXV_B.pdb", "C3"),
    (True, "3GR", "1t5e_3GR.pdb", "C3")],
    ids=["GXV on 4PTN B", "3GR on 4PTN B", "3GR on 1T5E"])
def test_a_centre_the_structure_did_not_decide_is_said_so(
        tmp_path: Path, right: bool, file_code: str, deposit: str, centre: str) -> None:
    """Setup's record, the line it returns and the check name the centre
    taken from the file, with the deposit's lengths there, and never count
    it among those that agree: the right file and the wrong alike read
    "placed as stated", not "verified"."""
    study, placement = _study(tmp_path, _ideal(file_code), deposit, "GXV"
                              if "4ptn" in deposit else "3GR")
    stereo = placement["stereochemistry"]
    assert stereo["undecided"] and "0 of them agree with the pose" in stereo["said"]
    assert "did not decide which arm carries the double bond" in stereo["said"]
    assert "did not decide which arm carries the double bond" in placement["returned"]
    [found] = check_ligand_mappings(study)
    assert found["status"] == PLACED_AS_STATED
    assert found["said"].startswith("placed as stated; the structure did not decide")
    assert " A" in found["said"].split(";")[1]
    assert "agree with the pose;" not in found["said"].replace("0 of them agree with the pose", "")


def _flattened_study(tmp_path: Path, smiles: str, centre: int, seed: int = 1,
                     mirror_hydrogen: bool = False) -> tuple[Path, dict]:
    """A study prepared from ``smiles`` on its own deposit with ``centre``
    laid in its three heavy neighbours' plane; ``mirror_hydrogen``, the
    prepared hydrogen there then put on the other side."""
    from rdkit.Geometry import Point3D

    from tests.test_a_ligand_is_placed_from_rough_and_noisy_inputs import _deposit, _order

    held = _built(smiles, 200 + seed)
    conformer = held.GetConformer()
    xyz = conformer.GetPositions()
    a, b, c = (xyz[n.GetIdx()] for n in held.GetAtomWithIdx(centre).GetNeighbors()
               if n.GetAtomicNum() > 1)
    normal = np.cross(b - a, c - a)
    normal /= np.linalg.norm(normal)
    conformer.SetAtomPosition(centre, Point3D(*(xyz[centre]
                                                - np.dot(xyz[centre] - a, normal) * normal)))
    file = _built(smiles, 100 + seed)
    setup = tmp_path / "study" / "setup"
    (setup / "ligands").mkdir(parents=True)
    structure = _deposit(setup / "input.pdb", held, _order(held, seed), 0.0, seed)
    ligand = setup / "ligands" / "LIG.sdf"
    ligand.write_text(Chem.MolToMolBlock(file), encoding="utf-8")
    record: dict = {}
    placed, said = pose_from_structure(molecule_from_file(ligand), structure, "LIG",
                                       required=True, record=record)
    xyz = np.array(placed.conformers[0].m_as("nanometer"))
    if mirror_hydrogen:
        hydrogen = next(n.GetIdx() for n in file.GetAtomWithIdx(centre).GetNeighbors()
                        if n.GetAtomicNum() == 1)
        xyz[hydrogen] = 2 * xyz[centre] - xyz[hydrogen]
    _topology(setup / "topology.pdb", _Held(file), xyz, "LIG")
    (setup / "setup_parameters.json").write_text(json.dumps({
        "phase": "setup", "parameters": {"ligand_pose": "auto"},
        "resolved_forcefield": {"ligand": {"files": [str(ligand)], "name": "LIG"}},
        "ligand_poses": [{"name": "LIG", "copy": 1, "file": str(ligand),
                          "sha256": hashlib.sha256(ligand.read_bytes()).hexdigest(),
                          **record}]}), encoding="utf-8")
    return tmp_path / "study", {**record, "returned": said}


def test_a_centre_the_structure_models_flat_is_said_so(tmp_path: Path) -> None:
    """Butan-2-ol's carbinol laid flat in its deposit: placed, and read
    "placed as stated; the structure models ... flat" by the structure's
    own name for the atom, where both said "agree with the pose; 1 held
    within noise"."""
    study, placement = _flattened_study(tmp_path, "C[C@@H](O)CC", 1)
    stereo = placement["stereochemistry"]
    assert "not judged: the structure models it flat" in stereo["said"]
    assert "0 of them agree with the pose" in stereo["said"]
    [found] = check_ligand_mappings(study)
    assert found["status"] == PLACED_AS_STATED
    flat = [m["structure_name"] for m in placement["mapping"] if m["file_atom"] == 1][0]
    assert f"the structure models {flat} flat" in found["said"]


def test_a_hydrogen_on_the_other_side_of_a_flat_centre_does_not_agree(tmp_path: Path) -> None:
    """The same, its prepared hydrogen then put on the other side of the
    flat carbinol: the other enantiomer, which the check read only from the
    deposit's flat heavy atoms and called "placed as stated"."""
    study, _ = _flattened_study(tmp_path, "C[C@@H](O)CC", 1, mirror_hydrogen=True)
    [found] = check_ligand_mappings(study)
    assert found["status"] == "does not agree"
    assert "lies on the other side" in found["said"]


def test_a_decided_centre_is_still_verified(tmp_path: Path) -> None:
    """3GR on 1W3T's copy D, whose C=O the deposit shows: verified. A guard,
    claiming no defect: the round before verified it too."""
    study, placement = _study(tmp_path, _ideal("3GR"), "1w3t_3GR.pdb", "3GR")
    assert placement["bond_orders_undecided"] == []
    [found] = check_ligand_mappings(study)
    assert found["status"] == VERIFIED


def _methine_angles(mol: Chem.Mol, xyz: np.ndarray, centre: int) -> list[float]:
    x = xyz[centre]
    hydrogen = next(n.GetIdx() for n in mol.GetAtomWithIdx(centre).GetNeighbors()
                    if n.GetAtomicNum() == 1)
    h = (xyz[hydrogen] - x) / np.linalg.norm(xyz[hydrogen] - x)
    out = []
    for n in mol.GetAtomWithIdx(centre).GetNeighbors():
        if n.GetAtomicNum() > 1:
            v = (xyz[n.GetIdx()] - x) / np.linalg.norm(xyz[n.GetIdx()] - x)
            out.append(float(np.degrees(np.arccos(np.clip(h @ v, -1.0, 1.0)))))
    return out


def test_a_2d_file_s_methine_hydrogens_are_laid_equally_on_their_bonds(tmp_path: Path) -> None:
    """SAM on 1VID from a 2D file: its C1' bonds span 92 to 142 degrees, and
    the hydrogen laid opposite their sum sat 77 degrees from C2' and 140 from
    O4' (the 3D file's 101 to 103). Each methine's hydrogen now makes angles
    with its three bonds within 15 degrees of each other."""
    mol = Chem.MolFromMolFile(str(REAL / "SAM_ideal.sdf"), removeHs=False)
    drawn = Chem.Mol(mol)
    AllChem.Compute2DCoords(drawn)
    Chem.WedgeMolBonds(drawn, drawn.GetConformer())
    sdf = tmp_path / "2d.sdf"
    sdf.write_text(Chem.MolToMolBlock(drawn), encoding="utf-8")
    record: dict = {}
    placed, _ = pose_from_structure(molecule_from_file(sdf), REAL / "1vid_SAM.pdb", "SAM",
                                    required=True, record=record)
    xyz = np.asarray(placed.conformers[0].m_as("nanometer")) * 10.0
    methines = [a.GetIdx() for a in drawn.GetAtoms() if a.GetAtomicNum() == 6
                and sum(n.GetAtomicNum() > 1 for n in a.GetNeighbors()) == 3
                and sum(n.GetAtomicNum() == 1 for n in a.GetNeighbors()) == 1]
    assert methines
    for centre in methines:
        angles = _methine_angles(drawn, xyz, centre)
        assert max(angles) - min(angles) < 15.0, (centre, angles)


def test_a_hydrogen_placed_badly_reads_high_and_is_not_verified(tmp_path: Path) -> None:
    """Glucose from a 2D file, prepared, then its C1 hydrogen turned 50
    degrees: the check measured it against the construction it rebuilt, and
    read 0.0 of a placement setup had built badly. It reads the turn, and
    says the ligand "agrees, with a hydrogen ... from ideal", not verified."""
    glucose = "OC[C@H]1O[C@H](O)[C@H](O)[C@@H](O)[C@@H]1O"
    drawn = Chem.AddHs(Chem.MolFromSmiles(glucose))
    AllChem.Compute2DCoords(drawn)
    Chem.WedgeMolBonds(drawn, drawn.GetConformer())
    held = _built(glucose, 301)
    xyz, record = _place(tmp_path, drawn, held)
    study_xyz = np.asarray(xyz) * 10.0
    # Its anomeric carbon (bonded to the ring O and an OH) and its H.
    c1 = held.GetSubstructMatch(Chem.MolFromSmarts("[OX2;R][CX4;R]([OX2H1])"))[1]
    h1 = next(n.GetIdx() for n in drawn.GetAtomWithIdx(c1).GetNeighbors() if n.GetAtomicNum() == 1)
    ring_o = held.GetSubstructMatch(Chem.MolFromSmarts("[OX2;R][CX4;R]([OX2H1])"))[0]
    # Turned 50 degrees about the normal of its H-C1-O5 plane, through C1.
    bent = study_xyz.copy()
    h, x = study_xyz[h1] - study_xyz[c1], study_xyz[ring_o] - study_xyz[c1]
    axis = np.cross(h, x) / np.linalg.norm(np.cross(h, x))
    t = np.radians(-50.0)
    bent[h1] = study_xyz[c1] + (h * np.cos(t) + np.cross(axis, h) * np.sin(t)
                                + axis * np.dot(axis, h) * (1 - np.cos(t)))
    bent = bent / 10.0
    setup = tmp_path / "study" / "setup"
    (setup / "ligands").mkdir(parents=True)
    shutil.copy(tmp_path / "input.pdb", setup / "input.pdb")
    ligand = setup / "ligands" / "LIG.sdf"
    shutil.copy(tmp_path / "file.sdf", ligand)
    _topology(setup / "topology.pdb", _Held(drawn), bent, "LIG")
    (setup / "setup_parameters.json").write_text(json.dumps({
        "phase": "setup", "parameters": {"ligand_pose": "auto"},
        "resolved_forcefield": {"ligand": {"files": [str(ligand)], "name": "LIG"}},
        "ligand_poses": [{"name": "LIG", "copy": 1, "file": str(ligand),
                          "sha256": hashlib.sha256(ligand.read_bytes()).hexdigest(),
                          **record}]}), encoding="utf-8")
    [found] = check_ligand_mappings(tmp_path / "study")
    assert found["worst_hydrogen_angle_deg"] > 35.0
    assert found["status"] == AGREES
    assert found["said"].startswith("agrees, with a hydrogen")
    assert "degrees from ideal" in found["said"]


@pytest.mark.parametrize("alcohol, ketone", [
    ("C[C@@H](O)CC", "CC(=O)CC"),
    ("O[C@@H]1CCCC[C@@H]1C", "O=C1CCCCC1C"),
    ("C[C@@H](O)c1ccccc1", "CC(=O)c1ccccc1")],
    ids=["butan-2-ol on butanone", "2-methylcyclohexanol on its ketone",
         "1-phenylethanol on acetophenone"])
def test_an_alcohol_s_file_on_its_ketone_s_deposit_is_refused(
        tmp_path: Path, alcohol: str, ketone: str) -> None:
    """The same heavy graph, the carbinol on the planar carbonyl carbon: it
    was placed, recorded only as "modelled flat". The deposit's C-O of 1.22 A
    is a C=O where the file has a C-OH: another molecule."""
    for seed in (1, 2):
        folder = tmp_path / str(seed)
        folder.mkdir()
        with pytest.raises(LigandError) as refused:
            _place(folder, _built(alcohol, 100 + seed), _built(ketone, 200 + seed), seed=seed)
        assert refused.value.code == "setup.ligand.atoms_unmatched", seed
        assert ", a C=O, where the file has C-OH" in refused.value.args[0], seed


@pytest.mark.parametrize("deposit", ["5ted_SKM_A.pdb", "5ted_SKM_B.pdb"])
def test_a_badly_modelled_copy_is_judged_with_its_own_noise(tmp_path: Path, deposit: str) -> None:
    """5TED's shikimate, its ring 0.05 to 0.16 A off any order: judged with
    0.025 A of noise, the right file was refused in every version and the C8
    epimer placed as decided. Its own noise is recorded; the right file is
    placed, and no epimer as decided."""
    for name, mol in _versions(_ideal("SKM")):
        sdf = tmp_path / f"{name}.sdf"
        sdf.write_text(Chem.MolToMolBlock(mol), encoding="utf-8")
        record: dict = {}
        pose_from_structure(molecule_from_file(sdf), REAL / deposit, "SKM", required=True,
                            record=record)
        assert record["bond_noise_nm"] > 0.005, name
    for smiles in _epimers(_ideal("SKM")):
        sdf = tmp_path / "epimer.sdf"
        sdf.write_text(Chem.MolToMolBlock(_built(smiles, 7)), encoding="utf-8")
        record = {}
        try:
            pose_from_structure(molecule_from_file(sdf), REAL / deposit, "SKM", required=True,
                                record=record)
        except LigandError:
            continue
        assert record["bond_orders_undecided"], smiles


def _stretched(held: Chem.Mol, a: int, b: int, length: float) -> Chem.Mol:
    from rdkit.Chem import rdMolTransforms

    held = Chem.Mol(held)
    rdMolTransforms.SetBondLength(held.GetConformer(), a, b, length)
    return held


def _pulled(held: Chem.Mol, a: int, b: int, length: float) -> Chem.Mol:
    """``held`` with ring atom ``b`` and its hydrogens and terminal atoms
    moved along the a-b bond until it is ``length`` A long."""
    from rdkit.Geometry import Point3D

    held = Chem.Mol(held)
    conformer = held.GetConformer()
    xyz = conformer.GetPositions()
    bond = xyz[b] - xyz[a]
    shift = bond / np.linalg.norm(bond) * (length - np.linalg.norm(bond))
    carried = [b] + [n.GetIdx() for n in held.GetAtomWithIdx(b).GetNeighbors() if n.GetDegree() == 1]
    for i in carried:
        conformer.SetAtomPosition(i, Point3D(*(xyz[i] + shift)))
    return held


def test_a_stretched_arm_loses_only_its_own_bond(tmp_path: Path) -> None:
    """D-glyceraldehyde's deposit with its CH2OH C-O stretched to 1.66 A, no
    C-O's length, its C=O still 1.21 A: one such bond tied the whole swap,
    and L-glyceraldehyde was placed as undecided. The C=O still decides."""
    d_form, l_form = "O=C[C@@H](O)CO", "O=C[C@H](O)CO"
    for seed in (1, 2, 3):
        held = _built(d_form, 200 + seed)
        hydroxymethyl = held.GetSubstructMatch(Chem.MolFromSmarts("[CH2][OH]"))
        held = _stretched(held, hydroxymethyl[0], hydroxymethyl[1], 1.66)
        folder = tmp_path / str(seed)
        folder.mkdir()
        with pytest.raises(LigandError) as refused:
            _place(folder, _built(l_form, 100 + seed), held, seed=seed)
        assert refused.value.code == "setup.ligand.stereo_mismatch", seed


def test_a_stretched_c_c_leaves_the_c_o_to_decide(tmp_path: Path) -> None:
    """1W3T's copy D with its C1-C2 stretched to 1.73 A: L-glyceraldehyde was
    placed as undecided though its C=O (1.23 A) and C-OH (1.46 A) decide it."""
    lines = (REAL / "1w3t_3GR.pdb").read_text(encoding="utf-8").splitlines()[:-1]
    xyz = {line[12:16].strip(): np.array([float(line[30:38]), float(line[38:46]),
                                          float(line[46:54])]) for line in lines}
    shift = xyz["C1"] - xyz["C2"]
    shift = shift / np.linalg.norm(shift) * (1.73 - np.linalg.norm(shift))
    out = []
    for line in lines:
        p = xyz[line[12:16].strip()] + (shift if line[12:16].strip() in ("C1", "O1") else 0.0)
        out.append(f"{line[:30]}{p[0]:8.3f}{p[1]:8.3f}{p[2]:8.3f}{line[54:]}")
    deposit = tmp_path / "stretched.pdb"
    deposit.write_text("\n".join(out + ["END"]) + "\n", encoding="utf-8")
    for name, mol in _versions(_ideal("GXV")):
        sdf = tmp_path / f"{name}.sdf"
        sdf.write_text(Chem.MolToMolBlock(mol), encoding="utf-8")
        with pytest.raises(LigandError) as refused:
            pose_from_structure(molecule_from_file(sdf), deposit, "3GR", required=True)
        assert refused.value.code == "setup.ligand.stereo_mismatch", name
    record: dict = {}
    pose_from_structure(molecule_from_file(_write(tmp_path, _ideal("3GR"))), deposit, "3GR",
                        required=True, record=record)
    assert record["bond_orders_undecided"] == []


def _write(tmp_path: Path, mol: Chem.Mol) -> Path:
    path = tmp_path / "right.sdf"
    path.write_text(Chem.MolToMolBlock(mol), encoding="utf-8")
    return path


def test_a_ring_bond_stretched_past_any_length_still_lets_the_ring_decide(
        tmp_path: Path) -> None:
    """A shikimate C8-epimer deposit, one ring C-C away from the double
    bond stretched to 1.72 A, no C-C's length: the ring's reversal was tied
    outright, and the right file's epimer placed as undecided, though the
    deposit's C=C (1.34 A) and C-C (1.50 A) decide it."""
    right = "O=C(O)C1=C[C@@H](O)[C@@H](O)[C@H](O)C1"
    epimer = "O=C(O)C1=C[C@@H](O)[C@H](O)[C@H](O)C1"
    ring = Chem.MolFromSmarts("C1=C[CH1][CH1][CH1][CH2]1")
    for seed in (1, 2, 3):
        held = _built(epimer, 500 + seed)
        match = held.GetSubstructMatch(ring)
        held = _pulled(held, match[3], match[4], 1.72)
        folder = tmp_path / str(seed)
        folder.mkdir()
        with pytest.raises(LigandError) as refused:
            _place(folder, _built(right, 100 + seed), held, seed=seed)
        assert refused.value.code == "setup.ligand.stereo_mismatch", seed


@pytest.mark.parametrize("smiles", ["O[C@@H]1CCCC[C@H]1O", "OC(=O)[C@@H](O)[C@H](O)C(O)=O"],
                         ids=["trans-1,2-cyclohexanediol", "meso-tartaric acid"])
def test_a_symmetric_ligand_with_one_centre_modelled_flat_is_placed(
        tmp_path: Path, smiles: str) -> None:
    """One carbinol carbon moved into its three neighbours' plane: the
    symmetry match lays the other centre on that flat atom, so every match
    had a flat centre held against it, and the right file was refused as
    another stereoisomer. The flat atom is the structure's, and it is not
    judged."""
    from rdkit.Geometry import Point3D

    mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
    centre = Chem.FindMolChiralCenters(mol, includeUnassigned=True,
                                       useLegacyImplementation=False)[0][0]
    for seed in (1, 2, 3):
        held = _built(smiles, 200 + seed)
        conformer = held.GetConformer()
        xyz = conformer.GetPositions()
        a, b, c = (xyz[n.GetIdx()] for n in held.GetAtomWithIdx(centre).GetNeighbors()
                   if n.GetAtomicNum() > 1)
        normal = np.cross(b - a, c - a)
        normal /= np.linalg.norm(normal)
        conformer.SetAtomPosition(centre, Point3D(*(xyz[centre]
                                                    - np.dot(xyz[centre] - a, normal) * normal)))
        folder = tmp_path / str(seed)
        folder.mkdir()
        _, record = _place(folder, _built(smiles, 100 + seed), held, seed=seed)
        [flat] = record["stereochemistry"]["modelled_flat"]
        assert flat["said"] == "not judged: the structure models this atom flat", seed
