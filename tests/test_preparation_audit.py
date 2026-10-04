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

    assert len(calls) == 1
    assert calls[0] is orchestrator._preparation_audit
    assert (setup_dir / "preparation_audit.json").is_file()
