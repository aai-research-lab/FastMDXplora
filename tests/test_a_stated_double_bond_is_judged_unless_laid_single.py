"""A stated double bond is judged unless the deposit lays it truly single,
and an aromatic five-ring is never verified on its saturated deposit.

Round 10 left a stated double bond's E or Z unjudged wherever a carbon of it
read tetrahedral or its length was nearer a single's: so a wrong E/Z file
on a deposit whose C=C was a little long, or whose carbon was bent on a
planar C=C, was placed "as stated" and relaxed to the deposit's isomer (a
stilbene at 1.45 A, 3R6P's abscisic acid, 1KT3's retinol, 6V87's
hydroxytamoxifen, 4PMF's curcumin, four retinoic acid copies), and the bent
carbon was said as a single bond's. It also judged a small ring's atoms by
angle, which says nothing there, so indole's file on indoline's deposit,
furan's on THF's, thiophene's and benzofuran's on their saturated forms
were verified.
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

from fastmdxplora.setup.ligand import LigandError  # noqa: E402
from tests.test_a_ligand_is_placed_from_rough_and_noisy_inputs import _built  # noqa: E402
from tests.test_another_molecule_is_never_verified import (  # noqa: E402
    _held,
    _never_verified,
    _place,
)
from tests.test_the_placed_molecule_is_the_file_s_molecule import _file, _refusal  # noqa: E402
from tests.test_what_is_said_of_a_placed_ligand_is_what_the_structure_shows import (  # noqa: E402
    _study,
)

REAL = Path(__file__).parent / "data" / "ligands_real"


def _stretched(smiles: str, seed: int, length: float) -> np.ndarray:
    """``smiles`` built, its stated C=C stretched to ``length`` (A) by moving
    one side along the bond."""
    held = _built(smiles, 200 + seed)
    xyz = held.GetConformer().GetPositions()
    bond = next(b for b in held.GetBonds() if b.GetStereo() != Chem.BondStereo.STEREONONE)
    u, v = bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()
    cut = Chem.RWMol(held)
    cut.RemoveBond(u, v)
    side = next(f for f in Chem.GetMolFrags(cut.GetMol(), asMols=False) if v in f)
    axis = (xyz[v] - xyz[u]) / np.linalg.norm(xyz[v] - xyz[u])
    shift = length - float(np.linalg.norm(xyz[v] - xyz[u]))
    for k in side:
        xyz[k] = xyz[k] + shift * axis
    return xyz


@pytest.mark.parametrize("length", [1.45, 1.48])
def test_a_wrong_e_z_file_on_a_stretched_c_c_is_refused(tmp_path: Path, length: float) -> None:
    """cis-Stilbene's file on a trans deposit whose C=C is 1.45 or 1.48 A:
    "laid as a single bond, its E or Z not judged", placed, and relaxed to
    trans."""
    deposit = "C(=C/c1ccccc1)\\c1ccccc1"
    for seed in (1, 2):
        refused = _refusal(tmp_path, _built("C(=C\\c1ccccc1)\\c1ccccc1", 100 + seed),
                           _held(tmp_path, deposit, seed, _stretched(deposit, seed, length)),
                           "LIG")
        assert refused.code == "setup.ligand.stereo_mismatch", seed


def _flipped(code: str, u: int, v: int) -> Chem.Mol:
    """The CCD file of ``code`` with its double bond u=v turned E to Z (or
    back), by mirroring v's side through the bond, then relaxed."""
    mol = Chem.Mol(_file(code))
    cut = Chem.RWMol(mol)
    cut.RemoveBond(u, v)
    side = next(f for f in Chem.GetMolFrags(cut.GetMol(), asMols=False) if v in f)
    xyz = mol.GetConformer().GetPositions()
    axis = (xyz[v] - xyz[u]) / np.linalg.norm(xyz[v] - xyz[u])
    for k in side:
        r = xyz[k] - xyz[v]
        xyz[k] = xyz[v] + (2.0 * np.dot(r, axis) * axis - r)
    for k, p in enumerate(xyz):
        mol.GetConformer().SetAtomPosition(k, Point3D(*(float(c) for c in p)))
    AllChem.MMFFOptimizeMolecule(mol, maxIters=3000)
    Chem.AssignStereochemistryFrom3D(mol)
    assert Chem.MolToSmiles(Chem.RemoveHs(mol)) != Chem.MolToSmiles(Chem.RemoveHs(_file(code)))
    return mol


@pytest.mark.parametrize("code, deposit, bond", [
    ("A8S", "3r6p_A8S.pdb", (1, 2)), ("A8S", "3r6p_A8S.pdb", (3, 4)),
    ("RTL", "1kt3_RTL.pdb", (8, 9)),
    ("OHT", "6v87_OHT.pdb", (2, 9)),
    ("CUR", "4pmf_CUR.pdb", (2, 3)),
    ("REA", "2fr3_REA.pdb", (10, 11)), ("REA", "5fhz_REA.pdb", (10, 11)),
    ("REA", "6eu9_REA_B.pdb", (10, 11)), ("REA", "6eu9_REA_D.pdb", (10, 11))],
    ids=["3R6P's abscisic acid 2E", "3R6P's abscisic acid 4Z", "1KT3's 9-cis retinol",
         "6V87 A's E hydroxytamoxifen", "4PMF A's curcumin made Z",
         "2FR3's 11-cis retinoic acid", "5FHZ's 11-cis retinoic acid",
         "6EU9 B's 11-cis retinoic acid", "6EU9 D's 11-cis retinoic acid"])
def test_a_real_deposit_s_other_e_z_isomer_is_refused(
        tmp_path: Path, code: str, deposit: str, bond: tuple[int, int]) -> None:
    """Each wrong E/Z file was placed "as stated", its C=C "laid as a single
    bond", and relaxed to the deposit's isomer."""
    with pytest.raises(LigandError) as refused:
        _study(tmp_path, _flipped(code, *bond), deposit, code)
    assert refused.value.code == "setup.ligand.stereo_mismatch"


@pytest.mark.parametrize("code, deposit", [
    ("PLP", "1o61_PLP.pdb"), ("CUR", "4pmf_CUR.pdb"), ("RTL", "1kt3_RTL.pdb"),
    ("REA", "2fr3_REA.pdb"), ("REA", "5fhz_REA.pdb"), ("REA", "6eu9_REA_B.pdb"),
    ("REA", "6eu9_REA_D.pdb")],
    ids=["1O61's PLP", "4PMF A's curcumin", "1KT3's retinol", "2FR3's retinoic acid",
         "5FHZ's retinoic acid", "6EU9 B's retinoic acid", "6EU9 D's retinoic acid"])
def test_a_right_file_s_double_bond_is_not_said_saturated(
        tmp_path: Path, code: str, deposit: str) -> None:
    """A carbon bent under 114 degrees on a 1.23 A C=O or a 1.34 to 1.44 A
    C=C was said "tetrahedral as in C-C", or the bond "laid as a single
    bond"."""
    _, placement = _study(tmp_path, _file(code), deposit, code)
    said = [m["said"] for m in placement.get("bonds_fitting_another_order", [])]
    assert not said, said
    assert "laid as a single bond" not in placement["returned"]


def test_a_tautomer_s_double_bond_is_still_left_unjudged(tmp_path: Path) -> None:
    """Acetylacetone's enol file on its keto deposit stays placed, as the
    other tautomer, its C=C laid single."""
    _, _, line = _place(tmp_path, _built("C/C(O)=C/C(C)=O", 101),
                        _held(tmp_path, "CC(=O)CC(C)=O", 1))
    assert "(the other tautomer)" in line
    assert "laid as a single bond, its E or Z not judged" in line


@pytest.mark.parametrize("file, deposit", [
    ("c1ccc2[nH]ccc2c1", "C1Cc2ccccc2N1"), ("c1ccoc1", "C1CCOC1"),
    ("c1ccsc1", "C1CCSC1"), ("c1ccc2occc2c1", "C1Cc2ccccc2O1")],
    ids=["indole on indoline", "furan on THF", "thiophene on tetrahydrothiophene",
         "benzofuran on 2,3-dihydrobenzofuran"])
def test_an_aromatic_five_ring_on_its_saturated_deposit_is_never_verified(
        tmp_path: Path, file: str, deposit: str) -> None:
    """A five-ring's atoms have two heavy neighbours, whose angle a small
    ring sets, so the long ring bonds' note was dropped and the ligand
    verified."""
    for seed in (1, 2):
        assert _never_verified(tmp_path, _built(file, 100 + seed),
                               _held(tmp_path, deposit, seed)), seed


def _ring_read(smiles: str) -> bool:
    """Whether the five-ring of ``smiles``, built, reads puckered."""
    from fastmdxplora.setup.ligand import _Chemistry, _puckered
    from fastmdxplora.validation.ligand_mapping import _Molecule

    mol = _Molecule(_built(smiles, 1))
    heavy = [i for i, atom in enumerate(mol.atoms) if atom.atomic_number > 1]
    chemistry = _Chemistry(mol, heavy, mol.conformers[0].m_as("nanometer"))
    k = next(i for i in range(chemistry.n) if chemistry.mol.GetRingInfo().IsAtomInRingOfSize(i, 5))
    return _puckered(chemistry, k, list(range(chemistry.n)), chemistry.positions)


@pytest.mark.parametrize("smiles, puckered", [
    ("c1cc[nH]c1", False), ("c1ccoc1", False), ("c1ccsc1", False),
    ("c1ccc2[nH]ccc2c1", False), ("Cc1sc[n+](C)c1", False),
    ("C1CCCC1", True), ("C1CCOC1", True), ("C1Cc2ccccc2N1", True)],
    ids=["pyrrole", "furan", "thiophene", "indole", "a thiazolium", "cyclopentane", "THF",
         "indoline"])
def test_a_flat_ring_reads_flat_and_a_reduced_one_reduced(smiles: str, puckered: bool) -> None:
    """The torsion was taken with one bond reversed, so a flat ring's 0
    degrees read 180 and every five-ring was "puckered". Indoline's ring,
    built shallow (its largest torsion 19 degrees), reads reduced by a
    CH2's angle."""
    from fastmdxplora.setup import ligand

    assert _ring_read(smiles) is puckered or (
        puckered and hasattr(ligand, "_small_ring_reduced") and _reduced(smiles))


def _reduced(smiles: str) -> bool:
    """Whether some atom of the five-ring of ``smiles``, built, reads as a
    reduced ring's."""
    from fastmdxplora.setup.ligand import _Chemistry, _small_ring_reduced
    from fastmdxplora.validation.ligand_mapping import _Molecule

    mol = _Molecule(_built(smiles, 1))
    heavy = [i for i, atom in enumerate(mol.atoms) if atom.atomic_number > 1]
    chemistry = _Chemistry(mol, heavy, mol.conformers[0].m_as("nanometer"))
    order = list(range(chemistry.n))
    return any(_small_ring_reduced(chemistry, k, order, chemistry.positions)
               for k in range(chemistry.n) if chemistry.mol.GetRingInfo().IsAtomInRingOfSize(k, 5))


@pytest.mark.parametrize("code, deposit", [
    ("IMN", "3fo7_IMN.pdb"), ("IMN", "2bxk_IMN.pdb"), ("STU", "6c9f_STU.pdb"),
    ("TPP", "1x7y_TPP.pdb")],
    ids=["3FO7's indomethacin", "2BXK's indomethacin", "6C9F's staurosporine",
         "1X7Y's thiamine diphosphate"])
def test_a_flat_five_ring_is_never_another_redox_form(
        tmp_path: Path, code: str, deposit: str) -> None:
    """Every five-ring read puckered, so two long bonds at a flat ring atom
    were "the file has it aromatic (the other redox or tautomeric form)"."""
    _, placement = _study(tmp_path, _file(code), deposit, code)
    assert "the other redox or tautomeric form" not in placement["returned"]


@pytest.mark.parametrize("noise", [0.035, 0.05])
def test_noise_alone_never_skips_a_bent_c_c_s_e_or_z(tmp_path: Path, noise: float) -> None:
    """cis-Cinnamic acid's file on a trans copy this rough: its C=C, a carbon
    bent under 114 degrees and its length past the midpoint by noise alone,
    was "laid as a single bond", placed and simulated trans."""
    from tests.test_a_ligand_is_placed_from_rough_and_noisy_inputs import _deposit

    for seed in range(1, 41):
        held = _built("OC(=O)/C=C/c1ccccc1", 200 + seed % 3)
        noisy = _deposit(tmp_path / f"noisy{seed}.pdb", held, list(range(held.GetNumAtoms())),
                         noise, seed)
        with pytest.raises(LigandError):
            _place(tmp_path, _built("OC(=O)/C=C\\c1ccccc1", 100 + seed % 3), noisy)


def _n_laid_long(smiles: str, seed: int, length: float) -> np.ndarray:
    """``smiles`` built, its aromatic five-ring N of three heavy neighbours
    moved out along its bisector, with its third neighbour, until both its
    ring bonds are ``length`` (A) or longer, as 2BXK's N-acyl indole lays
    them."""
    held = _built(smiles, 200 + seed)
    xyz = held.GetConformer().GetPositions()
    n = held.GetSubstructMatches(Chem.MolFromSmarts("[n;X3]"))[0][0]
    ring = next(r for r in held.GetRingInfo().AtomRings() if n in r and len(r) == 5
                and all(held.GetAtomWithIdx(a).GetIsAromatic() for a in r))
    near = [a.GetIdx() for a in held.GetAtomWithIdx(n).GetNeighbors() if a.GetIdx() in ring]
    out = [a.GetIdx() for a in held.GetAtomWithIdx(n).GetNeighbors() if a.GetIdx() not in ring]
    u = xyz[n] - (xyz[near[0]] + xyz[near[1]]) / 2.0
    u = u / np.linalg.norm(u)
    while min(np.linalg.norm(xyz[n] - xyz[k]) for k in near) < length:
        for k in [n, *out]:
            xyz[k] = xyz[k] + 0.002 * u
    return xyz


@pytest.mark.parametrize("smiles", ["C1Cc2cccn2C1", "C1Cc2nccn2C1"],
                         ids=["dihydropyrrolizine", "dihydropyrroloimidazole"])
def test_a_fused_saturated_ring_does_not_make_an_aromatic_one_reduced(
        tmp_path: Path, smiles: str) -> None:
    """The bridgehead N was read by the saturated ring's pucker, so its two
    long aromatic bonds were "the other redox or tautomeric form" of the
    right file."""
    for seed in (1, 2):
        _, _, line = _place(tmp_path, _built(smiles, 100 + seed),
                            _held(tmp_path, smiles, seed, _n_laid_long(smiles, seed, 1.46)))
        assert "the file has it aromatic" not in line, (seed, line)
