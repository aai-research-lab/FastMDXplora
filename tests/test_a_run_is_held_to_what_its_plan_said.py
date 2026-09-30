"""A run is held to checks its plan states, and ticked against them after.

An Agent's plan said what would be built and run, and nothing of how the
result would be judged; the report then listed what the run could not
support, in prose, with nothing to say which checks it had passed. Asked
"did it pass?", the Agent had only its own idea of a good run.

The checks are one list in the convergence module. The plan states them
before the run, the report's convergence section ticks each after it
(passed, failed, or not judged and why), and the Agent is given the same
ticks. The thresholds are the ones the report's findings use, so a check and
a finding cannot disagree.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from fastmdxplora.report.convergence import CHECKS, assess_run


def _checks(result: dict) -> dict[str, tuple]:
    return {c["check"]: (c["passed"], c["detail"]) for c in result["checks"]}


def _steady(n: int = 500, centre: float = 0.0, width: float = 1.0, seed: int = 0) -> list[float]:
    return list(centre + width * np.random.RandomState(seed).normal(size=n))


class TestTheList:
    def test_each_check_once_in_the_order_judged(self):
        assert [key for key, _, _ in CHECKS] == [
            "equilibrated", "correlation", "sampled", "temperature", "energy"]

    def test_every_assessment_ticks_every_check(self):
        result = assess_run({"rmsd": _steady(centre=0.2, width=0.01)})
        assert [c["check"] for c in result["checks"]] == [key for key, _, _ in CHECKS]
        assert [c["said"] for c in result["checks"]] == [said for _, said, _ in CHECKS]


class TestTheTicks:
    def test_a_sound_run_passes_them_all(self):
        checks = _checks(assess_run(
            {"potential_energy": _steady(centre=-1e5, width=50),
             "temperature": _steady(centre=300, width=2, seed=1),
             "rmsd": _steady(centre=0.2, width=0.01, seed=2)},
            duration_ns=10, n_atoms=30000, target_temperature_K=300))
        assert all(passed is True for passed, _ in checks.values()), checks
        assert checks["temperature"][1].endswith("K against 300.0 K")

    def test_a_measure_still_moving_fails(self):
        rising = list(np.linspace(0.1, 0.5, 500) + 0.005 * np.random.RandomState(0).normal(size=500))
        passed, detail = _checks(assess_run({"rmsd": rising}))["equilibrated"]
        assert passed is False and detail == "still moving: rmsd"

    def test_too_short_to_say_is_not_a_pass(self):
        checks = _checks(assess_run({"rmsd": [0.1, 0.2, 0.3]}))
        assert checks["equilibrated"] == (None, "too short to say: rmsd")
        assert checks["sampled"][0] is False

    @pytest.mark.parametrize("mean, passed", [(304.9, True), (305.1, False)])
    def test_the_temperature_agrees_with_the_finding(self, mean, passed):
        result = assess_run({"temperature": _steady(centre=mean, width=0.01)},
                            target_temperature_K=300)
        assert _checks(result)["temperature"][0] is passed
        assert any("mean temperature" in f for f in result["findings"]) is (not passed)

    def test_the_energy_agrees_with_the_finding(self):
        wide = assess_run({"potential_energy": _steady(centre=-1e5, width=500)},
                          duration_ns=0.01, n_atoms=6000)
        assert _checks(wide)["energy"][0] is False
        assert any("potential energy moved" in f for f in wide["findings"])
        narrow = assess_run({"potential_energy": _steady(centre=-1e5, width=500)},
                            duration_ns=100, n_atoms=6000)
        assert _checks(narrow)["energy"][0] is True
        assert not any("potential energy moved" in f for f in narrow["findings"])

    def test_what_could_not_be_judged_says_why(self):
        checks = _checks(assess_run({"rmsd": _steady(centre=0.2, width=0.01)}))
        assert checks["temperature"] == (None, "no temperature was recorded")
        assert checks["energy"] == (None, "no potential energy was recorded")
        short = _checks(assess_run({"potential_energy": [1.0, 2.0, 3.0],
                                    "temperature": _steady(centre=300)}))
        assert short["energy"] == (None, "3 energy records, too few to judge")
        assert short["temperature"] == (None, "no target temperature was recorded")
        unsized = _checks(assess_run({"potential_energy": _steady()}))
        assert unsized["energy"] == (None, "the run's length or size was not recorded")


class TestSaidBefore:
    def test_in_the_plan_of_a_study_that_simulates(self):
        from fastmdxplora.gui.plan import plan_of

        lines = {line["label"]: line for line in plan_of({"systems": [{"id": "a", "system": "1UBQ"}]})}
        said = lines["Checked after"]["value"]
        assert said == "; ".join(short for _, _, short in CHECKS)
        assert "at least 10 independent samples per mean" in said
        assert not lines["Checked after"]["default"]

    def test_not_in_the_plan_of_a_study_that_does_not(self):
        from fastmdxplora.gui.plan import plan_of

        labels = [line["label"] for line in plan_of(
            {"systems": [{"id": "a", "system": "1UBQ"}], "include_phase": ["setup"]})]
        assert "Checked after" not in labels


def _study(root: Path) -> Path:
    """A run whose RMSD is still rising and whose thermostat held."""
    (root / "simulation").mkdir(parents=True)
    rng = np.random.RandomState(0)
    rows = ["Step,Potential Energy (kJ/mole),Temperature (K)"]
    rows += [f"{i},{-1e5 + 50 * rng.normal():.3f},{300 + 2 * rng.normal():.3f}" for i in range(300)]
    (root / "simulation" / "energy.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    (root / "simulation" / "simulation_parameters.json").write_text(json.dumps(
        {"duration_ns_actual": 10.0, "parameters": {"temperature_K": 300.0}}), encoding="utf-8")
    rmsd = root / "analysis" / "rmsd"
    rmsd.mkdir(parents=True)
    rising = np.linspace(0.1, 0.5, 300) + 0.005 * rng.normal(size=300)
    (rmsd / "rmsd.dat").write_text("".join(f"{v:.5f}\n" for v in rising), encoding="utf-8")
    return root


class TestTickedAfter:
    def test_in_the_report(self, tmp_path):
        from fastmdxplora.report.document import _convergence_section

        section = _convergence_section(_study(tmp_path / "study"))
        assert "### The checks this run was held to" in section
        assert ("| Each observable equilibrates before it is averaged | **failed** "
                "| still moving: rmsd |") in section
        assert "| The mean temperature is within 5 K of the target | passed |" in section

    def test_to_the_agent(self, tmp_path):
        from fastmdxplora.gui.agent_panel import _run_status

        root = _study(tmp_path / "study")

        class Runtime:
            active_root = root

            def snapshot(self):
                return {"active_run": str(root), "status": "idle"}

        status = _run_status(Runtime())
        assert "the checks this run was held to (as the report ticks them):" in status
        assert ("FAILED: each observable equilibrates before it is averaged "
                "(still moving: rmsd)") in status
        assert "passed: the mean temperature is within 5 K of the target" in status

    def test_nothing_to_tick_says_nothing(self, tmp_path):
        from fastmdxplora.gui.agent_panel import _checks_summary

        assert _checks_summary(tmp_path) == ""
        assert _checks_summary(None) == ""
