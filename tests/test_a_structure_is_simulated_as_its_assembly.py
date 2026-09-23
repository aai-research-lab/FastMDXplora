"""A deposited structure is simulated as the biological assembly it declares.

Every deposited chain used to be simulated, whatever the depositors said the
molecule was. These run on six real entries as deposited at RCSB, kept
gzipped in tests/data/assemblies, not on records written to suit the code:

* 4AKE, 2HHB, 3PTB -- the file is the assembly; nothing changes.
* 1AKE -- two copies of a monomer, each its own assembly, the same in content.
* 1HHO, 1STP -- the assembly is built by symmetry beyond the chains deposited.
"""

from __future__ import annotations

import gzip
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from fastmdxplora.refusals import StudyError, refusal_of
from fastmdxplora.setup.assembly import choose_assembly, read_assemblies
from fastmdxplora.setup.pipeline import _select_chains, _the_chains_to_simulate

DEPOSITED = Path(__file__).parent / "data" / "assemblies"


def _deposited(entry: str, into: Path) -> Path:
    into.mkdir(parents=True, exist_ok=True)
    target = into / "input.pdb"
    target.write_bytes(gzip.decompress((DEPOSITED / f"{entry}.pdb.gz").read_bytes()))
    return target


def _chains_in(path: Path) -> list[str]:
    return sorted({line[21] for line in path.read_text().splitlines() if line.startswith("ATOM")})


class _Heard(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)


@pytest.fixture
def heard():
    # Attached to the logger that speaks, not to root: `fastmdx` does not
    # propagate once the console is set up, so caplog hears nothing then.
    handler = _Heard()
    logger = logging.getLogger("fastmdx.setup.assembly")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    yield handler
    logger.removeHandler(handler)


@pytest.mark.parametrize("entry, chains, copies, assigned", [
    ("4AKE", ["A", "B"], 1, "the authors"),
    ("2HHB", ["A", "B", "C", "D"], 1, "the authors, and software agrees"),
    ("3PTB", ["A"], 1, "the authors"),
    ("1HHO", ["A", "B"], 2, "the authors, and software agrees"),
    ("1STP", ["A"], 4, "the authors, and software agrees"),
])
def test_the_declared_assembly_is_read(tmp_path, entry, chains, copies, assigned) -> None:
    path = _deposited(entry, tmp_path)
    [assembly] = read_assemblies(path.read_text().splitlines())
    assert (assembly.chains, assembly.copies, assembly.assigned_by) == (chains, copies, assigned)


@pytest.mark.parametrize("entry", ["4AKE", "2HHB", "3PTB"])
def test_a_file_that_is_its_assembly_is_left_as_it_is(tmp_path, entry) -> None:
    path = _deposited(entry, tmp_path)
    orchestrator = SimpleNamespace(_assembly=None)
    kept = _the_chains_to_simulate(orchestrator, path, "pdb_id", {})
    assert kept == path
    assert orchestrator._assembly["generated_by_symmetry"] is False


def test_two_equivalent_copies_are_chosen_between_by_their_order(tmp_path, heard) -> None:
    # 1AKE's two chains are each an author-assigned monomer, both with the
    # inhibitor bound; chain B is the less ordered copy, as its paper says.
    path = _deposited("1AKE", tmp_path)
    orchestrator = SimpleNamespace(_assembly=None)
    kept = _the_chains_to_simulate(orchestrator, path, "pdb_id", {})
    assert _chains_in(kept) == ["A"]
    assert "AP5" in kept.read_text()
    record = orchestrator._assembly
    assert (record["assembly"], record["alternatives"]) == (1, [2])
    assert "lower mean alpha-carbon B-factor: 27.4 against 49.0" in record["chosen_because"]
    said = " ".join(r.getMessage() for r in heard.records if r.levelno >= logging.INFO)
    assert "`chains: [B]`" in said


def test_named_chains_are_kept_whatever_the_assembly(tmp_path) -> None:
    path = _deposited("1AKE", tmp_path)
    orchestrator = SimpleNamespace(_assembly=None)
    kept = _the_chains_to_simulate(orchestrator, path, "pdb_id", {"chains": ["B"]})
    assert _chains_in(kept) == ["B"]
    assert orchestrator._assembly is None


def test_copies_that_hold_different_things_are_asked_about(tmp_path) -> None:
    # 1AKE as deposited, with the inhibitor taken out of chain B's site: the
    # two assemblies are now an apo and a bound enzyme, which is the study.
    path = _deposited("1AKE", tmp_path)
    lines = [line for line in path.read_text().splitlines()
             if not (line.startswith("HETATM") and line[17:20] == "AP5" and line[21] == "B")]
    path.write_text("\n".join(lines) + "\n")
    with pytest.raises(StudyError) as caught:
        choose_assembly(path, select=_select_chains)
    refusal = refusal_of(caught.value)
    assert refusal.code == "setup.structure.assembly_ambiguous"
    assert "`chains: [A]`" in str(caught.value) and "`chains: [B]`" in str(caught.value)


@pytest.mark.parametrize("entry, copies", [("1HHO", 2), ("1STP", 4)])
def test_an_assembly_built_by_symmetry_says_how_much_is_simulated(
        tmp_path, heard, entry, copies) -> None:
    path = _deposited(entry, tmp_path)
    orchestrator = SimpleNamespace(_assembly=None)
    assert _the_chains_to_simulate(orchestrator, path, "pdb_id", {}) == path
    assert orchestrator._assembly["copies"] == copies
    warnings = [r.getMessage() for r in heard.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and f"{copies} copies" in warnings[0]


def test_a_structure_with_no_assembly_records_is_left_alone(tmp_path) -> None:
    path = _deposited("3PTB", tmp_path)
    path.write_text("\n".join(line for line in path.read_text().splitlines()
                              if not line.startswith("REMARK 350")) + "\n")
    orchestrator = SimpleNamespace(_assembly=None)
    assert _the_chains_to_simulate(orchestrator, path, "pdb_id", {}) == path
    assert orchestrator._assembly is None


def test_the_setup_record_names_the_assembly(tmp_path) -> None:
    # Through the record writer the setup phase uses.
    from fastmdxplora.setup.pipeline import _write_manifest

    path = _deposited("1AKE", tmp_path / "setup")
    orchestrator = SimpleNamespace(_assembly=None, _structure_provenance=None, system="1AKE")
    _the_chains_to_simulate(orchestrator, path, "pdb_id", {})
    _write_manifest(tmp_path / "setup", orchestrator, "pdb_id", {}, [], [])
    record = json.loads((tmp_path / "setup" / "setup_parameters.json").read_text())
    assert record["input"]["assembly"]["assembly"] == 1
