"""What the convergence view plots, from the package's own estimators.

The Analysis page shows a series' running mean, its block averaging, its
autocorrelation and where equilibration was taken to end. Each of those has
to agree with the mean and error the package records for the same series,
or the page and the findings say two things about one run.
"""

from __future__ import annotations

import json
import time

import numpy as np
import pytest

from fastmdxplora.statistics import (
    _for_the_mean,
    convergence_of,
    mean_record,
    statistical_inefficiency,
    summarise,
)


def _ar1(n: int, phi: float, seed: int = 0) -> np.ndarray:
    """AR(1) with unit innovations: g = (1 + phi) / (1 - phi) exactly."""
    rng = np.random.default_rng(seed)
    noise = rng.normal(size=n)
    out = np.empty(n)
    out[0] = noise[0] / np.sqrt(1.0 - phi ** 2)
    for i in range(1, n):
        out[i] = phi * out[i - 1] + noise[i]
    return out


def _json_ready(record) -> None:
    json.dumps(record, allow_nan=False)


class TestItAgreesWithWhatThePackageRecords:

    @pytest.mark.parametrize("series", [
        _ar1(20000, 0.9, seed=1),
        np.random.default_rng(2).normal(size=5000),
        _ar1(8000, 0.8, seed=3) + 8.0 * np.exp(-np.arange(8000) / 300.0),
        _ar1(300, 0.95, seed=4),
    ], ids=["ar1", "iid", "transient", "unresolved"])
    def test_the_equilibration_and_mean_are_summarises(self, series) -> None:
        record = convergence_of(series)
        equilibrated, why = summarise(series)
        said = record["equilibration"]
        assert said["discard_frames"] == equilibrated.discard
        assert said["mean"] == pytest.approx(equilibrated.mean, rel=1e-12)
        assert said["statistical_inefficiency"] == pytest.approx(
            equilibrated.inefficiency, rel=1e-9)
        if np.isnan(equilibrated.standard_error):
            assert said["standard_error"] is None
            assert said["refusal"] == why.refusal.code
        else:
            assert said["standard_error"] == pytest.approx(
                equilibrated.standard_error, rel=1e-9)
        assert mean_record(series)["discard"] == said["discard_frames"]
        _json_ready(record)

    def test_each_running_error_is_the_estimator_on_its_prefix(self) -> None:
        series = _ar1(6000, 0.9, seed=5)
        record = convergence_of(series)
        kept = series[record["equilibration"]["discard_frames"]:]
        running = record["running_mean"]
        assert 45 <= len(running["frames"]) <= 50
        for end, mean, error in zip(running["frames"], running["mean"],
                                    running["standard_error"]):
            prefix = kept[:end]
            assert mean == pytest.approx(prefix.mean(), rel=1e-9)
            g, _dof = _for_the_mean(prefix)
            effective = end / g
            resolved = statistical_inefficiency(prefix) < 2.0 or effective >= 25.0
            if effective > 1 and resolved:
                assert error == pytest.approx(
                    np.std(prefix, ddof=1) * np.sqrt(g / end), rel=1e-6)
            else:
                assert error is None
        # Early prefixes are too short to resolve g = 19 and are withheld;
        # the whole is not.
        assert running["standard_error"][0] is None
        assert running["standard_error"][-1] is not None


class TestAKnownCorrelation:

    def test_ar1_reads_its_own_inefficiency(self) -> None:
        phi = 0.9
        series = _ar1(100000, phi, seed=6)
        record = convergence_of(series, times=np.arange(series.size) * 2.0)
        g_true = (1 + phi) / (1 - phi)
        auto = record["autocorrelation"]
        assert auto["statistical_inefficiency"] == pytest.approx(g_true, rel=0.15)
        assert auto["tau_int_frames"] == pytest.approx(
            (auto["statistical_inefficiency"] - 1.0) / 2.0)
        assert auto["tau_int_time"] == pytest.approx(2.0 * auto["tau_int_frames"])
        assert record["frame_interval"] == 2.0
        # C(t) = phi^t, up to its first lag at or below zero.
        assert auto["correlation"][0] == 1.0
        assert auto["correlation"][1] == pytest.approx(phi, abs=0.02)
        assert auto["correlation"][10] == pytest.approx(phi ** 10, abs=0.05)
        assert auto["correlation"][-1] <= 0.0
        assert auto["zero_crossing_frames"] == len(auto["correlation"]) - 1
        assert auto["lag_time"][1] == 2.0

    def test_the_blocking_plateau_is_the_error_of_the_mean(self) -> None:
        phi = 0.9
        series = _ar1(100000, phi, seed=7)
        record = convergence_of(series)
        blocking = record["blocking"]
        kept = series.size - record["equilibration"]["discard_frames"]
        assert blocking["block_length"][:3] == [1, 2, 4]
        assert blocking["block_length"][-1] <= kept / 4
        # Block length one is the naive error, too small by sqrt(g).
        sd = 1.0 / np.sqrt(1.0 - phi ** 2)
        assert blocking["standard_error"][0] == pytest.approx(
            sd / np.sqrt(kept), rel=0.05)
        assert blocking["standard_error_uncertainty"][0] == pytest.approx(
            blocking["standard_error"][0] / np.sqrt(2 * (kept - 1)), rel=1e-6)
        truth = sd * np.sqrt(((1 + phi) / (1 - phi)) / kept)
        assert blocking["plateau_block_length"] >= 16
        assert blocking["plateau_standard_error"] == pytest.approx(truth, rel=0.25)
        assert blocking["plateau_reason"] is None

    def test_independent_values_have_no_correlation_to_speak_of(self) -> None:
        series = np.random.default_rng(8).normal(size=20000)
        record = convergence_of(series)
        assert record["equilibration"]["statistical_inefficiency"] < 1.2
        assert record["autocorrelation"]["tau_int_frames"] < 0.1
        assert record["autocorrelation"]["zero_crossing_frames"] <= 3
        assert record["blocking"]["plateau_block_length"] is not None
        assert record["blocking"]["plateau_standard_error"] == pytest.approx(
            record["equilibration"]["standard_error"], rel=0.2)


class TestATransientIsShownApart:

    def test_the_discarded_part_has_its_own_histogram(self) -> None:
        series = _ar1(8000, 0.8, seed=9) + 8.0 * np.exp(-np.arange(8000) / 300.0)
        times = np.arange(series.size) * 0.5
        record = convergence_of(series, times=times)
        discard = record["equilibration"]["discard_frames"]
        assert discard > 600
        assert record["equilibration"]["start_time"] == pytest.approx(0.5 * discard)
        histogram = record["histogram"]
        assert sum(histogram["discarded"]["counts"]) == discard
        assert sum(histogram["equilibrated"]["counts"]) == series.size - discard
        assert len(histogram["equilibrated"]["counts"]) <= 60
        assert len(histogram["equilibrated"]["edges"]) == (
            len(histogram["equilibrated"]["counts"]) + 1)
        # The running mean is of the equilibrated part, so it does not carry
        # the transient's high start.
        assert abs(record["running_mean"]["mean"][0]) < 2.0
        assert record["running_mean"]["time"][-1] == pytest.approx(times[-1])

    def test_nothing_discarded_has_no_histogram(self, monkeypatch) -> None:
        # Chodera's start is rarely exactly the first frame on noise, so the
        # start is fixed there to see what the record says of it.
        import fastmdxplora.statistics as statistics

        monkeypatch.setattr(statistics, "_latest_within_tolerance",
                            lambda counted: counted[0])
        record = convergence_of(_ar1(5000, 0.5, seed=10))
        assert record["equilibration"]["discard_frames"] == 0
        assert record["histogram"]["discarded"] is None
        assert sum(record["histogram"]["equilibrated"]["counts"]) == 5000


class TestWhatCannotBeRead:

    def test_a_constant_series_is_one_observation(self) -> None:
        record = convergence_of(np.full(500, 3.0))
        assert record["ok"] is True
        said = record["equilibration"]
        assert said["mean"] == 3.0
        assert said["standard_error"] is None
        assert said["refusal"] == "analysis.sampling.correlation_unresolved"
        assert record["autocorrelation"]["correlation"] == [1.0]
        assert record["blocking"]["plateau_block_length"] is None
        assert all(e is None for e in record["running_mean"]["standard_error"])
        assert record["histogram"]["equilibrated"]["counts"] == [
            500 - said["discard_frames"]]
        _json_ready(record)

    def test_a_short_series_gets_a_reason_not_numbers(self) -> None:
        record = convergence_of([1.0, 2.0, 3.0, 2.0, 1.0, 2.0, 3.0, 2.0, 1.0])
        assert record["ok"] is False
        assert "9 finite value(s)" in record["reason"]
        assert "equilibration" not in record
        assert "running_mean" not in record
        _json_ready(record)

    def test_values_that_are_not_finite_are_left_out_with_their_times(
            self) -> None:
        series = _ar1(3000, 0.7, seed=11)
        holed = series.copy()
        holed[[5, 900, 2000]] = [np.nan, np.inf, -np.inf]
        times = np.arange(series.size, dtype=float)
        record = convergence_of(holed, times=times)
        assert record["n_not_finite"] == 3
        assert record["n_values"] == 2997
        clean = np.delete(series, [5, 900, 2000])
        assert record["equilibration"]["mean"] == pytest.approx(
            summarise(clean)[0].mean)
        _json_ready(record)

    def test_times_must_match_the_values(self) -> None:
        from fastmdxplora.refusals import StudyError

        with pytest.raises(StudyError):
            convergence_of(np.arange(20.0), times=np.arange(10.0))


def test_a_million_values_take_under_a_second() -> None:
    """Processor time, so another process on the machine does not decide
    it. The correlations of every candidate start and every running-mean
    prefix come from one pass over the series each."""
    series = _ar1(1_000_000, 0.9, seed=12)
    convergence_of(series[:20000])
    began = time.process_time()
    record = convergence_of(series)
    spent = time.process_time() - began
    assert record["ok"]
    assert spent < 1.0, f"{spent:.2f} s"
