"""A coordinated ion stays whether or not the ligand's chemistry is a file.

Under `heterogens: auto` the structure's heterogens were read only when no
ligand file was given. Given one -- offline, or whenever an SDF is at hand --
nothing was classified, PDBFixer stripped every heterogen, and the ion went
without a word, because the removals PDBFixer reports leave ions out. Trypsin
with a benzamidine SDF lost the calcium its calcium-binding loop holds, while
the same structure without the SDF kept it.

The fixture is that shape at small scale: the tripeptide the neighbouring
test prepares, a calcium 2.4 A from a backbone oxygen and named in a LINK
record, a benzamidine beside it, a sodium out in the solvent, and the
benzamidine's chemistry as an SDF written by RDKit. PDBFixer runs for real;
solvation and parameterisation are replaced, as next door, since they read
the prepared structure and add nothing to the question.
"""

from __future__ import annotations

import io
import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fastmdxplora.setup import prepare as _prepare
from fastmdxplora.setup.heterogens import AmbiguousStructureError
from fastmdxplora.setup.pipeline import run as setup_run
from tests.test_a_coordinated_ion_reaches_the_prepared_system import (
    _at,
    _atom,
    _residue,
    _write,
    _zinc,
)

#: 2.4 A from alanine 1's carbonyl oxygen, along its C=O bond.
ON_THE_CARBONYL = (-0.227, 4.281, 0.000)
IN_THE_SOLVENT = (30.0, 30.0, 30.0)
ALSO_IN_THE_SOLVENT = (-30.0, 30.0, -30.0)

#: The coordination, as the deposition writes it.
LINK = ("LINK         O   ALA A   1                CA    CA A 480     "
        "1555   1555  2.40")


def _calcium(serial=22, where=ON_THE_CARBONYL, seq=480):
    return _zinc(serial, where, seq=seq, name="CA", element="CA")


def _sodium(serial=23):
    return _zinc(serial, IN_THE_SOLVENT, seq=482, name="NA", element="NA")


def _benzamidine():
    """Its chemistry as a molecule, and its heavy atoms as the structure
    has them: the same conformer, moved beside the peptide."""
    Chem = pytest.importorskip("rdkit.Chem")
    from rdkit.Chem import AllChem

    molecule = Chem.AddHs(Chem.MolFromSmiles("NC(=N)c1ccccc1"))
    AllChem.EmbedMolecule(molecule, randomSeed=7)
    conformer = molecule.GetConformer()
    lines, counts = [], {}
    for atom in molecule.GetAtoms():
        if atom.GetAtomicNum() == 1:
            continue
        element = atom.GetSymbol()
        counts[element] = counts.get(element, 0) + 1
        at = conformer.GetAtomPosition(atom.GetIdx())
        lines.append(_atom(
            "HETATM", 30 + len(lines), f"{element}{counts[element]}", " ",
            "BEN", "A", 490, at.x + 4.0, at.y + 12.0, at.z, element=element))
    return molecule, lines


def _setup(where: Path, extra: list[str], *, sdf: bool = True, **options):
    """Run setup to the prepared structure, with the ligand's SDF or not.

    Returns the prepared structure's lines, the setup record, what
    preparation was asked to add, and the setup directory.
    """
    pytest.importorskip("pdbfixer")
    where.mkdir()
    if sdf:
        from rdkit import Chem

        molecule, _ = _benzamidine()
        path = where / "BEN.sdf"
        Chem.MolToMolFile(molecule, str(path))
        options = {"ligand": str(path), "ligand_name": "BEN", **options}
    orchestrator = MagicMock()
    orchestrator.system = str(_write(where, extra))
    orchestrator._presenter = None
    setup_dir = where / "setup"
    setup_dir.mkdir()
    with patch.object(_prepare, "prepare_system", return_value={}) as solvate:
        setup_run(orchestrator=orchestrator, output_dir=setup_dir, **options)
    prepared = (setup_dir / "prepared.pdb").read_text(encoding="utf-8")
    record = json.loads(
        (setup_dir / "setup_parameters.json").read_text(encoding="utf-8"))
    return prepared.splitlines(), record, solvate.call_args.kwargs, setup_dir


def _trypsin_like() -> list[str]:
    return [LINK, _calcium(), _sodium(), *_benzamidine()[1]]


class TestTheCalciumStaysBesideASuppliedLigand:

    def test_it_is_in_the_prepared_structure(self, tmp_path: Path) -> None:
        """The whole failure: the ligand from a file cost the calcium."""
        prepared, _, _, _ = _setup(tmp_path / "run", _trypsin_like())
        calcium = _residue(prepared, "CA")
        assert len(calcium) == 1
        assert _at(calcium[0]) == ON_THE_CARBONYL

    def test_the_setup_record_names_it(self, tmp_path: Path) -> None:
        _, record, _, _ = _setup(tmp_path / "run", _trypsin_like())
        assert record["parameters"]["_retained_ions"] == ["CA A480"]

    def test_the_sodium_in_the_solvent_is_not_kept(
            self, tmp_path: Path) -> None:
        prepared, _, _, _ = _setup(tmp_path / "run", _trypsin_like())
        assert not _residue(prepared, "NA")

    def test_water_asked_for_is_kept_beside_it(self, tmp_path: Path) -> None:
        """The filtered structure is all PDBFixer keeps, so water asked for
        has to be in it, as it is under the auto policy."""
        water = _atom("HETATM", 25, "O", " ", "HOH", "A", 501, 12.0, 0.0, 0.0,
                      element="O")
        prepared, _, _, _ = _setup(tmp_path / "run", _trypsin_like() + [water],
                                   keep_water=True)
        assert _residue(prepared, "CA")
        assert _residue(prepared, "HOH")

    def test_the_ligand_comes_from_its_file_once(self, tmp_path: Path) -> None:
        """Kept in the filtered structure as well, it would be added twice.
        Its pose is read from input.pdb, which is not what is filtered."""
        prepared, _, asked, setup_dir = _setup(tmp_path / "run",
                                               _trypsin_like())
        assert not _residue(prepared, "BEN")
        assert asked["ligand"] == str(tmp_path / "run" / "BEN.sdf")
        posed = _residue(
            (setup_dir / "input.pdb").read_text(encoding="utf-8").splitlines(),
            "BEN")
        assert [_at(line) for line in posed] == \
            [_at(line) for line in _benzamidine()[1]]

    def test_the_outcome_is_the_one_without_the_file(
            self, tmp_path: Path) -> None:
        """The same calcium, kept the same way, as when the structure alone
        decides. Offline, that path has to leave the benzamidine out: its
        chemistry could only be fetched for a PDB identifier."""
        with_file, with_record, _, _ = _setup(tmp_path / "file",
                                              _trypsin_like())
        without, without_record, _, _ = _setup(
            tmp_path / "structure", [LINK, _calcium(), _sodium()], sdf=False)
        assert _residue(with_file, "CA") == _residue(without, "CA")
        assert with_record["parameters"]["_retained_ions"] \
            == without_record["parameters"]["_retained_ions"]

    def test_what_the_filter_left_out_is_still_reported(
            self, tmp_path: Path) -> None:
        """PDBFixer keeps all of a filtered structure and reports nothing
        removed, so the report it made on this path is made for it."""
        glycerol = [_atom("HETATM", 50 + i, name, " ", "GOL", "A", 491,
                          20.0 + 1.4 * i, 0.0, 0.0, element=name[0])
                    for i, name in enumerate(("C1", "O1", "C2", "O2", "C3",
                                              "O3"))]
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        package = logging.getLogger("fastmdx")
        previous = package.level
        package.addHandler(handler)
        package.setLevel(logging.INFO)
        try:
            prepared, _, _, _ = _setup(tmp_path / "run",
                                       _trypsin_like() + glycerol)
        finally:
            package.removeHandler(handler)
            package.setLevel(previous)
        said = stream.getvalue()
        assert _residue(prepared, "CA")
        assert not _residue(prepared, "GOL")
        assert "Removed heterogens: GOL" in said
        assert "Stripped BEN from the structure" in said


class TestAnExplicitPolicyStillDecides:

    def test_drop_removes_the_calcium(self, tmp_path: Path) -> None:
        prepared, record, _, _ = _setup(tmp_path / "run", _trypsin_like(),
                                        heterogens="drop")
        assert not _residue(prepared, "CA")
        assert "_retained_ions" not in record["parameters"]


class TestAnUndeterminedIonIsRefusedEitherWay:
    """One calcium on the carbonyl, one in the solvent: keeping or dropping
    the component as a whole is wrong either way, file or no file."""

    @staticmethod
    def _refusal(where: Path, *, sdf: bool) -> AmbiguousStructureError:
        mixed = _trypsin_like() + [_calcium(24, ALSO_IN_THE_SOLVENT, 483)]
        with pytest.raises(AmbiguousStructureError) as caught:
            _setup(where, mixed, sdf=sdf)
        return caught.value

    def test_with_the_file_as_without_it(self, tmp_path: Path) -> None:
        with_file = self._refusal(tmp_path / "file", sdf=True)
        without = self._refusal(tmp_path / "structure", sdf=False)
        assert with_file.code == without.code == "setup.structure.undetermined"
        assert "CA: 1 of 2 copies are coordinated" in str(with_file)
        assert str(with_file) == str(without)
