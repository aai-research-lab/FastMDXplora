"""What is said of a placed ligand names only a form its atoms can take.

Round 9 said of right ligands what their atoms cannot be: a bridging O or N
between two phosphorus atoms "fits O=P" or "N=P", a broken C=C of 1.11 A
"fits C#C", a C-C between two stated tetrahedral centres "fits C=C", a C-S
at a sulfonyl "fits C=S", and one long bond of an aromatic ring "the other
redox or tautomeric form". A tautomer was refused as a stereoisomer. A C-N
against a C=N was "another protonation or tautomeric form" where the two
differ by H2, and a true tautomer had no word. The refusals' fix line named
`setup.ligand`, the remedy did not say it moves every ligand, nor where the
file's pose lies, and a few lower things were said loosely.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("rdkit")
pytest.importorskip("mdtraj")

from rdkit import Chem  # noqa: E402

from fastmdxplora.setup.ligand import LigandError, _volume  # noqa: E402
from fastmdxplora.validation.ligand_mapping import (  # noqa: E402
    DISAGREES,
    check_ligand_mappings,
    molecule_from_file,
)
from tests.test_a_ligand_is_placed_from_rough_and_noisy_inputs import _built  # noqa: E402
from tests.test_another_molecule_is_never_verified import _held, _leaned, _place  # noqa: E402
from tests.test_the_placed_molecule_is_the_file_s_molecule import _file, _refusal  # noqa: E402
from tests.test_what_is_said_of_a_placed_ligand_is_what_the_structure_shows import (  # noqa: E402
    _study,
)

REAL = Path(__file__).parent / "data" / "ligands_real"


@pytest.mark.parametrize("code, deposit, gone", [
    ("GNP", "1eft_GNP.pdb", "N=P"),
    ("FAD", "3grs_FAD.pdb", "P=O"),
    ("VIA", "1udt_VIA.pdb", "C=S"),
    ("NDR", "1sqn_NDR.pdb", "C=C"),
    ("FK5", "1bkf_FK5.pdb", "C#C"),
    ("NAD", "4mdh_NAD.pdb", "aromatic")],
    ids=["1EFT's imidodiphosphate N", "3GRS's pyrophosphate O", "1UDT's sulfonyl",
         "1SQN's stated centres", "1BKF's broken C=C", "4MDH's adenine"])
def test_a_right_ligand_s_note_never_names_an_order_its_atoms_cannot_take(
        tmp_path: Path, code: str, deposit: str, gone: str) -> None:
    """Each right ligand's CCD file on its own deposit was placed saying its
    bond "fits" an order no form of it has (or, at 4MDH, one long bond of
    the adenine "the other redox or tautomeric form"). It says none."""
    _, placement = _study(tmp_path, _file(code), deposit, code)
    said = [m["said"] for m in placement.get("bonds_fitting_another_order", [])]
    assert not said, said
    assert gone not in placement["returned"]


def test_one_long_bond_of_an_aromatic_ring_is_not_another_form(tmp_path: Path) -> None:
    """1HA2's warfarin: C13-C15 1.52 A, one bond of its phenyl, said to be
    "the other redox or tautomeric form". Its C2-O3 is still the other
    tautomer."""
    _, placement = _study(tmp_path, _file("SWF"), "1ha2_SWF.pdb", "SWF")
    found = placement.get("bonds_fitting_another_order", [])
    assert not [m for m in found if m["file_order"] == 1.5]
    assert "the file has it aromatic" not in placement["returned"]


def test_a_tautomer_is_not_refused_as_a_stereoisomer(tmp_path: Path) -> None:
    """Acetylacetone's enol file on its keto deposit: refused, "holds the
    double bond C4=C7 within noise on the other side from the file's E or
    Z", where the deposit's C7 is a CH2 and no double bond is there."""
    for seed in (1, 2, 3):
        _, record, line = _place(tmp_path, _built("C/C(O)=C/C(C)=O", 100 + seed),
                                 _held(tmp_path, "CC(=O)CC(C)=O", seed))
        assert "(the other tautomer)" in line, seed
        assert "laid as a single bond, its E or Z not judged" in line, seed


def test_an_amine_on_its_imine_is_the_other_redox_form(tmp_path: Path) -> None:
    """Cyclohexylamine's file on cyclohexanimine's deposit, the forms H2
    apart: "(another protonation or tautomeric form)"."""
    _, _, line = _place(tmp_path, _built("NC1CCCCC1", 101), _held(tmp_path, "N=C1CCCCC1", 1))
    assert "fits C=N, the file has C-NH2 (the other redox form)" in line


def test_warfarin_s_keto_and_enol_are_the_other_tautomer(tmp_path: Path) -> None:
    """1H9Z's warfarin, its C4-O3 1.40 A where the file has C=O: said as a
    length alone, where the docs promise "(the other tautomer)"."""
    _, placement = _study(tmp_path, _file("RWF"), "1h9z_RWF.pdb", "RWF")
    assert "C4-O3 1.40 A fits C-O, the file has C=O (the other tautomer)" in placement["returned"]


def test_no_double_bond_is_said_at_a_stereocentre_that_agrees(tmp_path: Path) -> None:
    """1O86's lisinopril: "C4-N1 1.23 A fits C=N" on the line that says
    its stated centres, C4 among them, agree with the pose."""
    _, placement = _study(tmp_path, _file("LPR"), "1o86_LPR.pdb", "LPR")
    for m in placement.get("bonds_fitting_another_order", []):
        assert "C4" not in m["structure_atoms"].split("-"), m["said"]
    assert "agree with the pose" in placement["stereochemistry"]["said"]


def test_the_refusal_s_fix_line_names_the_setting_its_message_does(tmp_path: Path) -> None:
    """RET's file on 1KT6: the message offers `ligand_pose: file`, and the
    fix line said "Change `setup.ligand`"."""
    from fastmdxplora.remedies import remedy_for

    refused = _refusal(tmp_path, _file("RET"), REAL / "1kt6_RTL.pdb", "RTL")
    assert "`setup.ligand_pose`" in remedy_for(refused.refusal, price=False).fix


def test_a_residue_named_for_another_molecule_still_offers_its_file(tmp_path: Path) -> None:
    """DHT's file on 2AM9's testosterone: the likeliest fix, the deposited
    molecule's own file, was no longer offered."""
    refused = _refusal(tmp_path, _file("DHT"), REAL / "2am9_TES.pdb", "TES")
    assert "supply the file of the TES the structure holds" in refused.args[0]


def test_the_file_s_pose_far_from_the_residue_is_warned(tmp_path: Path) -> None:
    """`ligand_pose: file` with retinal's ideal file on 1KT6 starts it 62 A
    from the deposited retinol, and nothing said so."""
    from fastmdxplora.setup.ligand import pose_by_policy

    sdf = tmp_path / "RET.sdf"
    sdf.write_text(Chem.MolToMolBlock(_file("RET")), encoding="utf-8")
    record: dict = {}
    _, line = pose_by_policy(molecule_from_file(sdf), REAL / "1kt6_RTL.pdb", "RTL",
                             policy="file", record=record)
    assert record.get("from_the_structure_s_residue_nm", 0.0) > 1.0
    assert "from the centre of the structure's RTL" in record.get("warning", "")
    assert "so it starts away from that site" in line


def test_a_bond_to_s_is_said_with_an(tmp_path: Path) -> None:
    """ "its S1-C2 is 1.60 A, a S=C" read "a S=C"."""
    from fastmdxplora.setup.ligand import _other_molecule_said

    names = ["S1", "C2"]
    frame = SimpleNamespace(topology=SimpleNamespace(
        atom=lambda i: SimpleNamespace(name=names[i])))
    said = _other_molecule_said(frame, [0, 1], [16, 6], [(0, 1, 0.160, 1.0, 2.0, 0, "order",
                                                         None)])
    assert "an S=C" in said and "a S=C" not in said


def _flattened(smiles: str, seed: int, target: float) -> np.ndarray:
    """``smiles`` built, its S made to lean the other way, its unit-vector
    volume ``target`` against the file's, by moving the S along its
    neighbours' normal and each neighbour's branch with it, its bonds kept."""
    held = _built(smiles, 200 + seed)
    xyz = held.GetConformer().GetPositions()
    s = next(a.GetIdx() for a in held.GetAtoms() if a.GetSymbol() == "S")
    around = sorted(n.GetIdx() for n in held.GetAtomWithIdx(s).GetNeighbors()
                    if n.GetAtomicNum() > 1)
    sign = np.sign(_volume(xyz, s, tuple(around)))
    centre = xyz[around].mean(axis=0)
    normal = np.cross(xyz[around[1]] - xyz[around[0]], xyz[around[2]] - xyz[around[0]])
    normal /= np.linalg.norm(normal)

    def branch(n: int) -> set[int]:
        side, todo = {n}, [n]
        while todo:
            for m in held.GetAtomWithIdx(todo.pop()).GetNeighbors():
                if m.GetIdx() not in side and m.GetIdx() != s:
                    side.add(m.GetIdx())
                    todo.append(m.GetIdx())
        return side

    best = None
    for t in np.linspace(-1.0, 1.0, 2001):
        moved = xyz.copy()
        moved[s] = centre + t * normal
        for n in around:
            u = xyz[n] - moved[s]
            delta = moved[s] + 1.81 * u / np.linalg.norm(u) - xyz[n]
            for a in branch(n):
                moved[a] = xyz[a] + delta
        off = abs(_volume(moved, s, tuple(around)) + sign * target)
        if best is None or off < best[0]:
            best = (off, moved)
    return best[1]


def test_a_centre_s_lean_is_said_in_plain_words(tmp_path: Path) -> None:
    """A sulfonium with no terminal neighbour leaning 0.20 the other way:
    "its volume -0.20", a raw signed volume whose sign is the neighbours'
    order's."""
    smiles = "CC[S@+](CCC)CCCC"
    with pytest.raises(LigandError) as refused:
        _place(tmp_path, _built(smiles, 101), _held(tmp_path, smiles, 1,
                                                    _flattened(smiles, 1, 0.20)))
    said = refused.value.args[0]
    assert refused.value.code == "setup.ligand.stereo_mismatch"
    assert "its volume" not in said
    assert "% of a tetrahedral centre's lean, under the 32% judged" in said


def test_the_check_holds_a_laid_atom_where_setup_laid_it(tmp_path: Path) -> None:
    """The check dropped the atom setup laid on the file's side from the
    deviation altogether: wherever the prepared system held it, only the
    centre's sign was checked."""
    smiles = "CC[S@](=O)C[C@H](N)C(=O)O"
    xyz, end = _leaned(smiles, 1, "S", "O", 0.22)
    study, placement = _study(tmp_path, _built(smiles, 101), str(_held(tmp_path, smiles, 1, xyz)),
                              "LIG")
    [found] = check_ligand_mappings(study)
    assert "from its deposited place" in found["said"]
    topology = study / "setup" / "topology.pdb"
    lines = topology.read_text().splitlines()
    laid = int((placement.get("reflected_to_the_file_s_side") or [{}])[0].get("file_atom", end))
    row = [i for i, line in enumerate(lines) if line.startswith(("ATOM", "HETATM"))][laid]
    x = float(lines[row][30:38]) + 0.3
    lines[row] = f"{lines[row][:30]}{x:8.3f}{lines[row][38:]}"
    topology.write_text("\n".join(lines) + "\n", encoding="utf-8")
    [moved] = check_ligand_mappings(study)
    assert moved["status"] == DISAGREES
