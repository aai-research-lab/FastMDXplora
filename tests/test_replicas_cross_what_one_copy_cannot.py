"""Replica exchange: REST2's scaling, the bookkeeping of how replicas mixed,
the settings refused, and a short run of each kind through the driver."""

from __future__ import annotations

import json

import numpy as np
import pytest

openmm = pytest.importorskip("openmm")
pytest.importorskip("openmmtools")
from openmm import app, unit  # noqa: E402

KJ = unit.kilojoule_per_mole


@pytest.fixture(scope="module")
def dipeptide():
    from openmmtools import testsystems

    return testsystems.AlanineDipeptideExplicit()


def test_rest2_scales_the_solute_and_leaves_the_rest(dipeptide):
    """At scale s the energy is s E(solute) + sqrt(s) E(solute-water) + E(rest),
    exactly: fitted at three scales it predicts a fourth, and unscaled it is
    the original system's."""
    from fastmdxplora.simulation.replica_exchange import rest_values, with_rest2

    system = dipeptide.system
    for force in system.getForces():
        if isinstance(force, openmm.NonbondedForce):
            force.setUseDispersionCorrection(False)
    scaled = with_rest2(system, set(range(22)))
    platform = openmm.Platform.getPlatformByName("Reference")
    context = openmm.Context(scaled, openmm.VerletIntegrator(0.001), platform)
    context.setPositions(dipeptide.positions)

    def energy(scale: float) -> float:
        for name, value in rest_values(scale).items():
            context.setParameter(name, value)
        return context.getState(getEnergy=True).getPotentialEnergy().value_in_unit(KJ)

    original = openmm.Context(system, openmm.VerletIntegrator(0.001), platform)
    original.setPositions(dipeptide.positions)
    assert energy(1.0) == pytest.approx(
        original.getState(getEnergy=True).getPotentialEnergy().value_in_unit(KJ), abs=1e-3)
    fitted = [1.0, 0.6, 0.3]
    weights = np.linalg.solve([[s, np.sqrt(s), 1.0] for s in fitted],
                              [energy(s) for s in fitted])
    for scale in (0.8, 0.45):
        assert energy(scale) == pytest.approx(
            weights @ [scale, np.sqrt(scale), 1.0], abs=1e-3)
    # Within the solute and between it and water, both are felt.
    assert weights[0] != pytest.approx(0) and weights[1] != pytest.approx(0)


def test_a_force_field_rest2_cannot_scale_is_refused(dipeptide):
    from fastmdxplora.refusals import StudyError
    from fastmdxplora.simulation.replica_exchange import with_rest2

    system = openmm.XmlSerializer.deserialize(openmm.XmlSerializer.serialize(dipeptide.system))
    extra = openmm.CustomNonbondedForce("0")
    for _ in range(system.getNumParticles()):
        extra.addParticle([])
    system.addForce(extra)
    with pytest.raises(StudyError, match="Use `method: temperature`"):
        with_rest2(system, {0, 1})


def test_round_trips_are_bottom_to_top_and_back():
    from fastmdxplora.simulation.replica_exchange import round_trips

    states = np.array([[0, 2], [1, 1], [2, 0], [1, 1], [0, 2], [2, 0], [0, 2]])
    # The first replica goes 0 -> 2 -> 0 -> 2 -> 0: two round trips; the
    # second starts at the top, so its first return to the bottom is no trip.
    assert round_trips(states) == [2, 1]


def test_neighbour_acceptance_and_gaps_are_said():
    from fastmdxplora.simulation.replica_exchange import Plan, mixing_record

    accepted = np.array([[0, 30, 0], [30, 0, 2], [0, 2, 0]], dtype=float)
    proposed = np.array([[0, 50, 0], [50, 0, 50], [0, 50, 0]], dtype=float)
    plan = Plan("rest2", 3, 450.0, "protein", 500, 0)
    record = mixing_record(plan, [{}] * 3, np.array([[0, 1, 2]] * 4), accepted, proposed)
    assert record["neighbour_acceptance"] == [pytest.approx(0.6), pytest.approx(0.04)]
    assert "1-2" in record["gaps"] and "no_round_trip" in record


@pytest.mark.parametrize("block, said", [
    ({"method": "hamiltonian"}, "rest2 or temperature"),
    ({"replicas": 1}, "from 2 to 128"),
    ({"exchange_interval_steps": 2}, "at least 10"),
    ({"temperatures": [300, 310]}, "has no setting temperatures"),
])
def test_settings_that_mean_nothing_are_refused(block, said):
    from fastmdxplora.config.loader import ConfigError
    from fastmdxplora.simulation.replica_exchange import plan_from_block

    with pytest.raises(ConfigError, match=said):
        plan_from_block(block)


def test_a_bias_beside_replica_exchange_is_refused():
    from fastmdxplora.config.loader import ConfigError, _check_replica_exchange

    with pytest.raises(ConfigError, match="cannot be run with metadynamics"):
        _check_replica_exchange({"replica_exchange": {}, "metadynamics": {"sigma": 0.1}})


def test_a_replica_exchange_run_is_not_split():
    from fastmdxplora.simulation.resume import segmentability

    verdict = segmentability({"simulation": {"replica_exchange": {"method": "rest2"}}})
    assert verdict.allowed is False and verdict.method == "replica_exchange"


@pytest.mark.slow
@pytest.mark.parametrize("method, top", [("rest2", 400.0), ("temperature", 330.0)])
def test_a_short_run_writes_the_frames_at_the_conditions_of_interest(
        dipeptide, tmp_path, method, top):
    from fastmdxplora.simulation.replica_exchange import run_replica_exchange

    context = openmm.Context(dipeptide.system, openmm.VerletIntegrator(0.001))
    context.setPositions(dipeptide.positions)
    (tmp_path / "system.xml").write_text(openmm.XmlSerializer.serialize(dipeptide.system))
    (tmp_path / "state.xml").write_text(
        openmm.XmlSerializer.serialize(context.getState(getPositions=True)))
    with open(tmp_path / "topology.pdb", "w") as handle:
        app.PDBFile.writeFile(dipeptide.topology, dipeptide.positions, handle)
    done = run_replica_exchange(
        system_xml=tmp_path / "system.xml", state_xml=tmp_path / "state.xml",
        topology_pdb=tmp_path / "topology.pdb", output_dir=tmp_path / "run",
        block={"method": method, "replicas": 3, "max_temperature_K": top,
               "exchange_interval_steps": 20, "equilibration_iterations": 1,
               "solute": "not water"},
        temperature_K=300.0, timestep_fs=2.0, friction_per_ps=1.0, production_steps=400,
        pressure_bar=None, save_selection="not water", platform="CPU", random_seed=2)
    import mdtraj as md

    frames = md.load(str(done["trajectory"]), top=str(done["topology"]))
    assert frames.n_frames == 20 and frames.n_atoms == 22
    record = json.loads((tmp_path / "run" / "replica_exchange.json").read_text())
    assert record["method"] == method and len(record["neighbour_acceptance"]) == 2
    assert "not one continuous path" in record["trajectory"]
    assert (tmp_path / "run" / "energies.csv").read_text().startswith("time_ps,")


def test_the_mixing_analysis_reads_the_record(tmp_path):
    from fastmdxplora.analysis.replica_mixing import ReplicaMixing

    (tmp_path / "simulation").mkdir()
    (tmp_path / "simulation" / "replica_exchange.json").write_text(json.dumps({
        "method": "rest2", "replicas": 3, "neighbour_acceptance": [0.6, 0.04],
        "round_trips": [0, 0, 0], "round_trips_total": 0, "gaps": "Neighbours 1-2 ...",
        "no_round_trip": "No replica ..."}))
    analysis = ReplicaMixing(output_dir=tmp_path / "analysis")
    result = analysis.run(None)
    assert result.status == "ok", result.message
    assert result.data["acceptance"].tolist() == [0.6, 0.04]
    assert "gaps" in analysis.findings and "no_round_trip" in analysis.findings
