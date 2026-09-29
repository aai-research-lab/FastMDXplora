"""A budget works on a machine that has never been measured.

`--budget-hours` priced the study against a stored measurement of the
machine and refused every study on one without it. A new container, a
fresh cloud GPU and a new cluster node have none, so the budget was
unusable exactly where it is meant for: unattended runs. And the price was
asked for the platform "unknown", so even a machine measured on CUDA was
refused as a different machine.

Now the price is for the platform the simulation phase will choose, and a
machine with no usable measurement is measured on the spot, on the study's
own prepared system.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from fastmdxplora.agent import run_in_stages
from fastmdxplora.agent.staged import _what_it_will_run_on
from fastmdxplora.cost import Calibration, calibrate, load_calibration


@pytest.fixture(autouse=True)
def own_config_dir(monkeypatch, tmp_path):
    """Each test on a machine with no measurement stored."""
    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path / "config"))


def a_setup(*, config, output_dir):
    """Stands in for `explore`: setup records the solvated count."""
    if config.get("include_phase") == ["setup"]:
        where = Path(output_dir) / "setup"
        where.mkdir(parents=True, exist_ok=True)
        (where / "setup_parameters.json").write_text(json.dumps({"n_atoms_solvated": 60_000}))


def study(platform="CPU", steps=1000):
    return {"systems": [{"id": "a", "system": "x.pdb"}],
            "simulation": {"platform": platform, "production_steps": steps,
                           "nvt_steps": 0, "npt_steps": 0}}


class Timer:
    """Stands in for timing the prepared system: 1e-7 s per particle-step."""

    def __init__(self, fail: Exception | None = None) -> None:
        self.calls: list[tuple] = []
        self.fail = fail

    def __call__(self, setup_dir, *, platform_name, precision):
        self.calls.append((Path(setup_dir), platform_name, precision))
        if self.fail:
            raise self.fail
        return calibrate(particles=60_000, steps=2000, seconds=12.0,
                         platform_name=platform_name or "CPU", precision=precision)


def test_a_measured_machine_passes_without_naming_its_platform(tmp_path) -> None:
    # As `fastmdx explore --budget-hours` calls it: no platform given. The
    # config's platform is the one priced, so the measurement suits.
    calibrate(particles=30_000, steps=5_000, seconds=42.0, platform_name="CPU",
              precision="mixed")
    timer = Timer()
    staged = run_in_stages(study(), tmp_path / "out", budget_hours=10,
                           explore=a_setup, measure=timer)
    assert staged.refusal is None and staged.simulated
    assert timer.calls == [], "a suitable measurement is used, not retaken"


def test_an_unmeasured_machine_is_measured_on_the_prepared_system(tmp_path) -> None:
    timer = Timer()
    out = tmp_path / "out"
    staged = run_in_stages(study(), out, budget_hours=10, explore=a_setup, measure=timer)
    assert staged.simulated and staged.refusal is None
    [(where, platform, precision)] = timer.calls
    assert where == out / "setup" and (platform, precision) == ("CPU", "mixed")
    assert any("Measured this machine on the prepared system" in n for n in staged.notes)
    # 1e-7 s x 60,000 particles x 1,000 steps
    assert staged.estimate_seconds == pytest.approx(6.0)
    stored = load_calibration()
    assert stored is not None and stored.machine["platform"] == "CPU"


def test_the_measurement_still_holds_the_budget(tmp_path) -> None:
    staged = run_in_stages(study(steps=10**8), tmp_path / "out", budget_hours=1,
                           explore=a_setup, measure=Timer())
    assert not staged.simulated
    assert staged.refusal.code == "environment.budget.exhausted"
    assert staged.refusal.details["estimate_hours"] == pytest.approx(10**8 * 6e-3 / 3600)


def test_a_measurement_from_another_platform_is_retaken(tmp_path) -> None:
    calibrate(particles=30_000, steps=5_000, seconds=4.2, platform_name="CUDA",
              precision="mixed")
    timer = Timer()
    staged = run_in_stages(study(platform="CPU"), tmp_path / "out", budget_hours=10,
                           explore=a_setup, measure=timer)
    assert staged.simulated and len(timer.calls) == 1


def test_no_measurement_at_all_still_stops_it(tmp_path) -> None:
    staged = run_in_stages(study(), tmp_path / "out", budget_hours=10, explore=a_setup,
                           measure=Timer(fail=OSError("no system.xml")))
    assert not staged.simulated
    assert staged.refusal.code == "environment.calibration.absent"
    assert "no system.xml" in staged.refusal.message
    assert "has not been measured" in staged.refusal.message


def test_the_platform_is_the_one_the_config_names() -> None:
    assert _what_it_will_run_on(study(platform="CUDA"), "", "") == ("CUDA", "mixed")
    assert _what_it_will_run_on({"simulation": {"platform": "OpenCL", "precision": "single"}},
                                "", "") == ("OpenCL", "single")
    assert _what_it_will_run_on(study(platform="CUDA"), "CPU", "double") == ("CPU", "double")


def test_auto_is_resolved_as_the_simulation_phase_resolves_it() -> None:
    pytest.importorskip("openmm")
    from fastmdxplora.simulation.runner import _import_openmm, select_platform

    chosen = select_platform(_import_openmm(), requested="auto")[2]
    assert _what_it_will_run_on({"simulation": {}}, "", "") == (chosen, "mixed")


def test_a_real_prepared_system_is_timed(tmp_path) -> None:
    """With OpenMM: a small box of water, PME, constraints, as setup writes
    it, timed on the CPU platform."""
    openmm = pytest.importorskip("openmm")
    from openmm import app, unit

    from fastmdxplora.cost import measure_prepared_system

    forcefield = app.ForceField("amber14-all.xml", "amber14/tip3p.xml")
    modeller = app.Modeller(app.Topology(), [])
    modeller.addSolvent(forcefield, boxSize=openmm.Vec3(2.2, 2.2, 2.2) * unit.nanometer)
    system = forcefield.createSystem(modeller.topology, nonbondedMethod=app.PME,
                                     nonbondedCutoff=0.9 * unit.nanometer,
                                     constraints=app.HBonds)
    integrator = openmm.VerletIntegrator(1 * unit.femtosecond)
    context = openmm.Context(system, integrator, openmm.Platform.getPlatformByName("Reference"))
    context.setPositions(modeller.positions)
    context.setPeriodicBoxVectors(*modeller.topology.getPeriodicBoxVectors())
    state = context.getState(getPositions=True)
    setup = tmp_path / "setup"
    setup.mkdir()
    (setup / "system.xml").write_text(openmm.XmlSerializer.serialize(system))
    (setup / "state.xml").write_text(openmm.XmlSerializer.serialize(state))

    measured = measure_prepared_system(setup, platform_name="CPU", precision="mixed",
                                       steps=200, save=False)
    assert isinstance(measured, Calibration)
    assert measured.particles == system.getNumParticles() > 900
    assert measured.steps == 200 and measured.seconds > 0
    assert measured.machine["platform"] == "CPU"
    assert not Path(os.environ["FASTMDXPLORA_CONFIG_DIR"], "calibration.json").exists()


def test_without_a_prepared_system_there_is_nothing_to_time() -> None:
    pytest.importorskip("openmm")
    from fastmdxplora.cost import measure_prepared_system

    with pytest.raises(OSError):
        measure_prepared_system(Path(tempfile.mkdtemp()), platform_name="CPU", save=False)


def test_another_refusal_from_the_estimate_is_passed_on(tmp_path, monkeypatch) -> None:
    from fastmdxplora import cost
    from fastmdxplora.refusals import StudyError

    def undetermined(*args, **kwargs):
        raise StudyError("The particle count is not known yet.",
                         code="setup.structure.undetermined")

    monkeypatch.setattr(cost, "estimate_runs", undetermined)
    timer = Timer()
    staged = run_in_stages(study(), tmp_path / "out", budget_hours=10, explore=a_setup,
                           measure=timer)
    assert staged.refusal.code == "setup.structure.undetermined"
    assert timer.calls == [], "only a missing or stale measurement is measured"


def test_without_openmm_auto_names_no_platform(monkeypatch) -> None:
    from fastmdxplora.simulation import runner

    def absent():
        raise ImportError("no openmm")

    monkeypatch.setattr(runner, "_import_openmm", absent)
    assert _what_it_will_run_on({"simulation": {}}, "", "") == ("", "mixed")
