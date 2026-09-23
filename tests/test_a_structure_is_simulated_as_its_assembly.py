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


def _ca(path: Path) -> dict[tuple[str, int], list[float]]:
    return {(line[21], int(line[22:26])): [float(line[30:38]), float(line[38:46]),
                                           float(line[46:54])]
            for line in path.read_text().splitlines()
            if line.startswith("ATOM") and line[12:16].strip() == "CA" and line[16] in " A"}


def _rmsd(first, second) -> float:
    import numpy as np

    p, q = np.asarray(first) - np.mean(first, 0), np.asarray(second) - np.mean(second, 0)
    u, _, vt = np.linalg.svd(p.T @ q)
    d = np.sign(np.linalg.det(vt.T @ u.T))
    rotation = vt.T @ np.diag([1, 1, d]) @ u.T
    return float(np.sqrt((((rotation @ p.T).T - q) ** 2).sum(1).mean()))


@pytest.mark.parametrize("entry, chains, per_chain", [
    ("1HHO", "ABCD", {"A": 141, "B": 146, "C": 141, "D": 146}),
    ("1STP", "ABCD", {"A": 121, "B": 121, "C": 121, "D": 121}),
])
def test_an_assembly_made_by_symmetry_is_built(tmp_path, entry, chains, per_chain) -> None:
    # 1HHO holds half a haemoglobin and 1STP a quarter of streptavidin; the
    # rest comes from the operators, each copy under chain IDs of its own,
    # with its own declared sequence and its own heterogens.
    path = _deposited(entry, tmp_path)
    orchestrator = SimpleNamespace(_assembly=None)
    built = _the_chains_to_simulate(orchestrator, path, "pdb_id", {})
    assert built.name == "input_assembly.pdb"
    residues = {c: sum(1 for key in _ca(built) if key[0] == c) for c in chains}
    assert residues == per_chain
    declared = {line[11] for line in built.read_text().splitlines() if line.startswith("SEQRES")}
    assert declared == set(chains)
    assert sorted(sum(orchestrator._assembly["built_chains"].values(), [])) == list(chains)


def test_each_copy_of_haemoglobin_has_its_haem(tmp_path) -> None:
    built = _the_chains_to_simulate(SimpleNamespace(_assembly=None),
                                    _deposited("1HHO", tmp_path), "pdb_id", {})
    haems = {line[21] for line in built.read_text().splitlines()
             if line.startswith("HETATM") and line[17:20] == "HEM"}
    assert haems == set("ABCD")


def _operators_in_mmcif(cif: str) -> list[list[list[float]]]:
    """The _pdbx_struct_oper_list rows, by their own field names.

    The category is found where it starts a line; its name also appears
    quoted inside the revision history of some entries."""
    import shlex

    lines = cif.splitlines()
    first = next(i for i, line in enumerate(lines) if line.startswith("_pdbx_struct_oper_list."))
    looped = lines[first - 1].strip() == "loop_"
    block = []
    for line in lines[first:]:
        if line.startswith("#"):
            break
        block.append(line)
    fields = [line for line in block if line.startswith("_pdbx_struct_oper_list.")]
    names = [line.split(".", 1)[1].split()[0] for line in fields]
    if looped:
        tokens = shlex.split("\n".join(line for line in block if not line.startswith("_")))
        tables = [dict(zip(names, tokens[i:i + len(names)]))
                  for i in range(0, len(tokens), len(names))]
    else:
        # A single operator is written as key-value pairs, not a loop.
        tables = [dict(zip(names, (shlex.split(line)[1] for line in fields)))]
    return [[[float(t[f"matrix[{r}][{c}]"]) for c in (1, 2, 3)] + [float(t[f"vector[{r}]"])]
             for r in (1, 2, 3)] for t in tables]


def test_the_operators_are_the_ones_the_mmcif_records(tmp_path) -> None:
    # The same deposition states its operators twice, in REMARK 350 and in
    # the mmCIF operator list; reading one is checked against the other.
    for entry in ("1HHO", "1STP"):
        [assembly] = read_assemblies(_deposited(entry, tmp_path / entry).read_text().splitlines())
        cif = gzip.decompress((DEPOSITED / f"{entry}.cif.gz").read_bytes()).decode()
        rows = _operators_in_mmcif(cif)
        from_pdb = [op for _, ops in assembly.parts for op in ops]
        assert from_pdb == rows, entry


def test_the_built_haemoglobin_is_a_haemoglobin(tmp_path) -> None:
    # Checked against a structure the code never saw: 2HHB, deposited as the
    # whole tetramer. 1HHO is the oxy form and 2HHB the deoxy, which differ
    # by a quaternary rotation, so the built tetramer superposes to within a
    # few Angstrom; a copy put anywhere else would be tens of Angstrom off.
    built = _ca(_the_chains_to_simulate(SimpleNamespace(_assembly=None),
                                        _deposited("1HHO", tmp_path / "oxy"), "pdb_id", {}))
    whole = _ca(_deposited("2HHB", tmp_path / "deoxy"))
    common = sorted(set(built) & set(whole))
    assert len(common) > 560
    assert _rmsd([built[k] for k in common], [whole[k] for k in common]) < 3.0


def test_every_copy_is_missing_what_the_deposited_chain_is_missing(tmp_path) -> None:
    # PDBFixer finds missing residues by comparing the model with SEQRES, so
    # a copy without its own SEQRES would be found missing nothing.
    pdbfixer = pytest.importorskip("pdbfixer")
    built = _the_chains_to_simulate(SimpleNamespace(_assembly=None),
                                    _deposited("1STP", tmp_path), "pdb_id", {})
    fixer = pdbfixer.PDBFixer(filename=str(built))
    fixer.findMissingResidues()
    # By chain ID: each ID is a protein chain and, after its TER, a chain of
    # heterogens, which has nothing missing.
    chains = list(fixer.topology.chains())
    gaps: dict[str, list[tuple[int, int]]] = {}
    for (index, at), names in fixer.missingResidues.items():
        gaps.setdefault(chains[index].id, []).append((at, len(names)))
    assert gaps["A"] == [(0, 12), (121, 26)]
    assert gaps["A"] == gaps["B"] == gaps["C"] == gaps["D"]


def test_copies_put_in_one_place_are_refused(tmp_path) -> None:
    # 1STP with its second operator misread as the identity: two copies of
    # chain A in the same place, which is not an assembly.
    path = _deposited("1STP", tmp_path)
    lines = path.read_text().splitlines()
    identity = {"BIOMT1": "1.000000  0.000000  0.000000        0.00000",
                "BIOMT2": "0.000000  1.000000  0.000000        0.00000",
                "BIOMT3": "0.000000  0.000000  1.000000        0.00000"}
    lines = [line[:24] + identity[line[13:19]] if line.startswith("REMARK 350")
             and line[13:19] in identity and line[19:23].strip() == "2" else line
             for line in lines]
    path.write_text("\n".join(lines) + "\n")
    with pytest.raises(StudyError) as caught:
        _the_chains_to_simulate(SimpleNamespace(_assembly=None), path, "pdb_id", {})
    assert "two copies in one place" in str(caught.value)


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
