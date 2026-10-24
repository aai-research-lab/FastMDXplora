"""Every stage of a run holds a temperature, so its integrator has to.

`verlet` and `variable_verlet` have no thermostat, and the runner builds one
integrator for every stage, so the NVT stage and production in either
ensemble were constant-energy dynamics. Run on a solvated peptide at "300 K"
the temperature sat at 169-181 K through equilibration and production, the
kinetic energy it started with having filled the potential, while the
methods text said "at 300 K with a friction coefficient of 1 ps^-1".

`brownian` at the default friction (1 per ps) and 2 fs went to NaN within 50
steps: its random step, sqrt(2 kT dt / (m gamma)), moved a hydrogen about
0.1 nm a step.
"""

from __future__ import annotations

import math

import pytest

from fastmdxplora.config.loader import ConfigError, validate_config
from fastmdxplora.simulation.runner import (
    BROWNIAN_LARGEST_STEP_NM,
    check_integrator,
    least_brownian_friction,
)


def _simulation(**settings):
    return {"systems": [{"id": "a", "system": "x.pdb"}], "simulation": dict(settings)}


def _check(config):
    validate_config(config, require_systems=True)


class TestAnIntegratorWithNoThermostatIsRefused:

    @pytest.mark.parametrize("name", ["verlet", "variable_verlet"])
    def test_in_the_config(self, name):
        with pytest.raises(ConfigError, match="no thermostat") as caught:
            _check(_simulation(integrator=name))
        assert "langevin_middle" in str(caught.value)
        refusal = caught.value.refusal
        assert refusal.code == "config.option.not_permitted"
        assert refusal.details["option"] == "integrator"
        assert refusal.details["context"] == "simulation"
        assert refusal.details["suggestion"] == "langevin_middle"
        assert name not in refusal.details["permitted"]

    @pytest.mark.parametrize("name", ["verlet", "variable_verlet"])
    def test_in_a_direct_call(self, name):
        with pytest.raises(ValueError, match="no thermostat"):
            check_integrator(name, temperature_K=300.0, friction_per_ps=1.0,
                             timestep_fs=2.0)

    @pytest.mark.parametrize("name", ["langevin_middle", "langevin",
                                      "variable_langevin"])
    def test_a_thermostatted_one_is_not(self, name):
        _check(_simulation(integrator=name))


class TestBrownianDynamicsIsDampedEnoughForItsStep:

    def test_the_floor_is_the_step_bound(self):
        """2 kT dt / (m s^2): about 99.5 per ps at 300 K and 2 fs."""
        kT = 0.0083144626 * 300.0
        expected = 2 * kT * 0.002 / (1.008 * BROWNIAN_LARGEST_STEP_NM ** 2)
        assert least_brownian_friction(300.0, 2.0) == pytest.approx(expected)
        assert least_brownian_friction(300.0, 2.0) == pytest.approx(99.0, abs=1.0)
        # Linear in the step and the temperature.
        assert least_brownian_friction(300.0, 4.0) == pytest.approx(
            2 * least_brownian_friction(300.0, 2.0))
        assert least_brownian_friction(600.0, 2.0) == pytest.approx(
            2 * least_brownian_friction(300.0, 2.0))

    def test_the_defaults_are_refused(self):
        least = least_brownian_friction(300.0, 2.0)
        with pytest.raises(ConfigError,
                           match=f"friction of at least {math.ceil(least)} per ps") as caught:
            _check(_simulation(integrator="brownian"))
        assert caught.value.refusal.code == "config.option.conflicting"
        assert "simulation.friction_per_ps" in caught.value.refusal.details["options"]

    def test_the_floor_said_is_rounded_up(self):
        """At 298 K the floor is 98.3: "at least 98" with 98 given was a
        refusal of the number it named."""
        with pytest.raises(ConfigError, match="at least 99 per ps; 98 was given"):
            _check(_simulation(integrator="brownian", temperature_K=298.0,
                               friction_per_ps=98.0))

    def test_enough_friction_is_accepted(self):
        _check(_simulation(integrator="brownian", friction_per_ps=150.0))
        _check(_simulation(integrator="brownian", friction_per_ps=30.0,
                                    timestep_fs=0.5))

    def test_a_longer_step_needs_more(self):
        least = least_brownian_friction(300.0, 4.0)
        with pytest.raises(ConfigError, match=f"at least {math.ceil(least)} per ps"):
            _check(_simulation(integrator="brownian", friction_per_ps=150.0,
                                        timestep_fs=4.0))


class TestEveryRunIsChecked:
    """A sweep or a system's own settings make the runs; each is judged."""

    def test_a_swept_integrator(self):
        config = _simulation()
        config["sweep"] = {"simulation.integrator": ["langevin_middle", "verlet"]}
        with pytest.raises(ConfigError, match="Run .*verlet.*no thermostat"):
            _check(config)

    def test_a_system_s_own_integrator(self):
        config = {"systems": [{"id": "a", "system": "x.pdb"},
                              {"id": "b", "system": "y.pdb",
                               "simulation": {"integrator": "variable_verlet"}}]}
        with pytest.raises(ConfigError, match="Run b .*own settings.*no thermostat"):
            _check(config)

    def test_one_system_with_its_own_friction_is_named(self):
        config = {"systems": [{"id": "only", "system": "x.pdb",
                               "simulation": {"integrator": "brownian",
                                              "friction_per_ps": 5.0}}]}
        with pytest.raises(ConfigError, match="Run only "):
            _check(config)

    def test_a_sweep_the_refusal_does_not_rest_on_is_not_blamed(self):
        config = _simulation(integrator="verlet")
        config["sweep"] = {"simulation.temperature_K": [300, 310]}
        with pytest.raises(ConfigError) as caught:
            _check(config)
        assert not str(caught.value).startswith("Run ")

    def test_a_swept_friction_that_is_enough(self):
        config = _simulation(integrator="brownian")
        config["sweep"] = {"simulation.friction_per_ps": [150, 200]}
        _check(config)

    def test_a_swept_step_that_is_too_long(self):
        config = _simulation(integrator="brownian", friction_per_ps=120.0)
        config["sweep"] = {"simulation.timestep_fs": [2, 4]}
        with pytest.raises(ConfigError, match="at least 198"):
            _check(config)


class TestARecordOfAnOlderStudyCanStillBeRead:
    """A study run with Verlet before the refusal is reported, not refused."""

    def test_its_settings_validate_as_a_record(self):
        validate_config(_simulation(integrator="verlet"), as_record=True)

    def test_and_are_read_back_by_phase(self, tmp_path):
        import yaml

        from fastmdxplora.config.recorded import phase_settings

        source = tmp_path / "config.yml"
        source.write_text(yaml.safe_dump(_simulation(integrator="verlet")))
        assert phase_settings(source, "simulation")["integrator"] == "verlet"

    def test_a_check_with_no_systems_still_refuses(self):
        """What the GUI's check and a defaults file are read with."""
        with pytest.raises(ConfigError, match="no thermostat"):
            validate_config({"simulation": {"integrator": "verlet"}})

    def test_a_study_that_does_not_simulate_is_not_judged(self):
        config = _simulation(integrator="verlet")
        config["include_phase"] = ["report"]
        _check(config)


@pytest.mark.slow
class TestTheFloorIsWhereTheRunFails:
    """Measured, not argued: at the floor a water box runs, at half it does not."""

    @staticmethod
    def _run(friction, timestep_fs, steps=600):
        omm = pytest.importorskip("openmm")
        import numpy as np
        from openmm import app, unit

        field = app.ForceField("amber14-all.xml", "amber14/tip3p.xml")
        modeller = app.Modeller(app.Topology(), [])
        modeller.addSolvent(field, model="tip3p",
                            boxSize=omm.Vec3(2.5, 2.5, 2.5) * unit.nanometer)
        system = field.createSystem(modeller.topology, nonbondedMethod=app.PME,
                                    nonbondedCutoff=1.0 * unit.nanometer,
                                    constraints=app.HBonds)
        platform = omm.Platform.getPlatformByName("CPU")
        start = omm.Context(system, omm.VerletIntegrator(0.001), platform)
        start.setPositions(modeller.positions)
        omm.LocalEnergyMinimizer.minimize(start, 10, 200)
        positions = start.getState(getPositions=True).getPositions()

        integrator = omm.BrownianIntegrator(300 * unit.kelvin,
                                            friction / unit.picosecond,
                                            timestep_fs * unit.femtoseconds)
        integrator.setRandomNumberSeed(1)
        context = omm.Context(system, integrator, platform)
        context.setPositions(positions)
        for _ in range(steps // 50):
            try:
                integrator.step(50)
            except Exception:  # noqa: BLE001 - OpenMM raises on NaN coordinates
                return False
            x = context.getState(getPositions=True).getPositions(asNumpy=True)
            if not np.all(np.isfinite(x.value_in_unit(unit.nanometer))):
                return False
        return True

    def test_at_the_floor_it_runs(self):
        floor = least_brownian_friction(300.0, 2.0)
        assert self._run(math.ceil(floor), 2.0)

    def test_at_half_of_it_it_does_not(self):
        floor = least_brownian_friction(300.0, 2.0)
        assert not self._run(floor / 2.0, 2.0)


class TestARecordOfAVerletRunIsDescribedAsWhatItWas:
    """A record from before the refusal: no thermostat, so no friction."""

    def test_the_methods_say_there_was_no_thermostat(self):
        from pathlib import Path

        from fastmdxplora.report.methods import methods_paragraphs

        sim = {"parameters": {"timestep_fs": 1.0, "integrator": "verlet",
                              "temperature_K": 300.0, "friction_per_ps": 1.0,
                              "production_steps": 1000}}
        text = methods_paragraphs(Path("."), {}, sim)
        assert "no thermostat" in text
        assert "friction" not in text
        assert "at 300.0 K" not in text
        assert "no stage was held at a temperature" in text

    def test_a_thermostatted_run_keeps_its_sentence(self):
        from pathlib import Path

        from fastmdxplora.report.methods import methods_paragraphs

        sim = {"parameters": {"timestep_fs": 2.0, "integrator": "langevin_middle",
                              "temperature_K": 300.0, "friction_per_ps": 1.0,
                              "production_steps": 1000}}
        text = methods_paragraphs(Path("."), {}, sim)
        assert "at 300.0 K with a friction coefficient of 1.0 ps⁻¹" in text
