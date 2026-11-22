"""A State saved at the end of an NPT stage restores into a System prepared without a barostat.

The setup phase writes a System with no barostat, and the runner adds the one
the requested ensemble needs only after the saved State is loaded. A State
captured from a constant-pressure run carries the barostat's pressure and
temperature as context parameters, which the unprepared System has no name for,
so OpenMM refused to load it and the run stopped before its first stage, for
constant-volume and constant-pressure production alike.
"""

from __future__ import annotations

import os

import numpy as np
import pytest

openmm = pytest.importorskip("openmm")
from openmm import app, unit  # noqa: E402

from fastmdxplora.simulation import runner  # noqa: E402

REQUESTED_BAR = 1.0
SAVED_BAR = 3.0
SAVED_BOX_NM = 2.1
SAVED_TIME_PS = 1.5
SAVED_POSITIONS = np.array([[0.1, 0.2, 0.3], [0.5, 0.2, 0.3]])
SAVED_VELOCITIES = np.array([[0.05, -0.02, 0.01], [-0.05, 0.02, 0.03]])


def _system(*, barostat: bool):
    """Two argon-like particles in a periodic box, with one custom global parameter."""
    system = openmm.System()
    force = openmm.NonbondedForce()
    force.setNonbondedMethod(openmm.NonbondedForce.CutoffPeriodic)
    force.setCutoffDistance(0.5)
    for _ in range(2):
        system.addParticle(40.0)
        force.addParticle(0.0, 0.3, 0.1)
    system.addForce(force)
    drift = openmm.CustomExternalForce("drift_scale*x")
    drift.addGlobalParameter("drift_scale", 1.0)
    drift.addParticle(0, [])
    system.addForce(drift)
    system.setDefaultPeriodicBoxVectors(*(openmm.Vec3(*row) for row in np.eye(3) * 2.0))
    if barostat:
        system.addForce(openmm.MonteCarloBarostat(SAVED_BAR * unit.bar, 300 * unit.kelvin))
    return system


def _prepared(tmp_path, *, parameters: bool, extra=None):
    """Write what the setup phase writes, with a State captured from an NPT stage.

    The System file has no barostat. The State comes from a context that had one, so
    it carries the barostat's parameters when ``parameters`` is true. ``extra`` adds
    another named parameter to the State, which the System has no name for either.
    """
    system_xml = tmp_path / "system.xml"
    system_xml.write_text(openmm.XmlSerializer.serialize(_system(barostat=False)))

    topology = app.Topology()
    residue = topology.addResidue("AR", topology.addChain())
    for i in range(2):
        topology.addAtom(f"AR{i}", app.Element.getBySymbol("Ar"), residue)
    topology_pdb = tmp_path / "topology.pdb"
    with topology_pdb.open("w") as fh:
        app.PDBFile.writeFile(topology, SAVED_POSITIONS * unit.nanometer, fh)

    context = openmm.Context(_system(barostat=True), openmm.VerletIntegrator(0.001),
                             openmm.Platform.getPlatformByName("CPU"), {"Threads": "1"})
    context.setPeriodicBoxVectors(*(openmm.Vec3(*row) for row in np.eye(3) * SAVED_BOX_NM))
    context.setPositions(SAVED_POSITIONS)
    context.setVelocities(SAVED_VELOCITIES)
    context.setTime(SAVED_TIME_PS)
    context.setParameter("drift_scale", 2.5)
    state = context.getState(getPositions=True, getVelocities=True, getParameters=parameters)
    text = openmm.XmlSerializer.serialize(state)
    if extra:
        text = text.replace("<Parameters ", "<Parameters " + "".join(
            f'{name}="{value}" ' for name, value in extra.items()))
    state_xml = tmp_path / "state.xml"
    state_xml.write_text(text)
    return system_xml, state_xml, topology_pdb


def _run(tmp_path, spy, monkeypatch, *, ensemble: str, parameters: bool = True, extra=None):
    """Run no steps at all, so what comes out is what went in."""
    system_xml, state_xml, topology_pdb = _prepared(tmp_path, parameters=parameters, extra=extra)
    original = runner._validate_state_finite

    def watch(omm, simulation, *, stage):
        spy[stage] = simulation.context.getState(
            getPositions=True, getVelocities=True, getParameters=True)
        spy["simulation"] = simulation
        return original(omm, simulation, stage=stage)

    monkeypatch.setattr(runner, "_validate_state_finite", watch)
    return runner.run_simulation(
        system_xml=system_xml, state_xml=state_xml, topology_pdb=topology_pdb,
        output_dir=tmp_path / "run", nvt_steps=0, npt_steps=0, production_steps=0,
        minimize=False, platform="CPU", ensemble=ensemble, pressure_bar=REQUESTED_BAR,
        live_telemetry=False)


def _is_as_saved(state) -> None:
    """Exactly the physical state that was saved: positions, velocities, box and time."""
    box = state.getPeriodicBoxVectors(asNumpy=True).value_in_unit(unit.nanometer)
    assert np.array_equal(state.getPositions(asNumpy=True).value_in_unit(unit.nanometer),
                          SAVED_POSITIONS)
    assert np.array_equal(state.getVelocities(asNumpy=True).value_in_unit(
        unit.nanometer / unit.picosecond), SAVED_VELOCITIES)
    assert np.array_equal(box, np.eye(3) * SAVED_BOX_NM)
    assert state.getTime().value_in_unit(unit.picosecond) == SAVED_TIME_PS


@pytest.mark.parametrize("ensemble", ["nvt", "npt"])
def test_a_saved_npt_state_loads_and_the_run_keeps_what_it_saved(tmp_path, monkeypatch, ensemble):
    spy: dict = {}
    _run(tmp_path, spy, monkeypatch, ensemble=ensemble)
    loaded = spy["loading state.xml"]
    _is_as_saved(loaded)
    # A parameter the System does have is restored, not discarded with the barostat's.
    assert dict(loaded.getParameters()) == {"drift_scale": 2.5}


def test_npt_production_gets_the_pressure_it_asked_for_not_the_saved_one(tmp_path, monkeypatch):
    spy: dict = {}
    result = _run(tmp_path, spy, monkeypatch, ensemble="npt")
    assert result.barostat == "isotropic"
    assert result.pressure_bar_used == REQUESTED_BAR
    context = spy["simulation"].context
    assert context.getParameter("MonteCarloPressure") == REQUESTED_BAR
    assert context.getParameter("drift_scale") == 2.5


def test_constant_volume_production_gets_no_barostat(tmp_path, monkeypatch):
    spy: dict = {}
    result = _run(tmp_path, spy, monkeypatch, ensemble="nvt")
    assert result.barostat is None
    assert "MonteCarloPressure" not in spy["simulation"].context.getParameters()


@pytest.mark.parametrize("ensemble", ["nvt", "npt"])
def test_what_was_left_out_is_said_in_the_record_and_the_log(tmp_path, monkeypatch, ensemble):
    result = _run(tmp_path, {}, monkeypatch, ensemble=ensemble)
    said = [n for n in result.notes if "MonteCarloPressure" in n]
    assert len(said) == 1
    assert "MonteCarloTemperature" in said[0]
    assert said[0] in result.log_file.read_text()


@pytest.mark.parametrize("ensemble", ["nvt", "npt"])
def test_a_state_without_parameters_still_loads_and_says_nothing(tmp_path, monkeypatch, ensemble):
    spy: dict = {}
    result = _run(tmp_path, spy, monkeypatch, ensemble=ensemble, parameters=False)
    loaded = spy["loading state.xml"]
    _is_as_saved(loaded)
    assert dict(loaded.getParameters()) == {"drift_scale": 1.0}
    assert not [n for n in result.notes if "MonteCarlo" in n]


@pytest.mark.parametrize("ensemble", ["nvt", "npt"])
def test_a_parameter_that_is_not_the_barostats_is_refused(tmp_path, monkeypatch, ensemble):
    with pytest.raises(ValueError, match="sets no_such_parameter, which"):
        _run(tmp_path, {}, monkeypatch, ensemble=ensemble, extra={"no_such_parameter": 1.0})


@pytest.mark.skipif(not hasattr(os, "fchmod"), reason="a study writes its records with os.fchmod")
def test_a_refused_simulation_phase_exits_nonzero(tmp_path, monkeypatch, capsys):
    """The study's exit code is its failed phase's, not zero because the command parsed."""
    from fastmdxplora.cli.main import main
    from fastmdxplora.orchestrator import FastMDXplora
    from fastmdxplora.refusals import StudyError

    def refuse(*args, **kwargs):
        raise StudyError("The saved state sets no_such_parameter, which the system has none for.")

    monkeypatch.setattr(FastMDXplora, "_resolve_phase_runner", staticmethod(lambda phase: refuse))
    system = tmp_path / "structure.pdb"
    system.write_text("ATOM      1  N   ALA A   1       0.000   0.000   0.000"
                      "  1.00  0.00           N\nEND\n")
    assert main(["simulate", "--system", str(system), "--output", str(tmp_path / "study")]) == 1
    assert "no_such_parameter" in capsys.readouterr().out
