from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fastmdxplora.config.loader import ConfigError, validate_config
from fastmdxplora.setup import pipeline


def test_preparation_audit_is_a_validated_opt_in_with_a_false_default():
    assert pipeline.DEFAULTS["preparation_audit"] is False
    validate_config({"setup": {"preparation_audit": True}})
    with pytest.raises(ConfigError, match="should be true/false"):
        validate_config({"setup": {"preparation_audit": "yes"}})


def test_disabled_preparation_audit_does_no_io(tmp_path):
    from fastmdxplora.setup.audit import PreparationRecorder

    source = tmp_path / "source.pdb"
    audit = PreparationRecorder(tmp_path / "study" / "setup", enabled=False)
    audit.capture_file(source, "source")
    audit.decision("choice", {"value": 1})
    assert not (tmp_path / "study").exists()


def test_preparation_audit_keeps_bounded_immutable_snapshots(tmp_path, monkeypatch):
    from fastmdxplora.setup.audit import PreparationRecorder

    source = tmp_path / "source.pdb"
    source.write_bytes(b"HEADER supplied input\nEND\n")
    audit = PreparationRecorder(tmp_path / "study" / "setup", enabled=True)
    audit.capture_file(source, "original_source")

    record = json.loads((tmp_path / "study" / "setup" / "preparation_audit.json").read_text())
    row = next(iter(record["sources"].values()))
    snapshot = tmp_path / "study" / row["snapshot"]
    assert snapshot.read_bytes() == b"HEADER supplied input\nEND\n"
    assert row["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()

    source.write_bytes(b"HEADER changed later\nEND\n")
    assert snapshot.read_bytes() != source.read_bytes()

    monkeypatch.setattr("fastmdxplora.setup.audit.MAX_SNAPSHOT", 2)
    audit.capture_file(source, "oversized_source")
    limited = audit.record["sources"]["source-0002"]
    assert limited["snapshot"] is None
    assert "limit" in limited["unavailable"]


def test_preparation_audit_capture_failure_does_not_change_scientific_outcome(tmp_path,
                                                                                monkeypatch):
    from fastmdxplora.setup.audit import PreparationRecorder

    audit = PreparationRecorder(tmp_path / "study" / "setup", enabled=True)

    def fail_save():
        raise OSError("audit disk failure")

    monkeypatch.setattr(audit, "_save", fail_save)
    assert audit.decision("observed_choice", {"value": 1}) is None
    assert audit.record["status"] == "incomplete"
    assert audit.record["warnings"]


def test_repair_once_receives_one_preparation_recorder(tmp_path, monkeypatch):
    structure = tmp_path / "input.pdb"
    structure.write_text("END\n", encoding="utf-8")
    setup_dir = tmp_path / "setup"
    setup_dir.mkdir()
    calls: list[object] = []

    def repair(input_pdb, output_pdb, *, complex_pdb=None, audit=None, **_kwargs):
        calls.append(audit)
        Path(output_pdb).write_text("END\n", encoding="utf-8")
        if complex_pdb:
            Path(complex_pdb).write_text("END\n", encoding="utf-8")
        return []

    def found(params, input_pdb, setup_dir, entry_id):
        params["_reinstated_heterogens"] = ("LIG",)
        params["_explained_heterogens"] = ("LIG",)
        pipeline._repaired_complex(params, input_pdb, setup_dir)
        pipeline._repaired_complex(params, input_pdb, setup_dir)
        params["ligand_name"] = "LIG"
        return []

    orchestrator = MagicMock(system=str(structure), _presenter=None)
    with patch.object(pipeline, "_auto_ligands", found), \
            patch.object(pipeline, "_refuse_without_a_charge_provider"), \
            patch("fastmdxplora.setup.pdbfix.fix_pdb_with_pdbfixer", side_effect=repair), \
            patch("fastmdxplora.setup.prepare.prepare_system", return_value={}):
        pipeline.run(
            orchestrator=orchestrator,
            output_dir=setup_dir,
            preparation_audit=True,
        )

    from fastmdxplora.setup.audit import PreparationRecorder

    assert len(calls) == 1 and isinstance(calls[0], PreparationRecorder)
    assert (setup_dir / "preparation_audit.json").is_file()


def test_a_real_preparation_is_recorded_and_not_changed(tmp_path):
    """Setup for real on a tripeptide, with the audit and without, from one
    `setup.random_seed`: the same system and state byte for byte, and a
    record of what the preparation did, in the order it did it."""
    pytest.importorskip("pdbfixer")
    from fastmdxplora import FastMDXplora
    from tests.test_a_real_study_runs_end_to_end import TRI_ALANINE

    pdb = tmp_path / "tri.pdb"
    pdb.write_text(TRI_ALANINE, encoding="utf-8")

    def prepared(name, audit):
        study = FastMDXplora(system=str(pdb), output_dir=str(tmp_path / name),
                             options={"setup": {"preparation_audit": audit,
                                                "random_seed": 7}})
        results = study.explore(include_phase=["setup"])
        assert results[0].status == "ok", results[0].message
        setup = tmp_path / name / "setup"
        return setup, hashlib.sha256((setup / "solvated.pdb").read_bytes()
                                     + (setup / "state.xml").read_bytes()).hexdigest()

    plain, without = prepared("plain", False)
    audited, with_audit = prepared("audited", True)

    assert with_audit == without
    assert not (plain / "preparation_audit.json").exists()
    assert not (plain / "audit").exists()
    record = json.loads((audited / "preparation_audit.json").read_text(encoding="utf-8"))
    assert record["status"] == "complete", record["warnings"]
    done = [event["operation"] for event in record["events"]]
    assert done[0] == "original_supplied_source"
    assert done.index("pdbfixer_input") < done.index("add_missing_hydrogens") \
        < done.index("prepared_system") < done.index("recorded_setup_choices")
    assert done[-1] == "recorded_setup_choices"
    hydrogens = next(event for event in record["events"]
                     if event["operation"] == "add_missing_hydrogens")
    counts = record["sources"][hydrogens["after"]]["counts"]
    assert counts["hydrogens"] > 0 and counts["heavy_atoms"] > 0
    first = record["sources"][record["events"][0]["after"]]
    assert first["sha256"] == hashlib.sha256(pdb.read_bytes()).hexdigest()
    assert (tmp_path / "audited" / first["snapshot"]).read_bytes() == pdb.read_bytes()
    manifest = json.loads((audited / "setup_parameters.json").read_text(encoding="utf-8"))
    assert manifest["parameters"]["preparation_audit"] is True
    assert "object at 0x" not in json.dumps(manifest)


def test_a_preparation_that_stops_says_so_and_leaves_no_recorder(tmp_path, monkeypatch):
    from fastmdxplora.refusals import StudyError

    setup_dir = tmp_path / "setup"

    def stops(**_options):
        pipeline._RECORDER.get().decision("heterogen_policy", {"requested": "auto"})
        raise StudyError("The structure could not be read.", code="setup.input.unrecognised")

    monkeypatch.setattr(pipeline, "_run", stops)
    with pytest.raises(StudyError):
        pipeline.run(orchestrator=MagicMock(), output_dir=setup_dir, preparation_audit=True)

    record = json.loads((setup_dir / "preparation_audit.json").read_text(encoding="utf-8"))
    assert record["status"] == "stopped"
    assert record["stopped_by"] == "setup.input.unrecognised"
    # Nothing prepared afterwards on this thread records into it.
    assert pipeline._RECORDER.get() is None


def test_a_setup_folder_keeps_one_preparations_snapshots(tmp_path):
    from fastmdxplora.setup.audit import PreparationRecorder

    setup_dir = tmp_path / "setup"
    source = tmp_path / "source.pdb"
    for text in ("HEADER first\nEND\n", "HEADER second\nEND\n"):
        source.write_text(text, encoding="utf-8")
        PreparationRecorder(setup_dir).capture_file(source, "original_supplied_source")

    kept = sorted((setup_dir / "audit").iterdir())
    assert [path.read_text(encoding="utf-8") for path in kept] == ["HEADER second\nEND\n"]
    record = json.loads((setup_dir / "preparation_audit.json").read_text(encoding="utf-8"))
    assert (tmp_path / record["sources"]["source-0001"]["snapshot"]) == kept[0]
