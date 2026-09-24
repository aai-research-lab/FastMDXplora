"""A zinc the classifier keeps is in the system that gets simulated.

The log said "Keeping ZN as an ion" and the prepared structure had no zinc.
Keeping it in place was arranged only after the point where a structure with
nothing else to simulate had already returned, so PDBFixer ran with its
heterogens stripped and took the zinc with them. Beside a ligand the loss was
silent, because the zinc counted among the components already explained.

The fixture is a tripeptide whose histidine holds a zinc at 2.1 A from NE2,
prepared by the real PDBFixer: the fault lived between the classifier and
PDBFixer, so a test that stops at the classifier cannot see it. Solvation and
parameterisation are replaced, since they read the prepared structure and
add nothing to the question.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fastmdxplora.setup import prepare as _prepare
from fastmdxplora.setup.heterogens import Action, resolve
from fastmdxplora.setup.pipeline import _auto_ligands, run as setup_run


def _atom(record, serial, name, altloc, resname, chain, seq, x, y, z,
          occupancy=1.00, element="C"):
    return (
        f"{record:<6}{serial:>5} {name:<4}{altloc:1}{resname:>3} "
        f"{chain:1}{seq:>4}    {x:8.3f}{y:8.3f}{z:8.3f}"
        f"{occupancy:6.2f}  0.00          {element:>2}"
    )


# Ala-His-Ala, heavy atoms only; PDBFixer adds the hydrogens.
PEPTIDE = """\
ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00  0.00           N
ATOM      2  CA  ALA A   1       1.458   0.000   0.000  1.00  0.00           C
ATOM      3  C   ALA A   1       2.009   1.420   0.000  1.00  0.00           C
ATOM      4  O   ALA A   1       1.251   2.390   0.000  1.00  0.00           O
ATOM      5  CB  ALA A   1       1.914  -0.685  -1.286  1.00  0.00           C
ATOM      6  N   HIS A   2       3.332   1.549   0.000  1.00  0.00           N
ATOM      7  CA  HIS A   2       3.972   2.849   0.000  1.00  0.00           C
ATOM      8  C   HIS A   2       5.486   2.705   0.000  1.00  0.00           C
ATOM      9  O   HIS A   2       6.008   1.593   0.000  1.00  0.00           O
ATOM     10  CB  HIS A   2       4.074   3.237   1.493  1.00  0.00           C
ATOM     11  CG  HIS A   2       2.790   3.399   2.300  1.00  0.00           C
ATOM     12  ND1 HIS A   2       2.003   2.371   2.796  1.00  0.00           N
ATOM     13  CE1 HIS A   2       0.960   2.918   3.454  1.00  0.00           C
ATOM     14  NE2 HIS A   2       1.064   4.255   3.389  1.00  0.00           N
ATOM     15  CD2 HIS A   2       2.204   4.585   2.670  1.00  0.00           C
ATOM     16  N   ALA A   3       6.171   3.845   0.000  1.00  0.00           N
ATOM     17  CA  ALA A   3       7.623   3.845   0.000  1.00  0.00           C
ATOM     18  C   ALA A   3       8.174   5.265   0.000  1.00  0.00           C
ATOM     19  O   ALA A   3       7.416   6.235   0.000  1.00  0.00           O
ATOM     20  CB  ALA A   3       7.917   3.820  -1.497  1.00  0.00           C
ATOM     21  OXT ALA A   3       9.431   5.352   0.003  1.00  0.00           O
""".splitlines()

#: 2.1 A from the histidine's NE2, along the ring's outward bisector.
ON_THE_HISTIDINE = (-0.308, 5.589, 4.255)
#: 2.4 A along the same line: a second position for the same zinc.
FURTHER_OUT = (-0.503, 5.779, 4.379)
#: Nowhere near the protein.
IN_THE_SOLVENT = (30.0, 30.0, 30.0)


def _zinc(serial, where, *, altloc=" ", seq=401, occupancy=1.00,
          name="ZN", element="ZN"):
    return _atom("HETATM", serial, name, altloc, name, "A", seq, *where,
                 occupancy=occupancy, element=element)


def _write(tmp_path: Path, extra: list[str]) -> Path:
    path = tmp_path / "zinc_site.pdb"
    path.write_text("\n".join(PEPTIDE + extra) + "\nEND\n", encoding="utf-8")
    return path


def _prepared(tmp_path: Path, extra: list[str], **options):
    """Run setup on the fixture up to the prepared structure.

    Returns the prepared structure's lines, the setup record, and the
    structure handed on to be solvated and parameterised.
    """
    pytest.importorskip("pdbfixer")
    orchestrator = MagicMock()
    orchestrator.system = str(_write(tmp_path, extra))
    orchestrator._presenter = None
    setup_dir = tmp_path / "setup"
    setup_dir.mkdir()
    with patch.object(_prepare, "prepare_system", return_value={}) as solvate:
        setup_run(orchestrator=orchestrator, output_dir=setup_dir, **options)
    prepared = (setup_dir / "prepared.pdb").read_text(encoding="utf-8")
    record = json.loads(
        (setup_dir / "setup_parameters.json").read_text(encoding="utf-8"))
    return prepared.splitlines(), record, Path(solvate.call_args.args[0])


def _residue(lines: list[str], resname: str) -> list[str]:
    return [line for line in lines
            if line[:6] in ("ATOM  ", "HETATM") and line[17:20].strip() == resname]


def _at(line: str) -> tuple[float, float, float]:
    return (float(line[30:38]), float(line[38:46]), float(line[46:54]))


class TestTheZincIsSimulated:

    def test_it_is_in_the_prepared_structure(self, tmp_path: Path) -> None:
        """The whole failure: kept by the log, absent from the system."""
        prepared, _, _ = _prepared(tmp_path, [_zinc(22, ON_THE_HISTIDINE)])
        zinc = _residue(prepared, "ZN")
        assert len(zinc) == 1
        assert _at(zinc[0]) == ON_THE_HISTIDINE

    def test_it_is_what_goes_on_to_be_solvated(self, tmp_path: Path) -> None:
        _, _, handed_on = _prepared(tmp_path, [_zinc(22, ON_THE_HISTIDINE)])
        assert len(_residue(handed_on.read_text(encoding="utf-8").splitlines(),
                            "ZN")) == 1

    def test_the_setup_record_names_it(self, tmp_path: Path) -> None:
        _, record, _ = _prepared(tmp_path, [_zinc(22, ON_THE_HISTIDINE)])
        assert record["parameters"]["_retained_ions"] == ["ZN A401"]

    def test_water_asked_for_is_kept_beside_it(self, tmp_path: Path) -> None:
        """Keeping the zinc hands PDBFixer a filtered structure with every
        heterogen in it kept, and PDBFixer's own water option is then not
        read. Water that was asked for has to be in that structure."""
        water = _atom("HETATM", 23, "O", " ", "HOH", "A", 501, 12.0, 0.0, 0.0,
                      element="O")
        prepared, _, _ = _prepared(
            tmp_path, [_zinc(22, ON_THE_HISTIDINE), water], keep_water=True)
        assert len(_residue(prepared, "ZN")) == 1
        assert _residue(prepared, "HOH")


class TestAnIonAtAlternateLocationsIsOneIon:
    """A zinc written at locations A and B in one residue is one atom.

    Counted as two it was not monatomic, and from a local file setup refused
    and asked for a ZN SDF.
    """

    @staticmethod
    def _split(first: float, second: float) -> list[str]:
        return [_zinc(22, ON_THE_HISTIDINE, altloc="A", occupancy=first),
                _zinc(23, FURTHER_OUT, altloc="B", occupancy=second)]

    def test_it_is_classified_as_one_ion(self, tmp_path: Path) -> None:
        decisions = resolve(_write(tmp_path, self._split(0.6, 0.4)))
        zinc = next(d for d in decisions if d.resname == "ZN")
        assert zinc.action is Action.SIMULATE
        assert zinc.is_monatomic
        (only,) = zinc.instances
        assert only.atoms[0].altloc == "A"

    def test_a_local_file_is_not_refused(self, tmp_path: Path) -> None:
        assert _auto_ligands({"heterogens": "auto"},
                             _write(tmp_path, self._split(0.6, 0.4)),
                             tmp_path, None) == []

    @pytest.mark.parametrize(("first", "second", "where"), [
        (0.6, 0.4, ON_THE_HISTIDINE),
        (0.4, 0.6, FURTHER_OUT),
    ])
    def test_exactly_one_zinc_is_kept_at_the_major_location(
            self, tmp_path: Path, first: float, second: float,
            where: tuple[float, float, float]) -> None:
        prepared, record, _ = _prepared(tmp_path, self._split(first, second))
        zinc = _residue(prepared, "ZN")
        assert len(zinc) == 1
        assert _at(zinc[0]) == where
        assert record["parameters"]["_retained_ions"] == ["ZN A401"]


class TestAnUncoordinatedIonIsStillDiscarded:

    def test_alone(self, tmp_path: Path) -> None:
        prepared, record, _ = _prepared(tmp_path, [_zinc(22, IN_THE_SOLVENT)])
        assert not _residue(prepared, "ZN")
        assert "_retained_ions" not in record["parameters"]

    def test_beside_a_coordinated_one(self, tmp_path: Path) -> None:
        """Keeping the zinc filters the structure rather than stripping it,
        so the filter is what has to leave the sodium out."""
        sodium = _zinc(23, IN_THE_SOLVENT, seq=402, name="NA", element="NA")
        prepared, record, _ = _prepared(
            tmp_path, [_zinc(22, ON_THE_HISTIDINE), sodium])
        assert len(_residue(prepared, "ZN")) == 1
        assert not _residue(prepared, "NA")
        assert record["parameters"]["_retained_ions"] == ["ZN A401"]


class TestTheFilteredStructureKeepsThePolymer:

    def test_a_modified_residue_written_as_hetatm_stays(
            self, tmp_path: Path) -> None:
        """A selenomethionine is deposited as HETATM and is polymer; dropping
        it with the heterogens would break the chain it belongs to."""
        from fastmdxplora.setup.pipeline import _retain_in_structure

        selenium = _atom("HETATM", 24, "SE", " ", "MSE", "A", 4,
                         11.0, 5.0, 0.0, element="SE")
        source = _write(tmp_path, [_zinc(22, ON_THE_HISTIDINE), selenium])
        kept = [d for d in resolve(source) if d.resname == "ZN"]
        out = _retain_in_structure(source, tmp_path, kept)
        lines = out.read_text(encoding="utf-8").splitlines()
        assert _residue(lines, "MSE")
        assert _residue(lines, "ZN")
