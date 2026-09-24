"""An mmCIF file is read as mmCIF.

Setup copied any structure to input.pdb and read it as PDB text, so every
mmCIF file failed at its first number, and an entry RCSB keeps only in mmCIF
could not be fetched at all. It is now written as the PDB records setup
reads. These entries were deposited in both formats, so the translation is
checked against the depositors' own PDB file rather than against itself.
"""

from __future__ import annotations

import gzip
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

# The translation reads mmCIF with OpenMM's own reader, as setup does.
pytest.importorskip("openmm.app")

from fastmdxplora.refusals import StudyError, refusal_of  # noqa: E402
from fastmdxplora.setup.assembly import _quality, read_assemblies  # noqa: E402
from fastmdxplora.setup.mmcif import to_pdb_lines  # noqa: E402

DEPOSITED = Path(__file__).parent / "data" / "assemblies"
BOTH_FORMATS = ["1AKE", "1HHO", "1STP", "3PTB"]


def _unpacked(entry: str, suffix: str, into: Path) -> Path:
    into.mkdir(parents=True, exist_ok=True)
    target = into / f"{entry}.{suffix}"
    target.write_bytes(gzip.decompress((DEPOSITED / f"{entry}.{suffix}.gz").read_bytes()))
    return target


def _atoms(lines: list[str]) -> set:
    """Each atom by record, name, altloc, residue, chain, number, insertion
    code and coordinates, as written."""
    return {(line[:6], line[12:16].strip(), line[16], line[17:20].strip(), line[21],
             int(line[22:26]), line[26], line[30:38].strip(), line[38:46].strip(),
             line[46:54].strip())
            for line in lines if line.startswith(("ATOM", "HETATM"))}


def _sequences(lines: list[str]) -> dict[str, list[str]]:
    declared: dict[str, list[str]] = {}
    for line in lines:
        if line.startswith("SEQRES"):
            declared.setdefault(line[11], []).extend(line[19:70].split())
    return declared


def _assemblies(lines: list[str]) -> list:
    return [(a.number, a.chains, a.copies, a.assigned_by, a.size.lower(),
             [op for _, ops in a.parts for op in ops]) for a in read_assemblies(lines)]


@pytest.mark.parametrize("entry", BOTH_FORMATS)
def test_the_translation_is_the_depositors_pdb_file(tmp_path, entry) -> None:
    deposited = _unpacked(entry, "pdb", tmp_path).read_text().splitlines()
    translated = to_pdb_lines(_unpacked(entry, "cif", tmp_path))
    assert _atoms(translated) == _atoms(deposited)
    assert _sequences(translated) == _sequences(deposited)
    assert _assemblies(translated) == _assemblies(deposited)
    chains = sorted({line[21] for line in deposited if line.startswith("ATOM")})
    assert [_quality(translated, [c]) for c in chains] == [_quality(deposited, [c]) for c in chains]
    cell = [next(line[:54] for line in lines if line.startswith("CRYST1"))
            for lines in (translated, deposited)]
    assert cell[0] == cell[1]


def test_a_cif_given_to_setup_is_read(tmp_path) -> None:
    # Through setup's own input step, then its assembly step: 1STP from its
    # mmCIF is built into the same tetramer as from its PDB file.
    pdbfixer = pytest.importorskip("pdbfixer")
    from fastmdxplora.setup.pipeline import _resolve_input, _the_chains_to_simulate

    source = _unpacked("1STP", "cif", tmp_path / "given")
    setup = tmp_path / "setup"
    setup.mkdir()
    placed = _resolve_input(str(source), "pdb_file", setup)
    assert placed.name == "input.pdb" and (setup / "input.cif").is_file()
    assert pdbfixer.PDBFixer(filename=str(placed)).topology.getNumAtoms() == 1001

    built = {}
    for name, path in (("cif", placed), ("pdb", _unpacked("1STP", "pdb", tmp_path / "pdb"))):
        orchestrator = SimpleNamespace(_assembly=None)
        out = _the_chains_to_simulate(orchestrator, path, "pdb_file", {})
        built[name] = (_atoms(out.read_text().splitlines()), orchestrator._assembly["built_chains"])
    assert built["cif"] == built["pdb"]


def test_a_structure_the_pdb_format_cannot_hold_is_refused(tmp_path) -> None:
    # 1STP as deposited, its chain renamed to two characters, which mmCIF
    # allows and the PDB format has no column for.
    source = _unpacked("1STP", "cif", tmp_path)
    lines = []
    for line in source.read_text().splitlines():
        fields = line.split()
        if line.startswith(("ATOM ", "HETATM ")) and len(fields) > 18 and fields[18] == "A":
            fields[18] = "AB"
            line = " ".join(fields)
        lines.append(line)
    source.write_text("\n".join(lines) + "\n")
    with pytest.raises(StudyError) as caught:
        to_pdb_lines(source)
    assert refusal_of(caught.value).code == "setup.structure.too_large"
    assert "'AB'" in str(caught.value)


def test_an_entry_kept_only_as_mmcif_is_fetched_as_mmcif(tmp_path, monkeypatch) -> None:
    import urllib.error
    import urllib.request

    from fastmdxplora.setup.pipeline import _fetch_pdb_from_rcsb

    source = _unpacked("1HHO", "cif", tmp_path / "rcsb")
    asked = []

    def rcsb(url, destination):
        asked.append(url)
        if url.endswith(".pdb"):
            raise urllib.error.HTTPError(url, 404, "Not Found", None, None)
        shutil.copy(source, destination)
        return str(destination), None

    monkeypatch.setattr(urllib.request, "urlretrieve", rcsb)
    placed = _fetch_pdb_from_rcsb("1hho", tmp_path / "setup" / "input.pdb")
    assert [u.rsplit(".", 1)[1] for u in asked] == ["pdb", "cif"]
    deposited = _unpacked("1HHO", "pdb", tmp_path / "dep").read_text().splitlines()
    assert _atoms(placed.read_text().splitlines()) == _atoms(deposited)
