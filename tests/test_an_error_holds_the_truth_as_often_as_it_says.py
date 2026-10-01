"""An error on a mean holds the truth as often as an error should.

Calibrated on series with a known mean (``preregistration/stopping-calibration.md``),
the errors the analyses recorded and the stopping rule judged held the truth
within one error 43% of the time for a single run, where 68% is honest. Five
things were found to cause it, and each is pinned here: the inefficiency read
about the sample mean, the start discarded where the error reads smallest,
the start that maximises independent samples leaving a relaxation in the
mean, a check that halved the series and withheld at random, and a single
run's error judged as though it were known exactly.
"""

from __future__ import annotations

import json
import math

import numpy as np
import pytest

from fastmdxplora import statistics
from fastmdxplora.statistics import (
    RESOLVED_SAMPLES, correlation_is_resolved, detect_equilibration, statistical_inefficiency,
    summarise)


def _ar1(phi: float, n: int, seed: int, sigma: float = 1.0) -> np.ndarray:
    from scipy.signal import lfilter

    rng = np.random.default_rng(seed)
    noise = rng.normal(0.0, sigma * math.sqrt(1.0 - phi ** 2), n)
    return lfilter([1.0], [1.0, -phi], noise, zi=[phi * rng.normal(0.0, sigma)])[0]


def _by_each_lag(series: np.ndarray) -> float:
    """The initial positive sequence summed one lag at a time, as it was."""
    f = series - series.mean()
    n = f.size
    variance = float(np.mean(f * f))

    def c(lag: int) -> float:
        return 1.0 if lag == 0 else (1 - lag / n) * float(np.mean(f[: n - lag] * f[lag:]) / variance)

    total, pair = -1.0, 0
    while 2 * pair + 1 < n - 1:
        value = c(2 * pair) + c(2 * pair + 1)
        if value <= 0.0:
            break
        total += 2.0 * value
        pair += 1
    return max(1.0, total)


@pytest.mark.parametrize("phi,n,seed", [(0.0, 500, 1), (0.9, 3000, 2), (0.99, 4000, 3),
                                        (0.5, 7, 4), (0.95, 1001, 5)])
def test_the_inefficiency_by_transform_is_the_sum_over_lags(phi, n, seed):
    series = _ar1(phi, n, seed)
    assert statistical_inefficiency(series) == pytest.approx(_by_each_lag(series), rel=1e-9)


def test_the_inefficiency_for_the_mean_undoes_the_sample_means_bias():
    """At 25 times its inefficiency the estimate about the sample mean reads
    about a tenth low; corrected, the median is within a few percent."""
    raw, corrected = [], []
    for seed in range(200):
        series = _ar1(0.95, 975, seed)
        raw.append(statistical_inefficiency(series) / 39.0)
        corrected.append(statistics._for_the_mean(series)[0] / 39.0)
    assert np.median(raw) < 0.94
    assert abs(np.median(corrected) - 1.0) < 0.06


def test_the_degrees_of_freedom_are_the_frames_over_the_lags_summed():
    series = _ar1(0.95, 4000, 7)
    g, dof = statistics._for_the_mean(series)
    equilibrated, _ = summarise(series)
    assert 5 < dof < 60
    assert equilibrated.degrees_of_freedom > 0
    assert equilibrated.as_record()["degrees_of_freedom"] == equilibrated.degrees_of_freedom


def test_a_long_stationary_series_is_resolved():
    """The halving check withheld 19% to 41% of series 50 to 250 times their
    inefficiency. Counted on its own count, a series 100 times it is resolved."""
    resolved = [correlation_is_resolved(_ar1(0.95, 3900, seed)) for seed in range(100)]
    assert sum(resolved) >= 95


def test_a_short_correlated_series_is_not():
    resolved = [correlation_is_resolved(_ar1(0.95, 195, seed)) for seed in range(100)]
    assert sum(resolved) <= 5
    assert not correlation_is_resolved(_ar1(0.999, 4000, 7))


def test_its_mean_is_withheld_and_says_how_many_it_needs():
    equilibrated, why = summarise(_ar1(0.99, 1000, 8))
    assert why is not None and why.refusal.code == "analysis.sampling.correlation_unresolved"
    assert why.refusal.details["needed"] == RESOLVED_SAMPLES
    assert math.isnan(equilibrated.standard_error)


def test_the_start_is_the_latest_within_a_tenth_of_the_most():
    rng = np.random.default_rng(3)
    frames = np.arange(4000)
    series = 2.0 * np.exp(-frames / 150.0) + rng.normal(0, 0.1, frames.size)
    discard, _g, effective = detect_equilibration(series)
    candidates = np.unique(np.linspace(0, int(series.size * 2 / 3), num=40, dtype=int))
    counts = {int(s): (series.size - s) / statistical_inefficiency(series[s:]) for s in candidates}
    most = max(counts.values())
    assert effective >= (1 - statistics.EQUILIBRATION_TOLERANCE) * most
    assert all(count < (1 - statistics.EQUILIBRATION_TOLERANCE) * most
               for start, count in counts.items() if start > discard)


def test_after_a_discard_the_error_is_the_whole_runs_unless_the_discard_gained():
    """The start is chosen where what remains reads least correlated, so the
    error from what remains reads low; the whole run's error, scaled to the
    frames kept, does not."""
    for seed in range(40):
        series = 1.0 + _ar1(0.95, 2000, seed, sigma=0.05)
        discard, _g, _n = detect_equilibration(series)
        equilibrated, why = summarise(series)
        if why is not None or not discard:
            continue
        kept = series[discard:]
        g_kept, _ = statistics._for_the_mean(kept)
        from_kept = float(np.std(kept, ddof=1)) * math.sqrt(g_kept / kept.size)
        assert equilibrated.standard_error >= from_kept - 1e-12


def test_a_relaxation_that_dominates_keeps_its_own_error():
    rng = np.random.default_rng(5)
    frames = np.arange(3000)
    series = 5.0 * np.exp(-frames / 300.0) + rng.normal(0, 0.1, frames.size)
    equilibrated, why = summarise(series)
    assert why is None
    kept = series[equilibrated.discard:]
    g_kept, _ = statistics._for_the_mean(kept)
    assert equilibrated.standard_error == pytest.approx(
        float(np.std(kept, ddof=1)) * math.sqrt(g_kept / kept.size))


def _write(run, mean, error, effective, dof):
    where = run / "analysis" / "rmsd"
    where.mkdir(parents=True)
    (where / "options.json").write_text(json.dumps({"findings": {"mean": {
        "mean": mean, "standard_error": error, "effective_samples": effective,
        "degrees_of_freedom": dof, "n_frames": 1000, "discard": 0}}}), encoding="utf-8")


def test_the_rule_widens_an_error_by_its_degrees_of_freedom(tmp_path):
    from scipy.stats import t

    from fastmdxplora.simulation.stopping import StopTarget, judge

    # 0.0095 is within 0.01 as it stands, and 7% wider is not.
    _write(tmp_path / "run_0", 1.0, 0.0095, 80.0, 8.0)
    verdict = judge([tmp_path / "run_0"], [StopTarget("rmsd", standard_error=0.01)], 10.0)[0]
    assert verdict.error == pytest.approx(0.0095 * t.ppf(0.8413447460685429, 8.0))
    assert not verdict.met


def test_a_record_without_them_is_judged_as_it_stands(tmp_path):
    from fastmdxplora.simulation.stopping import StopTarget, judge

    where = tmp_path / "run_0" / "analysis" / "rmsd"
    where.mkdir(parents=True)
    (where / "options.json").write_text(json.dumps({"findings": {"mean": {
        "mean": 1.0, "standard_error": 0.009, "effective_samples": 80.0}}}), encoding="utf-8")
    verdict = judge([tmp_path / "run_0"], [StopTarget("rmsd", standard_error=0.01)], 10.0)[0]
    assert verdict.error == 0.009 and verdict.met


def test_one_run_is_judged_alone_only_on_enough_samples(tmp_path):
    from fastmdxplora.simulation.stopping import JUDGED_ALONE, StopTarget, judge

    _write(tmp_path / "run_0", 1.0, 0.005, 30.0, 1e9)
    verdict = judge([tmp_path / "run_0"], [StopTarget("rmsd", standard_error=0.01)], 10.0)[0]
    assert not verdict.met
    assert verdict.more_ns == pytest.approx(10.0 * (JUDGED_ALONE / 30.0 - 1.0))
    assert "independent samples" in verdict.said and "judged alone" in verdict.said

    _write(tmp_path / "run_1", 1.0, 0.005, 60.0, 1e9)
    assert judge([tmp_path / "run_1"], [StopTarget("rmsd", standard_error=0.01)], 10.0)[0].met


def test_replicas_need_no_such_floor(tmp_path):
    from fastmdxplora.simulation.stopping import StopTarget, judge

    runs = []
    for k, mean in enumerate((1.0, 1.002, 0.998)):
        _write(tmp_path / f"run_{k}", mean, 0.012, 30.0, 1e9)
        runs.append(tmp_path / f"run_{k}")
    assert judge(runs, [StopTarget("rmsd", standard_error=0.01)], 10.0)[0].met
