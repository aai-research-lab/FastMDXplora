"""A stated equilibration length of zero is zero, to everything that reads it.

`npt_duration_ns: 0` was read by the runner as unset, so it ran the default
nanosecond of NPT and produced at constant pressure, while the ensemble
resolver, the cost estimate and the run's record read it as no NPT stage and
constant-volume production. The record then said NVT of a run that was NPT.
`npt_steps: 0` was always read as zero; the duration now is too.
"""

from __future__ import annotations

import pytest

from fastmdxplora.cost import total_steps
from fastmdxplora.simulation.ensembles import resolve_ensemble
from fastmdxplora.simulation.runner import plan_stages


def _plan(**stated):
    return plan_stages(duration_ns=1.0, timestep_fs=2.0, nvt_steps=None, npt_steps=None,
                       production_steps=None, **stated)


def test_a_zero_npt_duration_runs_no_npt() -> None:
    assert _plan(npt_duration_ns=0)["npt_steps"] == 0


def test_a_zero_nvt_duration_runs_no_nvt() -> None:
    assert _plan(nvt_duration_ns=0.0)["nvt_steps"] == 0


def test_unset_is_still_the_default() -> None:
    plan = _plan()
    assert plan["npt_steps"] == 500_000  # 1 ns at 2 fs
    assert plan["nvt_steps"] == 250_000  # 500 ps


@pytest.mark.parametrize("npt_ns", [None, 0, 0.2])
def test_the_runner_and_the_resolver_agree_on_the_ensemble(npt_ns) -> None:
    stated = {} if npt_ns is None else {"npt_duration_ns": npt_ns}
    runner_says = "npt" if _plan(**stated)["npt_steps"] > 0 else "nvt"
    assert resolve_ensemble({"timestep_fs": 2.0, **stated}) == runner_says


@pytest.mark.parametrize("stated", [{"npt_duration_ns": 0}, {"nvt_duration_ns": 0},
                                    {"npt_duration_ns": 0.2, "nvt_duration_ns": 0.1}, {}])
def test_the_cost_estimate_counts_the_steps_the_runner_runs(stated) -> None:
    plan = _plan(**stated)
    counted = total_steps({"duration_ns": 1.0, "timestep_fs": 2.0, **stated})
    assert counted == plan["nvt_steps"] + plan["npt_steps"] + plan["production_steps"]
