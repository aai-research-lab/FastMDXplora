"""A run without a barostat says what density it is simulating at.

Solvation packs a box that is not at the density of water. Two real runs
agreed on how far off: one held 0.9215 g/mL for every step of NVT because
nothing could correct it, and the same system under NPT contracted 37.3 to
33.2 nm^3 -- 11% by volume -- to reach 1.03.

An under-dense box has voids, water accelerates into them, and both runs that
failed with non-finite coordinates failed in water. That is suggestive rather
than established, which is why this reports and does not correct: skipping NPT
is a legitimate choice. What is not legitimate is making it silently.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import fastmdxplora.simulation.runner as runner
from fastmdxplora.simulation.runner import _warn_density_was_never_equilibrated


@pytest.fixture()
def warnings(monkeypatch):
    """What the function warned, captured from the logger it actually uses.

    `caplog` attaches to the root logger, and this package configures its own
    with propagation off -- so the text arrived on stdout and the fixture saw
    nothing. Alone the tests passed, because whatever turns propagation off
    had not run yet; in the full suite they failed. The two that asserted
    silence passed either way, which is worse: they were vacuous.
    """
    said: list[str] = []
    monkeypatch.setattr(
        runner.logger, "warning",
        lambda msg, *args: said.append(msg % args if args else msg))
    return said


class _Mass(float):
    def value_in_unit(self, _unit):
        return float(self)


class _Volume(float):
    def value_in_unit(self, _unit):
        return float(self)


def _simulation(volume_nm3: float, mass_da: float, particles: int = 4):
    per = mass_da / particles
    system = SimpleNamespace(
        getNumParticles=lambda: particles,
        getParticleMass=lambda i: _Mass(per),
    )
    state = SimpleNamespace(getPeriodicBoxVolume=lambda: _Volume(volume_nm3))
    simulation = SimpleNamespace(
        context=SimpleNamespace(getState=lambda **k: state))
    return simulation, system


class TestItNamesTheDensity:
    def test_the_measured_run_is_reproduced(self, warnings) -> None:
        """43.5717 nm^3 holding 24,180 Da read 0.9215 g/mL on a real run."""
        simulation, system = _simulation(43.5717, 24180.0)
        _warn_density_was_never_equilibrated(
            simulation, system, temperature_K=300.0)
        assert warnings, "it said nothing at all"
        assert "0.921" in warnings[0] or "0.922" in warnings[0]

    def test_it_says_what_to_do_about_it(self, warnings) -> None:
        simulation, system = _simulation(43.5717, 24180.0)
        _warn_density_was_never_equilibrated(
            simulation, system, temperature_K=300.0)
        assert warnings and "npt_steps" in warnings[0]

    def test_a_packed_box_still_reports_its_number(self, warnings) -> None:
        """It states the density rather than judging it: how far off is
        acceptable depends on the system, and a membrane is not a protein."""
        simulation, system = _simulation(43.5717, 26750.0)
        _warn_density_was_never_equilibrated(
            simulation, system, temperature_K=300.0)
        assert warnings and ("1.019" in warnings[0] or "1.02" in warnings[0])


class TestItNeverStopsARunThatIsOtherwiseFine:
    def test_a_zero_volume_says_nothing(self, warnings) -> None:
        simulation, system = _simulation(0.0, 24180.0)
        _warn_density_was_never_equilibrated(
            simulation, system, temperature_K=300.0)
        assert warnings == []

    def test_an_unreadable_state_says_nothing(self, warnings) -> None:
        broken = SimpleNamespace(
            context=SimpleNamespace(
                getState=lambda **k: (_ for _ in ()).throw(RuntimeError("no"))))
        _warn_density_was_never_equilibrated(
            broken, SimpleNamespace(), temperature_K=300.0)
        assert warnings == []


class TestItFiresOnlyWhereThereIsNoBarostat:
    def test_the_call_sits_under_the_skipped_npt_branch(self) -> None:
        """A run whose density is corrected somewhere needs no warning.

        The guard grew a clause. It used to ask `npt_steps > 0`, which was
        the same question as "is there a barostat" until `ensemble` became
        its own setting. A resumed segment has no equilibration and a
        barostat, so the old condition warned that a run at constant
        pressure was at fixed volume -- caught on ubiquitin by reading the
        segment banner against the volume it produced.

        The shape this test is about has not changed: the warning sits
        inside the skipped-NPT branch, and before the barostat is added.
        """
        from pathlib import Path
        import fastmdxplora.simulation.runner as runner

        source = Path(runner.__file__).read_text(encoding="utf-8")
        guard = source.index(
            'if not plan["npt_steps"] > 0 and not wants_npt_production:')
        call = source.index("_warn_density_was_never_equilibrated(\n", guard)
        added = source.index("barostat_index = _add_barostat(", guard)
        assert guard < call < added

    def test_the_guard_asks_about_production_and_not_only_the_stage(self) -> None:
        """Both halves, so neither can quietly go back to the other.

        Dropping the ensemble clause makes a correct segment warn. Dropping
        the steps clause makes a run that never equilibrates stay silent,
        which is the failure the warning was written for.
        """
        from pathlib import Path
        import fastmdxplora.simulation.runner as runner

        source = Path(runner.__file__).read_text(encoding="utf-8")
        assert 'not plan["npt_steps"] > 0' in source
        assert "not wants_npt_production" in source


class TestTheCaptureItselfWorks:
    """The first version of these tests used `caplog`, which sees the root
    logger while this package logs through its own with propagation off. The
    two tests asserting silence passed either way, so they were checking
    nothing. A fixture that cannot observe a warning makes every assertion
    about silence vacuous, and that is worth one test of its own."""

    def test_a_warning_is_actually_observed(self, warnings) -> None:
        simulation, system = _simulation(43.5717, 24180.0)
        _warn_density_was_never_equilibrated(
            simulation, system, temperature_K=300.0)
        assert len(warnings) == 1

    def test_the_fixture_sees_a_direct_call(self, warnings) -> None:
        """If this ever stops holding, every 'says nothing' test above has
        quietly stopped testing anything."""
        runner.logger.warning("density %.3f", 0.5)
        assert warnings == ["density 0.500"]
