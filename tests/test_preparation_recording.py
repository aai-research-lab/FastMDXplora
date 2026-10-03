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


def test_disabled_backend_observers_do_not_access_models_or_diagnostics(tmp_path):
    from fastmdxplora.setup.audit import observe_decision, observe_model

    class Unreadable:
        @property
        def topology(self):
            pytest.fail("disabled observation accessed topology")

    def diagnostic():
        pytest.fail("disabled observation evaluated diagnostic")

    audit = PreparationRecorder(tmp_path / "setup", enabled=False)
    observe_model(audit, "solvent_ions", Unreadable(), diagnostic)
    observe_decision(audit, "system_parameterization", diagnostic)
    assert not audit.root.exists()


def test_backend_diagnostic_failures_are_visible_without_replacing_results(tmp_path):
    from fastmdxplora.setup.audit import observe_decision, observe_model

    def unavailable():
        raise RuntimeError("diagnostic failed")

    audit = PreparationRecorder(tmp_path / "setup")
    observe_model(audit, "solvent_ions", object(), unavailable)
    observe_decision(audit, "system_parameterization", unavailable)
    assert audit.record["status"] == "incomplete"
    assert len(audit.record["warnings"]) == 2
    assert not audit.record["events"]


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
        "assembled_solute",
        "solvent_ions",
        "system_parameterization",
        "prepared_system",
        "recorded_setup_choices",
    } <= set(operations)
    assert record["backends"]["openmm"] == openmm.__version__


def test_membrane_patch_observation_preserves_topology_positions_and_system(tmp_path):
    import random
    from pathlib import Path
    from time import perf_counter

    openmm = pytest.importorskip("openmm")
    app = pytest.importorskip("openmm.app")
    np = pytest.importorskip("numpy")
    from fastmdxplora.setup.audit import MAX_TOTAL, observe_model

    # An actual shipped lipid/water patch and parameterized System, without
    # constructing another bilayer or advancing a simulation.
    patch = Path(app.__file__).parent / "data" / "POPC.pdb"
    original = patch.read_bytes()
    model = app.PDBFile(str(patch))
    system = app.ForceField("amber14-all.xml", "amber14/tip3p.xml").createSystem(
        model.topology, nonbondedMethod=app.PME, constraints=app.HBonds
    )
    serialized = openmm.XmlSerializer.serialize(system)
    atoms = [(atom.index, atom.name, atom.residue.id, atom.residue.name,
              atom.residue.chain.id) for atom in model.topology.atoms()]
    bonds = [(one.index, two.index) for one, two in model.topology.bonds()]
    positions = np.array(model.positions.value_in_unit(openmm.unit.nanometer), copy=True)
    box = model.topology.getPeriodicBoxVectors()
    stream = random.getstate()
    disabled = PreparationRecorder(tmp_path / "off", enabled=False)
    observe_model(disabled, "membrane_solvent_ions", model)
    audit = PreparationRecorder(tmp_path / "on")
    started = perf_counter()
    observe_model(audit, "membrane_solvent_ions", model, {"fixture": "OpenMM POPC patch"})
    elapsed = perf_counter() - started
    assert audit.record["status"] == "recording" and not audit.record["warnings"]
    assert random.getstate() == stream
    assert patch.read_bytes() == original
    assert [(atom.index, atom.name, atom.residue.id, atom.residue.name,
             atom.residue.chain.id) for atom in model.topology.atoms()] == atoms
    assert [(one.index, two.index) for one, two in model.topology.bonds()] == bonds
    np.testing.assert_array_equal(model.positions.value_in_unit(openmm.unit.nanometer), positions)
    assert model.topology.getPeriodicBoxVectors() == box
    assert openmm.XmlSerializer.serialize(system) == serialized
    assert not disabled.root.exists()
    assert 0 < audit.bytes <= MAX_TOTAL
    print(f"POPC observer receipt: {len(atoms)} atoms; {audit.bytes} snapshot bytes; {elapsed:.3f} seconds")
