"""A prepared ligand is checked against its file and the structure's pose.

Preparation takes a ligand's chemistry from its SDF or MOL2 and, on a
complex, its pose from the structure, matching the file's heavy atoms to the
structure's residue by element and bond, and records the mapping with the
file's SHA-256. `check_ligand_mappings` asks of a study already prepared,
reading only, whether the prepared system's own ligand atoms are the file's
molecule on the structure's pose: "verified" was today's matcher asked
again, so a study prepared by file order, or from a file since changed, was
said verified. And called as documented, it read a structure linked out of
the study.

The studies here are laid out as setup lays them out, the prepared topology
written from the ligand as placed, since the OpenFF toolkit that builds the
whole system is not installed where these run.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("rdkit")
pytest.importorskip("mdtraj")

from rdkit import Chem  # noqa: E402

from fastmdxplora.setup.ligand import pose_by_policy, pose_from_structure  # noqa: E402
from fastmdxplora.validation.ligand_mapping import (  # noqa: E402
    CHANGED,
    DISAGREES,
    FILE_POSE,
    NOT_READ,
    NOT_VERIFIABLE,
    REFUSED,
    VERIFIED,
    check_ligand_mappings,
    molecule_from_file,
)
from tests.test_a_pose_is_matched_atom_for_atom import (  # noqa: E402
    PARACETAMOL,
    _deposit,
    _Supplied,
)

BENZAMIDINE = "NC(=N)c1ccccc1"


def _topology(path: Path, molecule, xyz_nm: np.ndarray, resname: str) -> None:
    """The prepared system's topology, as far as the ligand: its atoms in the
    file's order at ``xyz_nm``."""
    lines = []
    for serial, (atom, (x, y, z)) in enumerate(zip(molecule.rdkit.GetAtoms(),
                                                   xyz_nm * 10.0), start=1):
        symbol = atom.GetSymbol()
        lines.append(
            f"HETATM{serial:5d}  {symbol}{serial:<2d} {resname:>3s} A   1    "
            f"{x:8.3f}{y:8.3f}{z:8.3f}  1.00  0.00          {symbol:>2s}")
    lines.append("END")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _study(tmp_path: Path, molecule: _Supplied, order: list[int], *,
           resname: str = "BEN", positions_of: _Supplied | None = None,
           pose: str = "auto", sdf: str | None = None, recorded: bool = True,
           prepared: str = "placed") -> Path:
    """A prepared study: its setup record, the structure as it arrived with
    the ligand's heavy atoms in ``order``, the ligand file, and the prepared
    topology (``placed`` as setup places it, ``by order`` as releases
    before 2.5.8 did, or ``none``)."""
    setup = tmp_path / "study" / "setup"
    (setup / "ligands").mkdir(parents=True)
    structure = _deposit(setup, molecule, order, resname=resname,
                         positions_of=positions_of)
    ligand = setup / "ligands" / f"{resname}.sdf"
    ligand.write_text(sdf or Chem.MolToMolBlock(molecule.rdkit), encoding="utf-8")
    placement: dict = {}
    poses = None
    if prepared != "none":
        loaded = molecule_from_file(ligand)
        if prepared == "placed":
            placed, _ = pose_by_policy(loaded, structure, resname, policy=pose,
                                       record=placement)
            xyz = placed.conformers[0].m_as("nanometer")
        else:
            import mdtraj as md

            xyz = loaded.conformers[0].m_as("nanometer").copy()
            heavy = [i for i, a in enumerate(loaded.atoms) if a.atomic_number > 1]
            xyz[heavy] = md.load(str(structure)).xyz[0][:len(heavy)]
        _topology(setup / "topology.pdb", molecule, xyz, resname)
    if recorded and placement:
        poses = [{"name": resname, "copy": 1, "file": str(ligand),
                  "sha256": hashlib.sha256(ligand.read_bytes()).hexdigest(),
                  **placement}]
    (setup / "setup_parameters.json").write_text(json.dumps({
        "phase": "setup",
        "parameters": {"ligand_pose": pose},
        "resolved_forcefield": {"ligand": {"files": [str(ligand)], "name": resname}},
        **({"ligand_poses": poses} if poses else {}),
    }), encoding="utf-8")
    return tmp_path / "study"


def _shuffled(molecule: _Supplied, seed: int = 5) -> list[int]:
    order = [int(i) for i in np.random.default_rng(seed).permutation(molecule.heavy)]
    assert order != molecule.heavy
    return order


class TestAPreparedLigand:

    def test_on_its_pose_under_the_recorded_mapping_is_verified(
            self, tmp_path: Path) -> None:
        supplied = _Supplied(BENZAMIDINE)
        [found] = check_ligand_mappings(_study(tmp_path, supplied, _shuffled(supplied)))
        assert found["status"] == VERIFIED and found["code"] is None
        assert found["mapping"] == "recorded" and found["deviation_nm"] <= 0.002
        assert "SHA-256" in found["said"]

    def test_from_a_file_changed_since_is_said_so(self, tmp_path: Path) -> None:
        supplied = _Supplied(BENZAMIDINE)
        study = _study(tmp_path, supplied, _shuffled(supplied))
        ligand = study / "setup" / "ligands" / "BEN.sdf"
        ligand.write_text(ligand.read_text(encoding="utf-8").replace("RDKit", "edited"),
                          encoding="utf-8")
        [found] = check_ligand_mappings(study)
        assert found["status"] == CHANGED

    def test_placed_by_file_order_before_the_mapping_was_recorded_does_not_agree(
            self, tmp_path: Path) -> None:
        """A study prepared by an earlier release, which put the file's
        heavy atoms on the structure's in the order each lists them."""
        supplied = _Supplied(BENZAMIDINE)
        study = _study(tmp_path, supplied, _shuffled(supplied), recorded=False,
                       prepared="by order")
        [found] = check_ligand_mappings(study)
        assert found["status"] == DISAGREES

    def test_placed_right_before_the_mapping_was_recorded_is_checked_again(
            self, tmp_path: Path) -> None:
        supplied = _Supplied(BENZAMIDINE)
        study = _study(tmp_path, supplied, _shuffled(supplied), recorded=False)
        [found] = check_ligand_mappings(study)
        assert found["status"] == VERIFIED and found["mapping"] == "found again"
        assert "before the file's SHA-256 was recorded" in found["said"]

    def test_without_its_prepared_coordinates_is_not_verifiable(
            self, tmp_path: Path) -> None:
        supplied = _Supplied(BENZAMIDINE)
        study = _study(tmp_path, supplied, _shuffled(supplied), recorded=False,
                       prepared="none")
        [found] = check_ligand_mappings(study)
        assert found["status"] == NOT_VERIFIABLE
        assert found["said"].startswith(
            "not verifiable: prepared before the mapping was recorded")


class TestAReorderedFile:

    def test_gives_the_same_placed_ligand(self, tmp_path: Path) -> None:
        """The SDF with its atoms listed in another order, at the same
        coordinates, places every atom where the first order placed it.
        Placed, not prepared whole: the OpenFF toolkit is not installed
        where these run."""
        supplied = _Supplied(BENZAMIDINE)
        structure = _deposit(tmp_path, supplied, _shuffled(supplied), resname="BEN")
        n = supplied.rdkit.GetNumAtoms()
        renumbered = [int(i) for i in np.random.default_rng(9).permutation(n)]
        reordered = Chem.RenumberAtoms(supplied.rdkit, renumbered)
        first = tmp_path / "first.sdf"
        second = tmp_path / "second.sdf"
        first.write_text(Chem.MolToMolBlock(supplied.rdkit), encoding="utf-8")
        second.write_text(Chem.MolToMolBlock(reordered), encoding="utf-8")

        placed = []
        for path in (first, second):
            molecule, said = pose_from_structure(molecule_from_file(path), structure,
                                                 "BEN", required=True)
            assert said and "placed BEN" in said
            placed.append(molecule.conformers[0].m_as("nanometer"))
        # Atom k of the second file is atom renumbered[k] of the first.
        assert np.allclose(placed[1], placed[0][renumbered], atol=2e-4)


def test_a_symmetric_ligand_is_verified(tmp_path: Path) -> None:
    """Benzamidine's ring and amidine each have a mirror: more than one
    match is right, and any one of them is a complete mapping."""
    supplied = _Supplied(BENZAMIDINE)
    [found] = check_ligand_mappings(_study(tmp_path, supplied,
                                           list(reversed(supplied.heavy))))
    assert found["status"] == VERIFIED and found["copy"] == 1


def test_an_isomer_of_equal_count_is_refused_with_its_code(tmp_path: Path) -> None:
    """3-acetamidophenol in the structure, paracetamol in the file: the same
    heavy atoms, other bonds."""
    supplied = _Supplied(PARACETAMOL)
    isomer = _Supplied("CC(=O)Nc1cccc(O)c1")
    study = _study(tmp_path, supplied, isomer.heavy, resname="TYL",
                   positions_of=isomer, prepared="none")
    [found] = check_ligand_mappings(study)
    assert found["status"] == REFUSED
    assert found["code"] == "setup.ligand.atoms_unmatched"
    assert "Not matched" in found["said"]


def test_a_pose_from_the_file_has_no_mapping_to_check(tmp_path: Path) -> None:
    supplied = _Supplied(BENZAMIDINE)
    study = _study(tmp_path, supplied, supplied.heavy, pose="file")
    [found] = check_ligand_mappings(study)
    assert found["status"] == FILE_POSE


def test_a_missing_ligand_file_is_not_read(tmp_path: Path) -> None:
    supplied = _Supplied(BENZAMIDINE)
    study = _study(tmp_path, supplied, supplied.heavy)
    (study / "setup" / "ligands" / "BEN.sdf").unlink()
    [found] = check_ligand_mappings(study)
    assert found["status"] == NOT_READ


def test_a_file_that_will_not_read_is_refused_as_unreadable(tmp_path: Path) -> None:
    supplied = _Supplied(BENZAMIDINE)
    study = _study(tmp_path, supplied, supplied.heavy, sdf="not a molecule\n",
                   prepared="none")
    [found] = check_ligand_mappings(study)
    assert found["status"] == REFUSED and found["code"] == "setup.ligand.unreadable"


def test_a_study_without_a_ligand_has_nothing_to_check(tmp_path: Path) -> None:
    (tmp_path / "setup").mkdir()
    (tmp_path / "setup" / "setup_parameters.json").write_text(
        json.dumps({"resolved_forcefield": {"name": "amber14"}}), encoding="utf-8")
    assert check_ligand_mappings(tmp_path) == []


def test_a_structure_linked_out_of_the_study_is_not_read(tmp_path: Path) -> None:
    """Called as documented, with no read held: the check holds its own, so
    a structure outside the study is not read, and not called absent."""
    supplied = _Supplied(BENZAMIDINE)
    study = _study(tmp_path / "in", supplied, _shuffled(supplied))
    outside = tmp_path / "elsewhere.pdb"
    structure = study / "setup" / "input.pdb"
    outside.write_bytes(structure.read_bytes())
    structure.unlink()
    structure.symlink_to(outside)
    [found] = check_ligand_mappings(study)
    assert found["status"] == NOT_READ
    assert "outside the study" in found["said"]


def test_a_run_simulated_from_another_study_s_system_is_checked_there(
        tmp_path: Path) -> None:
    supplied = _Supplied(BENZAMIDINE)
    prepared = _study(tmp_path / "prepared", supplied, _shuffled(supplied))
    for name in ("system.xml", "state.xml"):
        (prepared / "setup" / name).write_text("<System/>\n", encoding="utf-8")
    run = tmp_path / "run"
    (run / "simulation").mkdir(parents=True)
    (run / "simulation" / "simulation_parameters.json").write_text(json.dumps(
        {"parameters": {"setup_from": str(prepared)}}), encoding="utf-8")
    [found] = check_ligand_mappings(run)
    assert found["status"] == VERIFIED


def test_the_check_writes_nothing(tmp_path: Path) -> None:
    supplied = _Supplied(BENZAMIDINE)
    study = _study(tmp_path, supplied, _shuffled(supplied))

    def contents() -> dict[str, bytes]:
        return {p.relative_to(study).as_posix(): p.read_bytes()
                for p in sorted(study.rglob("*")) if p.is_file()}

    before = contents()
    check_ligand_mappings(study)
    assert contents() == before


def test_a_ligand_file_outside_the_study_is_read_from_the_copy_setup_kept(
        tmp_path: Path) -> None:
    """The usual case: the file the study was given lies outside it. Setup
    keeps a copy in `setup/ligands/` and records it, and the check, holding
    its reads to the study, reads that."""
    from fastmdxplora.setup.prepare import _kept_in_the_study

    supplied = _Supplied(BENZAMIDINE)
    study = _study(tmp_path, supplied, _shuffled(supplied))
    setup = study / "setup"
    given = tmp_path / "mine" / "ben.sdf"
    given.parent.mkdir()
    (setup / "ligands" / "BEN.sdf").rename(given)
    record = json.loads((setup / "setup_parameters.json").read_text(encoding="utf-8"))
    kept = _kept_in_the_study(given, setup)
    assert kept["file"] == "ligands/ben.sdf" and kept["source"] == str(given)
    record["resolved_forcefield"]["ligand"]["files"] = [str(given)]
    record["ligand_poses"][0].update(kept)
    (setup / "setup_parameters.json").write_text(json.dumps(record), encoding="utf-8")
    [found] = check_ligand_mappings(study)
    assert found["status"] == VERIFIED and found["mapping"] == "recorded"


def test_a_name_kept_already_by_another_file_takes_another(tmp_path: Path) -> None:
    from fastmdxplora.setup.prepare import _kept_in_the_study

    first, second = tmp_path / "a" / "LIG.sdf", tmp_path / "b" / "LIG.sdf"
    for path, text in ((first, "one\n"), (second, "two\n")):
        path.parent.mkdir()
        path.write_text(text, encoding="utf-8")
    setup = tmp_path / "setup"
    assert _kept_in_the_study(first, setup)["file"] == "ligands/LIG.sdf"
    assert _kept_in_the_study(second, setup)["file"] == "ligands/LIG-2.sdf"
    assert _kept_in_the_study(first, setup)["file"] == "ligands/LIG.sdf"


def test_without_its_stereochemistry_read_it_is_not_verifiable(
        tmp_path: Path, monkeypatch) -> None:
    """Where RDKit cannot read the molecule's chemistry, its stereochemistry
    was not checked, and the ligand is not called verified."""
    from fastmdxplora.setup import ligand

    supplied = _Supplied(BENZAMIDINE)
    study = _study(tmp_path, supplied, _shuffled(supplied))
    monkeypatch.setattr(ligand, "_heavy_rdkit", lambda *_: None)
    [found] = check_ligand_mappings(study)
    assert found["status"] == NOT_VERIFIABLE
    assert found["said"].startswith("not verifiable: stereochemistry not checked")


def test_a_ketone_placed_on_an_alcohol_does_not_agree(tmp_path: Path) -> None:
    """Prepared before the mapping was recorded, with the C=O and the C-OH
    of 4-hydroxycyclohexanone on each other's positions: bonded as the file
    by distance, and not at its bonds' lengths."""
    smiles = "OC1CCC(=O)CC1"
    supplied = _Supplied(smiles, seed=101)
    study = _study(tmp_path, supplied, supplied.heavy, resname="LIG", recorded=False,
                   prepared="none")
    mol = Chem.MolFromSmiles(smiles)
    swap = next(m for m in mol.GetSubstructMatches(
        Chem.MolFromSmarts("[#8]~[#6]1~[#6]~[#6]~[#6](~[#8])~[#6]~[#6]1"), uniquify=False)
        if mol.GetAtomWithIdx(m[0]).GetDegree() == 1 and m[0] != 0)
    import mdtraj as md

    crystal = md.load(str(study / "setup" / "input.pdb")).xyz[0]
    canonical = mol.GetSubstructMatch(Chem.MolFromSmarts(
        "[#8]~[#6]1~[#6]~[#6]~[#6](~[#8])~[#6]~[#6]1"))
    xyz = supplied.conformers[0].m_as("nanometer").copy()
    for a, b in zip(canonical, swap):
        xyz[a] = crystal[b]
    _topology(study / "setup" / "topology.pdb", supplied, xyz, "LIG")
    [found] = check_ligand_mappings(study)
    assert found["status"] == DISAGREES and "bond orders say" in found["said"]


def test_a_flat_file_stating_no_isomer_is_never_said_to_agree(tmp_path: Path) -> None:
    """A 2D glucose without wedges on a galactose deposit: the file states
    no isomer, so none can agree. The check held the prepared ligand to the
    pose it had just placed, and said "5 stereocentres ... from the file's
    3D coordinates, agree"."""
    from rdkit.Chem import AllChem

    glucose = "OC[C@H]1O[C@H](O)[C@H](O)[C@@H](O)[C@@H]1O"
    galactose = "OC[C@H]1O[C@H](O)[C@H](O)[C@@H](O)[C@H]1O"
    drawn = Chem.AddHs(Chem.MolFromSmiles(Chem.MolToSmiles(
        Chem.MolFromSmiles(glucose), isomericSmiles=False)))
    AllChem.Compute2DCoords(drawn)
    supplied = _Supplied(glucose)
    study = _study(tmp_path, supplied, supplied.heavy, resname="GLC",
                   positions_of=_Supplied(galactose), sdf=Chem.MolToMolBlock(drawn))
    [found] = check_ligand_mappings(study)
    assert found["status"] == VERIFIED
    assert "stereochemistry not stated in the file" in found["said"]
    assert "agree" not in found["said"] and "3D coordinates" not in found["said"]


def test_a_hydrogen_inside_its_own_bonds_does_not_agree(tmp_path: Path) -> None:
    """Cyclohexylbenzene prepared with its methine hydrogen put through its
    carbon, every H-C-Y about 72 degrees: no closer than 1.0 A to an atom
    three bonds away, it was verified with only an angle number."""
    supplied = _Supplied("C1CCC(CC1)c1ccccc1")
    study = _study(tmp_path, supplied, _shuffled(supplied), resname="LIG")
    import mdtraj as md

    topology = study / "setup" / "topology.pdb"
    xyz = md.load(str(topology)).xyz[0].astype(float)
    methine = next(a for a in supplied.rdkit.GetAtoms() if a.GetAtomicNum() == 6
                   and not a.GetIsAromatic() and a.GetDegree() == 4
                   and sum(n.GetAtomicNum() == 1 for n in a.GetNeighbors()) == 1)
    hydrogen = next(n.GetIdx() for n in methine.GetNeighbors() if n.GetAtomicNum() == 1)
    centre = methine.GetIdx()
    xyz[hydrogen] = xyz[centre] - (xyz[hydrogen] - xyz[centre])
    _topology(topology, supplied, xyz, "LIG")
    [found] = check_ligand_mappings(study)
    assert found["status"] == DISAGREES and "inside its own atom's bonds" in found["said"]


def test_a_recorded_mapping_that_puts_a_ketone_on_an_alcohol_does_not_agree(
        tmp_path: Path) -> None:
    """The mapping recorded with the C=O and the C-OH of
    4-hydroxycyclohexanone given each other's structure atoms, the prepared
    atoms on them: the recorded mapping was held to positions only."""
    smiles = "OC1CCC(=O)CC1"
    supplied = _Supplied(smiles, seed=101)
    study = _study(tmp_path, supplied, supplied.heavy, resname="LIG")
    record_path = study / "setup" / "setup_parameters.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    mol = Chem.MolFromSmiles(smiles)
    pattern = Chem.MolFromSmarts("[#8]~[#6]1~[#6]~[#6]~[#6](~[#8])~[#6]~[#6]1")
    canonical = mol.GetSubstructMatch(pattern)
    swap = next(m for m in mol.GetSubstructMatches(pattern, uniquify=False)
                if m[0] != canonical[0])
    given = {m["file_atom"]: m for m in record["ligand_poses"][0]["mapping"]}
    structure = {a: dict(given[b]) for a, b in zip(canonical, swap)}
    for a, entry in structure.items():
        given[a] = {**entry, "file_atom": a}
    record["ligand_poses"][0]["mapping"] = [given[k] for k in sorted(given)]
    record_path.write_text(json.dumps(record), encoding="utf-8")
    import mdtraj as md

    crystal = md.load(str(study / "setup" / "input.pdb")).xyz[0]
    xyz = md.load(str(study / "setup" / "topology.pdb")).xyz[0].astype(float)
    for a in canonical:
        xyz[a] = crystal[given[a]["structure_atom"]]
    _topology(study / "setup" / "topology.pdb", supplied, xyz, "LIG")
    [found] = check_ligand_mappings(study)
    assert found["mapping"] == "recorded"
    assert found["status"] == DISAGREES and "bond orders say" in found["said"]
