import hashlib
import json
import random

import pytest

from fastmdxplora.setup.audit import PreparationRecorder


def test_source_snapshot_is_immutable_and_disabled_capture_writes_nothing(tmp_path):
    source = tmp_path / "source.pdb"
    source.write_bytes(b"HEADER supplied input\nEND\n")
    audit = PreparationRecorder(tmp_path / "study/setup")
    kept = random.getstate()
    audit.capture_file(source, "original_supplied_source")
    assert random.getstate() == kept
    row = next(iter(audit.record["sources"].values()))
    snapshot = tmp_path / "study" / row["snapshot"]
    assert snapshot.read_bytes() == source.read_bytes()
    assert row["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    source.write_bytes(b"HEADER changed later\nEND\n")
    assert snapshot.read_bytes() != source.read_bytes()
    disabled = PreparationRecorder(tmp_path / "disabled/setup", enabled=False)
    disabled.capture_file(source, "original_supplied_source")
    assert not disabled.root.exists()


def test_snapshot_limit_is_explicit_and_audit_failure_does_not_raise(tmp_path, monkeypatch):
    source = tmp_path / "source.pdb"
    source.write_bytes(b"HEADER supplied input\nEND\n")
    monkeypatch.setattr("fastmdxplora.setup.audit.MAX_SNAPSHOT", 2)
    audit = PreparationRecorder(tmp_path / "study/setup")
    audit.capture_file(source, "original_supplied_source")
    row = next(iter(audit.record["sources"].values()))
    assert row["snapshot"] is None and "limit" in row["unavailable"]
    assert audit.record["status"] == "incomplete" and audit.record["warnings"]

    def refused():
        raise OSError("fixture disk failure")

    monkeypatch.setattr(audit, "_folder", refused)
    assert audit.decision("fixture_operation", {"observed": True}) is None
    assert audit.record["status"] == "incomplete"


def test_a_repeated_preparation_preserves_prior_snapshots(tmp_path):
    source = tmp_path / "input.pdb"
    source.write_text("END\n", encoding="utf-8")
    first = PreparationRecorder(tmp_path / "study/setup")
    first.capture_file(source, "original_supplied_source")
    original = next(iter(first.record["sources"].values()))["snapshot"]
    second = PreparationRecorder(first.root)
    second.capture_file(source, "original_supplied_source")
    newer = next(iter(second.record["sources"].values()))["snapshot"]
    assert original != newer
    assert (tmp_path / "study" / original).is_file()
    assert (tmp_path / "study" / newer).is_file()


def test_old_system_file_does_not_certify_new_preparation(tmp_path):
    audit = PreparationRecorder(tmp_path / "setup")
    audit.root.mkdir()
    (audit.root / "system.xml").write_text("old output")
    audit.manifest({})
    assert audit.record["status"] == "partial"


def test_real_seeded_preparation_has_identical_scientific_outputs_with_audit_on_or_off(
    tmp_path, monkeypatch
):
    openmm = pytest.importorskip("openmm")
    pytest.importorskip("pdbfixer")
    np = pytest.importorskip("numpy")
    from pdbfixer import PDBFixer

    from tests._the_phase import a_real_setup

    # Compare the same explicit deterministic backend. Automatic CPU/GPU
    # selection can introduce minimizer roundoff unrelated to observation.
    initialize = PDBFixer.__init__

    def on_reference(self, *args, **kwargs):
        initialize(self, *args, **kwargs)
        self.platform = openmm.Platform.getPlatformByName("Reference")

    monkeypatch.setattr(PDBFixer, "__init__", on_reference)
    settings = {"random_seed": 314159, "force_field": ["amber14-all.xml", "amber14/tip3p.xml"]}
    runs = []
    for name, enabled in (("off", "0"), ("on", "1")):
        root = tmp_path / name
        root.mkdir()
        monkeypatch.setenv("FASTMDXPLORA_PREPARATION_AUDIT", enabled)
        runs.append(a_real_setup(root, **settings))
    off, on = [run.root / "setup" for run in runs]
    for name in ("input.pdb", "prepared.pdb", "topology.pdb", "system.xml"):
        assert (off / name).read_bytes() == (on / name).read_bytes(), name
    states = [
        openmm.XmlSerializer.deserialize((root / "state.xml").read_text(encoding="utf-8"))
        for root in (off, on)
    ]
    np.testing.assert_allclose(
        states[0].getPositions(asNumpy=True)._value,
        states[1].getPositions(asNumpy=True)._value,
        rtol=0,
        atol=1e-10,
    )
    np.testing.assert_allclose(
        states[0].getPeriodicBoxVectors(asNumpy=True)._value,
        states[1].getPeriodicBoxVectors(asNumpy=True)._value,
        rtol=0,
        atol=1e-10,
    )
    assert not (off / "preparation_audit.json").exists()
    record = json.loads((on / "preparation_audit.json").read_text(encoding="utf-8"))
    assert record["status"] == "complete" and not record["warnings"]
    operations = [event["operation"] for event in record["events"]]
    assert {
        "original_supplied_source",
        "model_selection",
        "assembly_chain_selection",
        "missing_atom_requests",
        "add_missing_atoms",
        "add_missing_hydrogens",
        "prepared_system",
        "recorded_setup_choices",
    } <= set(operations)
    assert record["backends"]["openmm"] == openmm.__version__
