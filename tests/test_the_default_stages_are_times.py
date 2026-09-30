"""The default stage lengths are times, and the steps follow the timestep.

The defaults were step counts: 250,000 steps of NVT, 500,000 of NPT and
1,000,000 of production, which are 500 ps, 1 ns and 2 ns at 2 fs, as every
text said. At 4 fs, with hydrogen mass repartitioning, they ran 1 ns, 2 ns
and 4 ns. The runner, the cost estimate and the ensemble a config implies
each kept their own copy, and the continuation of a stopped study already
counted times. Decided with the user, 2026-09-30: 500 ps and 1 ns.
"""

from __future__ import annotations

import pytest

from fastmdxplora.simulation.lengths import (
    DEFAULT_NPT_NS, DEFAULT_NVT_NS, DEFAULT_PRODUCTION_NS, steps_in,
)
from fastmdxplora.simulation.runner import plan_stages


def _plan(timestep_fs: float, **given) -> dict[str, int]:
    asked = {"duration_ns": None, "nvt_steps": None, "npt_steps": None, "production_steps": None}
    return plan_stages(timestep_fs=timestep_fs, **{**asked, **given})


@pytest.mark.parametrize("timestep_fs, nvt, npt, production", [
    (2.0, 250_000, 500_000, 1_000_000),
    (4.0, 125_000, 250_000, 500_000),
    (1.0, 500_000, 1_000_000, 2_000_000),
])
def test_the_runner_runs_the_same_time_at_any_timestep(timestep_fs, nvt, npt, production):
    plan = _plan(timestep_fs)
    assert (plan["nvt_steps"], plan["npt_steps"], plan["production_steps"]) == (nvt, npt, production)
    ns = {stage: plan[f"{stage}_steps"] * timestep_fs * 1e-6 for stage in ("nvt", "npt", "production")}
    assert ns == pytest.approx({"nvt": DEFAULT_NVT_NS, "npt": DEFAULT_NPT_NS,
                                "production": DEFAULT_PRODUCTION_NS})


def test_what_is_stated_is_kept():
    assert _plan(4.0, nvt_steps=10, npt_steps=0, duration_ns=1.0) == {
        "nvt_steps": 10, "npt_steps": 0, "production_steps": 250_000}
    assert _plan(4.0, nvt_duration_ns=0.1)["nvt_steps"] == 25_000


def test_the_price_counts_the_steps_the_runner_takes():
    from fastmdxplora.cost import total_steps

    for timestep in (2.0, 4.0):
        plan = _plan(timestep)
        assert total_steps({"timestep_fs": timestep}) == sum(plan.values())
    assert total_steps({"timestep_fs": 4.0, "duration_ns": 10}) == (
        steps_in(10, 4.0) + steps_in(DEFAULT_NVT_NS, 4.0) + steps_in(DEFAULT_NPT_NS, 4.0))


def test_the_ensemble_is_read_from_the_same_stage():
    from fastmdxplora.simulation.ensembles import _npt_stage_steps

    assert _npt_stage_steps({"timestep_fs": 4.0}) == _plan(4.0)["npt_steps"]
    assert _npt_stage_steps({}) == _plan(2.0)["npt_steps"]
    assert _npt_stage_steps({"npt_duration_ns": 0}) == 0


def test_the_plan_says_what_runs():
    from fastmdxplora.gui.plan import plan_of

    said = {line["label"]: line["value"] for line in plan_of(
        {"systems": [{"system": "1L2Y"}], "simulation": {"timestep_fs": 4.0}})}
    assert said["Equilibration"] == "NVT 500 ps, then NPT 1 ns"
    assert said["Production"] == "2 ns, 4 fs steps"
