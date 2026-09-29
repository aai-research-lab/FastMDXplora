"""A residue is given the protonation state asked for.

Setup chose every residue's protonation from its usual pKa at the pH, and a
histidine's tautomer from its hydrogen bonds, with no way to say otherwise:
a catalytic histidine that should be charged, or a metal ligand that must
carry its hydrogen on the other nitrogen, was whatever PDBFixer placed.
`setup.residue_states` names residues by chain and number, as the structure
numbers them, and the state each takes; a residue the structure does not
hold, or a state the residue cannot take, stops setup.

Trypsin (3PTB) is the case: His57 of the catalytic triad.
"""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from fastmdxplora.refusals import StudyError, refusal_of
from fastmdxplora.setup.pdbfix import RESIDUE_STATES, parse_residue_states

DATA = Path(__file__).resolve().parent / "data" / "assemblies"


class TestTheNames:
    def test_chain_number_and_insertion(self):
        assert parse_residue_states({"A:57": "hip", "B:184a": "ASH", " A : -3 ": "LYN"}) == {
            ("A", "57", ""): "HIP", ("B", "184", "A"): "ASH", ("A", "-3", ""): "LYN"}

    @pytest.mark.parametrize("key", ["57", "A57", "A:", "A:5-7", "chainA:57"])
    def test_what_is_not_a_residue_is_refused(self, key):
        with pytest.raises(StudyError) as raised:
            parse_residue_states({key: "HIP"})
        found = refusal_of(raised.value)
        assert found.code == "setup.structure.residue_state_unparseable"
        assert found.details["accepted_forms"] == ["A:57", "A:184A"]

    def test_the_states_are_openmms(self):
        assert RESIDUE_STATES == {"HIS": ("HID", "HIE", "HIP"), "ASP": ("ASH", "ASP"),
                                  "GLU": ("GLH", "GLU"), "LYS": ("LYN", "LYS")}


@pytest.fixture(scope="module")
def trypsin(tmp_path_factory) -> Path:
    # Here rather than for the module, so the names above are checked where
    # OpenMM is not installed.
    pytest.importorskip("pdbfixer")
    pytest.importorskip("openmm")
    where = tmp_path_factory.mktemp("trypsin") / "3PTB.pdb"
    where.write_bytes(gzip.decompress((DATA / "3PTB.pdb.gz").read_bytes()))
    return where


def _hydrogens(path: Path) -> dict[tuple[str, str], set[str]]:
    pytest.importorskip("openmm")
    from openmm.app import PDBFile

    found: dict[tuple[str, str], set[str]] = {}
    for residue in PDBFile(str(path)).topology.residues():
        found[(residue.chain.id, residue.id)] = {
            a.name for a in residue.atoms() if a.element is not None and a.element.symbol == "H"}
    return found


@pytest.fixture(scope="module")
def as_setup_would(trypsin, tmp_path_factory) -> dict:
    from fastmdxplora.setup.pdbfix import fix_pdb_with_pdbfixer

    out = tmp_path_factory.mktemp("default") / "fixed.pdb"
    assert fix_pdb_with_pdbfixer(str(trypsin), str(out), ph=7.0) == []
    return _hydrogens(out)


@pytest.mark.parametrize("state, has, lacks", [
    ("HIP", {"HD1", "HE2"}, set()), ("HID", {"HD1"}, {"HE2"}), ("HIE", {"HE2"}, {"HD1"})])
def test_his57_takes_the_state_and_nothing_else_moves(trypsin, as_setup_would, tmp_path,
                                                      state, has, lacks):
    from fastmdxplora.setup.pdbfix import fix_pdb_with_pdbfixer

    out = tmp_path / "fixed.pdb"
    applied = fix_pdb_with_pdbfixer(str(trypsin), str(out), ph=7.0,
                                    residue_states={"A:57": state})
    assert applied == [f"A:57 HIS as {state}"]
    found = _hydrogens(out)
    assert has <= found[("A", "57")] and not (lacks & found[("A", "57")])
    others = {key: names for key, names in found.items() if key != ("A", "57")}
    assert others == {key: names for key, names in as_setup_would.items() if key != ("A", "57")}


def test_a_residue_the_structure_does_not_hold(trypsin, tmp_path):
    from fastmdxplora.setup.pdbfix import fix_pdb_with_pdbfixer

    with pytest.raises(StudyError) as raised:
        fix_pdb_with_pdbfixer(str(trypsin), str(tmp_path / "x.pdb"),
                              residue_states={"B:57": "HIP"})
    found = refusal_of(raised.value)
    assert found.code == "setup.structure.residue_state_unmatched"
    assert found.details["chains"] == ["A"]


def test_a_state_the_residue_cannot_take(trypsin, tmp_path):
    from fastmdxplora.setup.pdbfix import fix_pdb_with_pdbfixer

    with pytest.raises(StudyError) as raised:
        fix_pdb_with_pdbfixer(str(trypsin), str(tmp_path / "x.pdb"),
                              residue_states={"A:189": "HIP"})
    found = refusal_of(raised.value)
    assert found.code == "setup.structure.residue_state_not_permitted"
    assert found.details["permitted"] == ["ASH", "ASP"]
    assert "ASP" in str(raised.value) and "A:189" in str(raised.value)


def test_the_hook_it_relies_on_is_there():
    """PDBFixer asks one method which variant each residue gets; if an update
    renames it, this fails here rather than a study quietly taking setup's
    own choice."""
    PDBFixer = pytest.importorskip("pdbfixer").PDBFixer

    assert callable(getattr(PDBFixer, "_describeVariant", None))


def test_it_is_a_setting_of_setup():
    from fastmdxplora.config.loader import validate_config

    validate_config({"setup": {"residue_states": {"A:57": "HIP"}}})


def test_setup_passes_it_on_and_records_it(tmp_path):
    """Through the setup phase: handed to the PDBFixer step, then said on
    screen and kept in the setup record's notes."""
    import json
    from unittest.mock import MagicMock, patch

    from fastmdxplora.setup.pipeline import run as setup_run

    structure = tmp_path / "input.pdb"
    structure.write_text(
        "ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00  0.00           N\n"
        "ATOM      2  CA  ALA A   1       1.458   0.000   0.000  1.00  0.00           C\n"
        "END\n", encoding="utf-8")
    orchestrator = MagicMock()
    orchestrator.system = str(structure)
    orchestrator.output_dir = tmp_path
    presenter = MagicMock()
    orchestrator._presenter = presenter
    out = tmp_path / "setup"
    out.mkdir()
    with patch("fastmdxplora.setup.pdbfix.fix_pdb_with_pdbfixer",
               return_value=["A:1 HIS as HIP"]) as fixed, \
         patch("fastmdxplora.setup.prepare.prepare_system", return_value={}):
        setup_run(orchestrator=orchestrator, output_dir=out,
                  residue_states={"A:1": "HIP"})
    assert fixed.call_args.kwargs["residue_states"] == {"A:1": "HIP"}
    said = [str(call.args[0]) for call in presenter.step.call_args_list if call.args]
    assert "Protonation states set by hand: A:1 HIS as HIP" in said
    record = json.loads((out / "setup_parameters.json").read_text(encoding="utf-8"))
    assert ("Protonation states set by hand (setup.residue_states): A:1 HIS as HIP."
            in json.dumps(record))


def test_the_plan_says_it():
    from fastmdxplora.gui.plan import plan_of

    lines = {line["label"]: line for line in plan_of(
        {"systems": [{"system": "3PTB"}], "setup": {"residue_states": {"A:57": "HIP"}}})}
    assert lines["Residue states"] == {"label": "Residue states", "value": "A:57 HIP",
                                       "default": False}
