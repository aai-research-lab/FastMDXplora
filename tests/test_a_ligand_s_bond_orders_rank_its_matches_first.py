"""A ligand's matches are ranked by its bond orders before its stereochemistry.

Round 4 kept only the matches whose stereochemistry agreed and ranked the
rest by bond lengths. Where two arms of a centre are alike in the graph
(a vinyl and an isopropenyl, a CHO and a CH2OH), swapping them turns the
centre over, so the swapped match "agreed" however badly it put the file's
double bonds on the structure's single ones, and the wrong isomer was
placed: 4-epi-shikimate on shikimate's deposits, L-glyceraldehyde on
D-glyceraldehyde's, limonene's enantiomer. Now the bond orders choose
first, a deposit's noise allowed each bond two matches assign differently;
among those tied, only one whose stereochemistry agrees is placed.

A methine hydrogen whose two ring arms the match gives the other way round
from the file's was carried through its own bonds (every H-C-Y about 72
degrees). An imine's =N-H turned clear of a crowded neighbour became the
other of E and Z. A roughly built file's worst hydrogen angle was measured
against angles it did not use.
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

from fastmdxplora.setup.ligand import LigandError, _file_name, pose_from_structure  # noqa: E402
from fastmdxplora.validation.ligand_mapping import molecule_from_file  # noqa: E402
from tests.test_a_ligand_is_placed_from_rough_and_noisy_inputs import (  # noqa: E402
    _built,
    _deposit,
    _order,
    _place,
    misplaced,
)

REAL = Path(__file__).parent / "data" / "ligands_real"

#: The CCD ideal files' heavy atoms, in their order.
NAMES = {"SKM": ["O12", "C8", "C6", "O7", "C5", "C4", "C1", "O2", "O3", "C10", "C9", "O11"],
         "3GR": ["C3", "O3", "C2", "O2", "C1", "O1"],
         "GXV": ["O3", "C3", "C2", "O2", "C1", "O1"]}


def _ideal(code: str) -> Chem.Mol:
    mol = Chem.MolFromMolFile(str(REAL / f"{code}_ideal.sdf"), removeHs=False)
    heavy = [a for a in mol.GetAtoms() if a.GetAtomicNum() > 1]
    assert [a.GetSymbol() for a in heavy] == [n.rstrip("0123456789") for n in NAMES[code]]
    for atom, name in zip(heavy, NAMES[code]):
        atom.SetProp("ccd", name)
    Chem.AssignStereochemistryFrom3D(mol)
    return mol


def _versions(mol: Chem.Mol) -> list[tuple[str, Chem.Mol]]:
    """The file as given, its atoms listed in another order, and an MMFF
    conformer of it."""
    order = [int(i) for i in np.random.default_rng(3).permutation(mol.GetNumAtoms())]
    built = Chem.Mol(mol)
    parameters = AllChem.ETKDGv3()
    parameters.randomSeed = 7
    assert AllChem.EmbedMolecule(built, parameters) == 0
    AllChem.MMFFOptimizeMolecule(built, maxIters=2000)
    return [("ccd", mol), ("shuffled", Chem.RenumberAtoms(mol, order)), ("mmff", built)]


def _on_deposit(tmp_path: Path, mol: Chem.Mol, deposit: str, code: str) -> dict:
    sdf = tmp_path / "file.sdf"
    sdf.write_text(Chem.MolToMolBlock(mol), encoding="utf-8")
    record: dict = {}
    pose_from_structure(molecule_from_file(sdf), REAL / deposit, code, required=True,
                        record=record)
    return record


def _wrong_names(mol: Chem.Mol, record: dict) -> list[str]:
    """File atoms laid on a deposit atom of another name and of another
    symmetry class (bond orders, charges and stereochemistry counted). The
    oxygens of one carboxyl count as one class: a deposit's names for them
    do not say which carries the proton (1ZYU's O2 is the short one, the
    CCD's O3)."""
    ranks = list(Chem.CanonicalRankAtoms(mol, breakTies=False))
    for atom in mol.GetAtoms():
        ends = [n for n in atom.GetNeighbors() if n.GetSymbol() == "O"
                and len([m for m in n.GetNeighbors() if m.GetAtomicNum() > 1]) == 1]
        for end in ends[1:]:
            ranks[end.GetIdx()] = ranks[ends[0].GetIdx()]
    by_name = {a.GetProp("ccd"): a.GetIdx() for a in mol.GetAtoms() if a.HasProp("ccd")}
    wrong = []
    for m in record["mapping"]:
        name = mol.GetAtomWithIdx(m["file_atom"]).GetProp("ccd")
        there = m["structure_name"]
        if there != name and ranks[by_name[there]] != ranks[m["file_atom"]]:
            wrong.append(f"{name}->{there}")
    return wrong


def _epimers(mol: Chem.Mol) -> list[str]:
    """Each single-centre epimer of ``mol``, and its enantiomer."""
    heavy = Chem.RemoveHs(mol)
    centres = [i for i, _ in Chem.FindMolChiralCenters(heavy, includeUnassigned=False,
                                                       useLegacyImplementation=False)]
    out = []
    for flipped in [[c] for c in centres] + ([centres] if len(centres) > 1 else []):
        epimer = Chem.Mol(heavy)
        for c in flipped:
            epimer.GetAtomWithIdx(c).InvertChirality()
        out.append(Chem.MolToSmiles(epimer))
    assert Chem.MolToSmiles(heavy) not in out
    return out


@pytest.mark.parametrize("deposit", ["1zyu_SKM.pdb", "2aa9_SKM.pdb", "1u8a_SKM.pdb"])
def test_an_epimer_of_shikimate_is_refused_on_its_deposits(tmp_path: Path,
                                                            deposit: str) -> None:
    """Each epimer at C3, C4 or C5, and the enantiomer, as an MMFF file:
    4-epi-shikimate was placed on all three as decided, its ring C=C on a
    C-C. It is refused, or, where the deposit's ring does not show its
    double bond (2AA9's C4-C5 1.43 A against C4=C10 1.41), placed only with
    the record that the structure did not decide. The right file is placed
    with each atom on its own name."""
    for k, smiles in enumerate(_epimers(_ideal("SKM"))):
        try:
            record = _on_deposit(tmp_path, _built(smiles, 7), deposit, "SKM")
        except LigandError as refused:
            assert refused.code == "setup.ligand.stereo_mismatch", (k, smiles)
            continue
        assert record["bond_orders_undecided"], (k, smiles)
    for name, mol in _versions(_ideal("SKM")):
        assert _wrong_names(mol, _on_deposit(tmp_path, mol, deposit, "SKM")) == [], name


@pytest.mark.parametrize("file_code, deposit, deposit_code", [
    ("GXV", "1w3t_3GR.pdb", "3GR"), ("3GR", "4ptn_GXV.pdb", "GXV")],
    ids=["L-glyceraldehyde on 1W3T", "D-glyceraldehyde on 4PTN"])
def test_the_other_glyceraldehyde_is_refused_on_a_deposit(
        tmp_path: Path, file_code: str, deposit: str, deposit_code: str) -> None:
    """Its CHO and CH2OH swapped, the centre agreed and the C=O lay on the
    deposit's C-OH: placed. From the CCD file, reordered or rebuilt, it is
    refused, and the deposit's own file is placed with its C=O on the
    deposit's. (1W3T names its aldehyde C3 and O3, the CCD C1 and O1, so
    there the names do not say which atom is which.)"""
    for name, mol in _versions(_ideal(file_code)):
        with pytest.raises(LigandError) as refused:
            _on_deposit(tmp_path, mol, deposit, deposit_code)
        assert refused.value.code == "setup.ligand.stereo_mismatch", name
    for name, mol in _versions(_ideal(deposit_code)):
        record = _on_deposit(tmp_path, mol, deposit, deposit_code)
        given = {mol.GetAtomWithIdx(m["file_atom"]).GetProp("ccd"): m["structure_name"]
                 for m in record["mapping"]}
        aldehyde = ("O3", "C3") if deposit_code == "3GR" else ("O1", "C1")
        assert (given["O1"], given["C1"]) == aldehyde, name
        assert record["worst_bond_nm"] < 0.01, name


#: Right and wrong isomers whose wrong one a swap of two arms differing in
#: bond order made agree.
ISOMERS = {
    "limonene": ("CC1=CC[C@@H](C(C)=C)CC1", "CC1=CC[C@H](C(C)=C)CC1"),
    "terpinen-4-ol": ("CC1=CC[C@@](O)(C(C)C)CC1", "CC1=CC[C@](O)(C(C)C)CC1"),
    "pent-1-en-3-ol": ("C=C[C@@H](O)CC", "C=C[C@H](O)CC"),
    "4-methylcyclohex-2-enone": ("O=C1C=C[C@@H](C)CC1", "O=C1C=C[C@H](C)CC1"),
    "3-hydroxycyclohexanone": ("O[C@@H]1CCCC(=O)C1", "O[C@H]1CCCC(=O)C1"),
    "4-hydroxypentan-2-one": ("C[C@@H](O)CC(C)=O", "C[C@H](O)CC(C)=O"),
}


@pytest.mark.parametrize("name", list(ISOMERS))
def test_an_isomer_whose_arms_differ_in_bond_order_is_refused(tmp_path: Path,
                                                               name: str) -> None:
    right, wrong = ISOMERS[name]
    for seed in range(1, 5):
        folder = tmp_path / str(seed)
        folder.mkdir()
        file = _built(right, 100 + seed)
        with pytest.raises(LigandError) as refused:
            _place(folder, file, _built(wrong, 200 + seed), seed=seed)
        assert refused.value.code == "setup.ligand.stereo_mismatch", seed
        held = _built(right, 200 + seed)
        _, record = _place(folder, file, held, seed=seed)
        assert misplaced(file, held, record, seed) == [], seed


#: J8's arm-swap pairs: (file, structure), three conformer pairs each. The
#: threose-like pair (the fifth) the round before refused already: a guard.
ARM_SWAPS = [("O=C[C@@H](O)CO", "O=C[C@H](O)CO"),
             ("C=C[C@H](C)CC", "C=C[C@@H](C)CC"),
             ("C=CC[C@H](C)CCC", "C=CC[C@@H](C)CCC"),
             ("O=CC[C@H](O)CCO", "O=CC[C@@H](O)CCO"),
             ("O=C[C@H](O)[C@@H](O)CO", "O=C[C@@H](O)[C@H](O)CO"),
             ("O=C[C@H](O)[C@H](O)CO", "O=C[C@@H](O)[C@@H](O)CO"),
             ("C=CC[C@](C)(O)CCC", "C=CC[C@@](C)(O)CCC"),
             ("O=C1C=C[C@H](C)CC1", "O=C1C=C[C@@H](C)CC1")]


@pytest.mark.parametrize("file_smiles, structure_smiles", ARM_SWAPS)
def test_each_arm_swap_pair_is_refused(tmp_path: Path, file_smiles: str,
                                       structure_smiles: str) -> None:
    for seed in range(3):
        folder = tmp_path / str(seed)
        folder.mkdir()
        with pytest.raises(LigandError) as refused:
            _place(folder, _built(file_smiles, seed), _built(structure_smiles, 100 + seed),
                   seed=seed + 1)
        assert refused.value.code == "setup.ligand.stereo_mismatch", seed


def test_a_hydroxy_ketone_is_placed_right_in_every_order(tmp_path: Path) -> None:
    """J3's 4-hydroxypentan-2-one, its deposit listed in twelve orders. A
    guard: with these seeds the round before placed it right too, so it
    claims no defect."""
    smiles = "C[C@@H](O)CC(C)=O"
    for seed in range(1, 13):
        folder = tmp_path / str(seed)
        folder.mkdir()
        file, held = _built(smiles, 100 + seed), _built(smiles, 200 + seed)
        _, record = _place(folder, file, held, seed=seed)
        assert misplaced(file, held, record, seed) == [], seed


def _inside(mol: Chem.Mol, xyz: np.ndarray) -> list[int]:
    """Hydrogens on an atom with three heavy neighbours whose H-X-Y angles
    are all under 90 degrees."""
    found = []
    for atom in mol.GetAtoms():
        if atom.GetAtomicNum() != 1:
            continue
        parent = atom.GetNeighbors()[0]
        ys = [y.GetIdx() for y in parent.GetNeighbors() if y.GetAtomicNum() > 1]
        if len(ys) != 3:
            continue
        h = xyz[atom.GetIdx()] - xyz[parent.GetIdx()]
        if all(np.dot(h, xyz[y] - xyz[parent.GetIdx()]) > 0 for y in ys):
            found.append(atom.GetIdx())
    return found


@pytest.mark.parametrize("smiles", [
    "C[C@@H](Oc1cc(-c2cnn(C3CCNCC3)c2)cnc1N)c1c(Cl)ccc(F)c1Cl",
    "Nc1ncnc2n(C3CCCC3)cnc12"], ids=["crizotinib", "9-cyclopentyladenine"])
def test_a_methine_hydrogen_is_never_laid_inside_its_bonds(tmp_path: Path,
                                                           smiles: str) -> None:
    """Twelve MMFF pairs, the file's atoms listed in another order: where the
    match gives a piperidin-4-yl's or a cyclopentyl's two ring arms the other
    way round from the file's, the hydrogen was carried through its carbon
    (H-C-Y 71 to 73 degrees), in 8 and 7 of 12."""
    for k in range(12):
        folder = tmp_path / str(k)
        folder.mkdir()
        file = _built(smiles, 5000 + 11 * k)
        order = [int(i) for i in np.random.default_rng(77 + k).permutation(file.GetNumAtoms())]
        file = Chem.RenumberAtoms(file, order)
        held = _built(smiles, 1000 + 7 * k)
        sdf = folder / "file.sdf"
        sdf.write_text(Chem.MolToMolBlock(file), encoding="utf-8")
        structure = _deposit(folder / "input.pdb", held, _order(held, k), 0.0, k)
        record: dict = {}
        placed, _ = pose_from_structure(molecule_from_file(sdf), structure, "LIG",
                                        required=True, record=record)
        assert _inside(file, placed.conformers[0].m_as("nanometer")) == [], k
        assert record["worst_hydrogen_angle_deg"] < 15.0, k


def _dihedral(xyz: np.ndarray, a: int, b: int, c: int, d: int) -> float:
    from rdkit.Geometry import Point3D

    conformer = Chem.Conformer(len(xyz))
    for i, p in enumerate(xyz):
        conformer.SetAtomPosition(i, Point3D(*(float(v) for v in p)))
    return rdMolTransforms.GetDihedralDeg(conformer, a, b, c, d)


@pytest.mark.parametrize("smiles", ["N=Cc1c(C)cccc1C", "CC(=N)c1c(C)cccc1C",
                                    "Cc1cccc(C)c1C(N)=N"],
                         ids=["aldimine", "ketimine", "benzamidine"])
def test_an_imine_s_hydrogen_keeps_the_file_s_e_or_z(tmp_path: Path, smiles: str) -> None:
    """Coplanar with a 2,6-dimethylphenyl, the =N-H against a methyl: turned
    clear, it became the other isomer at its C=N (5 of 12 aldimines). It
    stays where the file has it, and the record says it is crowded, once."""
    pattern = Chem.MolFromSmarts("c(C):c-C=[NH]")
    recorded = 0
    for seed in range(1, 7):
        for torsion in (0.0, 30.0):
            folder = tmp_path / f"{seed}-{torsion:g}"
            folder.mkdir()
            file, held = _built(smiles, 100 + seed), _built(smiles, 200 + seed)
            match = held.GetSubstructMatch(pattern)
            rdMolTransforms.SetDihedralDeg(held.GetConformer(), match[0], match[2],
                                           match[3], match[4], torsion)
            xyz, record = _place(folder, file, held, seed=seed)
            c, n = file.GetSubstructMatch(pattern)[3:5]
            hydrogen = next(a.GetIdx() for a in file.GetAtomWithIdx(n).GetNeighbors()
                            if a.GetAtomicNum() == 1)
            ring = file.GetSubstructMatch(pattern)[2]
            before = _dihedral(file.GetConformer().GetPositions(), hydrogen, n, c, ring)
            after = _dihedral(np.asarray(xyz) * 10.0, hydrogen, n, c, ring)
            assert (abs(before) < 90.0) == (abs(after) < 90.0), (seed, torsion)
            crowded = record["kept_crowded"]
            named = _file_name(molecule_from_file(folder / "file.sdf"), hydrogen)
            assert crowded in ([], [named]), crowded
            recorded += bool(crowded)
    # Some of the twelve are crowded, and say so, once each.
    assert recorded


def test_a_rough_file_s_worst_hydrogen_angle_is_against_ideal(tmp_path: Path) -> None:
    """A file with one hydrogen drawn 1.3 A long and 30 degrees from its
    carbon's bond: built ideal, it was measured against that angle, 80
    degrees "off"."""
    smiles = "CCc1ccccc1"
    file = _built(smiles, 101)
    hydrogen = next(a.GetIdx() for a in file.GetAtomWithIdx(0).GetNeighbors()
                    if a.GetAtomicNum() == 1)
    conformer = file.GetConformer()
    rdMolTransforms.SetBondLength(conformer, 0, hydrogen, 1.3)
    rdMolTransforms.SetAngleDeg(conformer, 1, 0, hydrogen, 30.0)
    _, record = _place(tmp_path, file, _built(smiles, 201))
    assert "roughly built" in record["hydrogens"]
    assert record["worst_hydrogen_angle_deg"] < 10.0


def test_a_phosphate_s_oxygens_given_within_noise_say_their_lengths(tmp_path: Path) -> None:
    """Methyl phosphate's three oxygens on a deposit whose P=O and P-OH are
    noise apart: which one carries the double bond is recorded with each
    one's deposited length."""
    smiles = "COP(=O)(O)O"
    file, held = _built(smiles, 101), _built(smiles, 201)
    conformer = held.GetConformer()
    phosphorus = 2
    for oxygen in (3, 4, 5):
        rdMolTransforms.SetBondLength(conformer, phosphorus, oxygen, 1.53)
    _, record = _place(tmp_path, file, held)
    [group] = record["assigned_within_noise"]
    assert len(group["given"]) == 3
    assert sum(o["double"] for o in group["given"]) == 1
    assert all(abs(o["length_nm"] - 0.153) < 0.002 for o in group["given"])


def test_a_centre_the_deposit_holds_nearly_flat_is_not_judged(tmp_path: Path) -> None:
    """Butan-2-ol's C2 drawn towards the plane of its three carbons and
    oxygen until their volume is 0.18 (0.77 tetrahedral): between flat (an
    sp2 atom, which disagrees) and judged, it is placed and said not judged,
    of either handedness."""
    from rdkit.Geometry import Point3D

    from fastmdxplora.setup.ligand import _volume

    for smiles in ("C[C@@H](O)CC", "C[C@H](O)CC"):
        held = _built("C[C@@H](O)CC", 201)
        xyz = held.GetConformer().GetPositions()
        centre, around = 1, (0, 2, 3)
        middle = xyz[list(around)].mean(axis=0)
        start = xyz[centre].copy()
        low, high = 0.0, 1.0
        for _ in range(40):
            t = (low + high) / 2
            xyz[centre] = middle + t * (start - middle)
            if abs(_volume(xyz, centre, around)) < 0.18:
                low = t
            else:
                high = t
        held.GetConformer().SetAtomPosition(centre, Point3D(*xyz[centre]))
        folder = tmp_path / smiles.replace("@", "a")
        folder.mkdir()
        _, record = _place(folder, _built(smiles, 101), held)
        # By the structure's name for the atom the file's C2 was laid on.
        laid_on = next(m["structure_name"] for m in record["mapping"] if m["file_atom"] == 1)
        assert record["stereochemistry"]["not_judged"] == [laid_on], smiles


def test_a_hydrogen_left_overlapping_after_the_scan_is_refused(tmp_path: Path,
                                                               monkeypatch) -> None:
    """The refusal reached: with its bar raised past the molecule's closest
    contact, every torsion leaves a hydrogen within it."""
    from fastmdxplora.setup import ligand

    monkeypatch.setattr(ligand, "HYDROGEN_OVERLAP_NM", 0.30)
    with pytest.raises(LigandError) as refused:
        _place(tmp_path, _built("CCCC", 101), _built("CCCC", 201))
    assert refused.value.code == "setup.ligand.hydrogens_overlap"
    assert "at every torsion its rotor can take" in str(refused.value)
