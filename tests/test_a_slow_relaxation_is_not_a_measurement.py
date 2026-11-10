"""A mean of a quantity still relaxing, slower than the run, is withheld.

The check a mean was withheld on asked whether its frames' first and last
thirds differed by two of the frames' spreads. That bar does not tighten as
a run grows while the mean's error does: on frames 500 inefficiencies long
under a decay of one spread over half the run, every mean was called
determined, a median five errors from where the decay was heading. The
trend is now also judged against its own error.
"""

from __future__ import annotations

import numpy as np
import pytest
from scipy.signal import lfilter

from fastmdxplora.refusals import refusal_of
from fastmdxplora.statistics import (
    DRIFT_IN_ERRORS, MEAN_RULES, mean_record, summarise, trend_in_errors)

PHI = 0.95
INEFFICIENCY = (1 + PHI) / (1 - PHI)


def _ar1(n, rng):
    noise = rng.normal(0.0, np.sqrt(1.0 - PHI ** 2), n)
    return lfilter([1.0], [1.0, -PHI], noise, zi=[PHI * rng.normal()])[0]


def _relaxing(n, rng, amplitude=1.0, over=0.5):
    t = np.arange(n)
    return amplitude * np.exp(-t / (over * n)) + _ar1(n, rng)


class TestTheTrendIsJudgedAgainstItsError:
    def test_a_slow_relaxation_is_often_withheld(self) -> None:
        rng = np.random.default_rng(2026)
        n = int(500 * INEFFICIENCY)
        withheld = 0
        for _ in range(40):
            _mean, why = summarise(_relaxing(n, rng))
            withheld += (why is not None and refusal_of(why).code
                         == "analysis.sampling.still_drifting")
        # About two in five; every one of them was called determined before.
        assert withheld >= 8

    def test_a_stationary_series_rarely_is(self) -> None:
        rng = np.random.default_rng(1918)
        n = int(100 * INEFFICIENCY)
        drifting = 0
        for _ in range(300):
            _mean, why = summarise(_ar1(n, rng))
            drifting += (why is not None and refusal_of(why).code
                         == "analysis.sampling.still_drifting")
        # Fewer than one in a hundred.
        assert drifting <= 6

    def test_the_refusal_says_the_trend_and_its_size(self) -> None:
        rng = np.random.default_rng(7)
        n = int(500 * INEFFICIENCY)
        for _ in range(40):
            _mean, why = summarise(_relaxing(n, rng, amplitude=1.5))
            if why is not None and refusal_of(why).code == \
                    "analysis.sampling.still_drifting":
                break
        else:
            pytest.fail("no relaxation was withheld")
        details = refusal_of(why).details
        assert abs(details["trend_in_errors"]) >= DRIFT_IN_ERRORS
        assert abs(details["drift_in_spread"]) < 2.0
        assert "times its own error" in str(why)

    def test_the_record_says_which_rules_judged_it(self) -> None:
        rng = np.random.default_rng(3)
        record = mean_record(_ar1(int(100 * INEFFICIENCY), rng))
        assert record["rules"] == MEAN_RULES >= 3


class TestTheTrendItself:
    def test_a_straight_line_reads_as_steep(self) -> None:
        rng = np.random.default_rng(0)
        x = np.linspace(0.0, 1.0, 4000) + rng.normal(scale=0.1, size=4000)
        assert trend_in_errors(x, 1.0) > 50.0

    def test_noise_reads_as_flat(self) -> None:
        rng = np.random.default_rng(0)
        values = [trend_in_errors(rng.normal(size=2000), 1.0)
                  for _ in range(400)]
        # A unit normal: about 5% beyond 1.96.
        assert np.mean(np.abs(values) > 1.96) < 0.09

    def test_a_short_or_constant_series_reads_as_zero(self) -> None:
        assert trend_in_errors([1.0, 2.0, 3.0], 1.0) == 0.0
        assert trend_in_errors(np.ones(100), 1.0) == 0.0


class TestTheReportGivesItOneVerdict:
    def test_a_mean_withheld_for_its_trend_has_not_equilibrated(self) -> None:
        # The report's own test of the thirds still passed it, and the report
        # called the mean equilibrated beside its refusal for drifting.
        from fastmdxplora.report.convergence import assess_series

        rng = np.random.default_rng(7)
        n = int(500 * INEFFICIENCY)
        for _ in range(40):
            series = _relaxing(n, rng, amplitude=1.5)
            _mean, why = summarise(series)
            if why is not None and refusal_of(why).code == \
                    "analysis.sampling.still_drifting":
                break
        else:
            pytest.fail("no relaxation was withheld")
        assessment = assess_series("rmsd", series)
        assert assessment.has_equilibrated is False
        assert not assessment.is_determined


class TestAJoinedRunSaysItToo:
    def test_a_segment_withheld_for_its_trend_is_said_to_drift(self) -> None:
        # Its NaN error read as an unresolved correlation, "longer segments"
        # its remedy, and the report called the run equilibrated.
        from fastmdxplora.report.convergence import assess_series
        from fastmdxplora.statistics import summarise_segments

        rng = np.random.default_rng(7)
        n = int(500 * INEFFICIENCY)
        for _ in range(40):
            moving = _relaxing(n, rng, amplitude=1.5)
            _mean, why = summarise(moving)
            if why is not None and refusal_of(why).code == \
                    "analysis.sampling.still_drifting":
                break
        else:
            pytest.fail("no relaxation was withheld")
        series = np.concatenate([_ar1(n, rng), moving, _ar1(n, rng)])
        joins = [n, 2 * n]
        pooled, why = summarise_segments(series, joins)
        assert pooled is None
        refusal = refusal_of(why)
        assert refusal.code == "analysis.sampling.still_drifting"
        # Its details say the cause: a trend against its error.
        assert refusal.details["needed"] == DRIFT_IN_ERRORS
        assert refusal.details["trend_in_errors"] >= DRIFT_IN_ERRORS
        assert assess_series("rmsd", series, joins=joins).has_equilibrated is False
