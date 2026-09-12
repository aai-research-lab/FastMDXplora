"""A joined trajectory is not a single sample path, and reading it as one
loses most of the run.

`provenance["joins"]` has been recorded since segmentation landed and
nothing read it. This is what reads it, and the reason is not tidiness.

Chodera's equilibration detection picks the discard that maximises
effective samples. A join is a small step change -- under a barostat the
move size is re-adapting -- and a step change is exactly what that method
is built to find. On a ten-segment run it will often discard everything
before one of the later joins, throw away most of the study, and report
the remainder as though that had been the experiment. Silently: the
number that comes out has a standard error on it and looks like a
measurement.

The demonstration below is ten segments drawn from the same distribution
with a small shift at each join. The naive reading discards six segments
and lands eighteen standard errors from the truth. The join-aware reading
uses all ten and lands on it.
"""

from __future__ import annotations

import unittest

import numpy as np

from fastmdxplora.refusals import refusal_of
from fastmdxplora.statistics import summarise, summarise_segments


#: Offsets at each join: unordered, which is what a re-adapting barostat
#: gives. A monotone ramp would be a different defect -- the run still
#: drifting -- and `summarise_segments` refuses that one, so using it here
#: would test the drift guard rather than the join handling.
_OFFSETS = (0.0, 0.09, -0.06, 0.11, -0.10, 0.04, -0.08, 0.07, -0.03, 0.06)


def _ten_segments(n: int = 400, seed: int = 3):
    rng = np.random.default_rng(seed)
    pieces = [rng.normal(loc=10.0 + offset, scale=0.3, size=n)
              for offset in _OFFSETS]
    return np.concatenate(pieces), [n * i for i in range(1, 10)]


class TestReadingAJoinedRunAsOneLosesIt(unittest.TestCase):

    def test_a_run_that_actually_drifts_is_refused_rather_than_pooled(self):
        # The ordered version of the same construction. It is a different
        # defect and gets a different answer: not a worse mean, no mean.
        rng = np.random.default_rng(3)
        ramped = np.concatenate(
            [rng.normal(loc=10.0 + 0.05 * i, scale=0.3, size=400)
             for i in range(10)])
        pooled, why = summarise_segments(ramped, [400 * i for i in range(1, 10)])
        self.assertIsNone(pooled)
        self.assertEqual(refusal_of(why).code, "analysis.sampling.drifting")

    def test_the_naive_reading_throws_most_of_the_run_away(self):
        # Not a hypothetical. This is what happens today to anybody who
        # runs a segmented study and summarises the joined series.
        joined, _ = _ten_segments()
        equilibrated, why = summarise(joined)
        self.assertIsNone(why)
        self.assertGreater(
            equilibrated.discard, len(joined) // 2,
            "expected the detector to read the joins as a transient")

    def test_the_join_aware_reading_keeps_it(self):
        joined, joins = _ten_segments()
        pooled, why = summarise_segments(joined, joins)
        self.assertIsNone(why)
        self.assertEqual(pooled.contributing, 10)

    def test_and_lands_closer_to_the_truth(self):
        joined, joins = _ten_segments()
        truth = 10.0 + sum(_OFFSETS) / len(_OFFSETS)

        naive, _ = summarise(joined)
        pooled, _ = summarise_segments(joined, joins)

        self.assertLess(abs(pooled.mean - truth), abs(naive.mean - truth))

    def test_the_cost_is_precision_rather_than_bias_when_joins_are_unordered(self):
        # Worth separating, because the two failures want different
        # responses. With unordered offsets the naive mean is roughly
        # right and its error bar is several times too wide, so a real
        # difference looks unsupported. With ordered ones it would be
        # biased too -- and that case is refused as drift rather than
        # reported, so it cannot reach here.
        joined, joins = _ten_segments()
        naive, _ = summarise(joined)
        pooled, _ = summarise_segments(joined, joins)
        self.assertGreater(naive.standard_error, pooled.standard_error * 1.5)

    def test_pooling_recovers_the_independent_samples(self):
        joined, joins = _ten_segments()
        naive, _ = summarise(joined)
        pooled, _ = summarise_segments(joined, joins)
        self.assertGreater(pooled.effective_samples, naive.effective_samples)


class TestPoolingIsArithmeticAndNotOptimism(unittest.TestCase):

    def test_no_joins_falls_through_to_the_ordinary_reading(self):
        # A caller should not have to branch on whether a run was
        # segmented. An empty join list is the unsegmented case.
        series = np.random.default_rng(1).normal(size=2000)
        pooled, why = summarise_segments(series, [])
        self.assertIsNone(why)
        self.assertEqual(pooled.contributing, 1)
        equilibrated, _ = summarise(series)
        self.assertAlmostEqual(pooled.mean, equilibrated.mean, places=12)

    def test_segments_that_say_nothing_say_nothing_together(self):
        # Pooling does not rescue a run that was too short. This is the
        # failure mode a pooling function invites, and the assertion is
        # the guard against it.
        tiny = np.concatenate([np.array([1.0, 2.0]) for _ in range(10)])
        pooled, why = summarise_segments(tiny, [2 * i for i in range(1, 10)])
        self.assertIsNone(pooled)
        self.assertEqual(refusal_of(why).code,
                         "analysis.sampling.too_few_independent")

    def test_a_short_joined_run_is_still_withheld(self):
        rng = np.random.default_rng(7)
        short = np.concatenate([rng.normal(size=3) for _ in range(3)])
        pooled, why = summarise_segments(short, [3, 6])
        self.assertIsNone(pooled)
        self.assertTrue(refusal_of(why).matches("analysis.sampling"))

    def test_the_mean_is_weighted_by_what_each_segment_supports(self):
        # Minimum-variance combination. A segment with four times the
        # independent samples should pull four times as hard, and an
        # unweighted mean of segment means would not do that.
        rng = np.random.default_rng(11)
        long_piece = rng.normal(loc=0.0, scale=0.2, size=4000)
        short_piece = rng.normal(loc=5.0, scale=0.2, size=200)
        joined = np.concatenate([long_piece, short_piece])
        pooled, why = summarise_segments(joined, [4000])
        self.assertIsNone(why)
        unweighted = (0.0 + 5.0) / 2
        self.assertLess(abs(pooled.mean - 0.0), abs(pooled.mean - unweighted))

    def test_a_withheld_segment_is_named_rather_than_counted(self):
        # A run where four of ten segments said nothing is a different
        # object from one where all ten contributed, and a bare count does
        # not say which.
        rng = np.random.default_rng(5)
        pieces = [rng.normal(size=600) if i != 4 else np.array([1.0, 1.0])
                  for i in range(6)]
        joined = np.concatenate(pieces)
        edges, at = [], 0
        for piece in pieces[:-1]:
            at += piece.size
            edges.append(at)
        pooled, why = summarise_segments(joined, edges)
        self.assertIsNone(why)
        self.assertEqual([index for index, _ in pooled.withheld], [4])

    def test_the_record_says_it_was_pooled(self):
        # A reader comparing this against a run that went through in one
        # piece should know they are not the same kind of number.
        joined, joins = _ten_segments()
        pooled, _ = summarise_segments(joined, joins)
        self.assertTrue(pooled.as_record()["pooled_across_joins"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


def _joined(locations, *, n=400, scale=0.3, seed=3):
    rng = np.random.default_rng(seed)
    series = np.concatenate(
        [rng.normal(loc=where, scale=scale, size=n) for where in locations])
    return series, [n * i for i in range(1, len(locations))]


class TestDriftAndScatterAreNotTheSameThing(unittest.TestCase):
    """The gap pooling opens, and the guard that closes it.

    Pooling combines estimates of one quantity. If the segments are not
    measuring one quantity -- a system still moving across the whole run --
    it returns the mean of a moving target with a confident error bar,
    which is the worst of the three outcomes because it looks most like a
    measurement.

    Scatter and drift look identical in a list of numbers. Segment means
    that disagree in no order say the per-segment errors are too small.
    Segment means that climb say the system had not settled. The first is
    a qualification; the second is a refusal.
    """

    def test_a_settled_run_pools_without_comment(self):
        # The case that must not be refused, or the guard is useless.
        series, joins = _joined([10.0] * 8)
        pooled, why = summarise_segments(series, joins)
        self.assertIsNone(why)
        self.assertEqual(pooled.qualification, "")
        self.assertLess(pooled.heterogeneity, 2.0)

    def test_a_drifting_run_refuses(self):
        series, joins = _joined([10.0 + 0.25 * i for i in range(8)])
        pooled, why = summarise_segments(series, joins)
        self.assertIsNone(pooled)
        refusal = refusal_of(why)
        self.assertEqual(refusal.code, "analysis.sampling.drifting")
        self.assertLess(refusal.details["drift_p"], 0.05)

    def test_the_refusal_says_how_far_it_moved(self):
        # A caller deciding whether to run longer needs the size of the
        # movement, not only that there was one.
        series, joins = _joined([10.0 + 0.25 * i for i in range(8)])
        _, why = summarise_segments(series, joins)
        self.assertAlmostEqual(refusal_of(why).details["span"], 1.75, places=1)

    def test_scattered_segments_qualify_rather_than_refuse(self):
        # Disagreement in no order is not movement. It says the errors are
        # too small, which is worth saying and is not grounds to withhold.
        series, joins = _joined(
            [10.0, 10.4, 9.6, 10.35, 9.65, 10.1, 9.9, 10.05])
        pooled, why = summarise_segments(series, joins)
        self.assertIsNone(why)
        self.assertIn("lower bound", pooled.qualification)
        self.assertGreater(pooled.drift_p, 0.05)

    def test_drift_needs_both_an_ordering_and_a_disagreement(self):
        # An ordering of segments that agree is a trend of nothing. Without
        # the second condition, a settled run with eight segments would
        # refuse one time in twenty on the p-value alone.
        series, joins = _joined([10.0] * 8)
        pooled, why = summarise_segments(series, joins)
        self.assertIsNone(why)

    def test_two_segments_are_never_called_drifting(self):
        # Two points always lie on a line. Calling that a trend would
        # refuse every two-segment run.
        from fastmdxplora.statistics import drift_across_segments

        self.assertEqual(
            drift_across_segments(np.array([1.0, 9.0]),
                                  np.array([100.0, 100.0])), 1.0)

    def test_the_permutation_p_value_cannot_be_zero(self):
        # No permutation test can support one. The observed ordering is
        # itself one of the orderings.
        from fastmdxplora.statistics import drift_across_segments

        steep = np.arange(10, dtype=float) * 100.0
        p = drift_across_segments(steep, np.full(10, 1e6), permutations=99)
        self.assertGreater(p, 0.0)
        self.assertLessEqual(p, 0.011)

    def test_the_record_carries_both_numbers(self):
        series, joins = _joined([10.0] * 6)
        pooled, _ = summarise_segments(series, joins)
        record = pooled.as_record()
        self.assertIn("heterogeneity", record)
        self.assertIn("drift_p", record)


def _mean_of(assessment_record: dict, name: str) -> float:
    """The mean for one observable, whatever key assess_run files it under."""
    for value in assessment_record.values():
        if isinstance(value, dict) and name in value:
            entry = value[name]
            if isinstance(entry, dict) and "mean" in entry:
                return float(entry["mean"])
            if hasattr(entry, "mean"):
                return float(entry.mean)
    raise AssertionError(f"no mean for {name} in {list(assessment_record)}")


class TestTheReportReadsTheJoinsWithoutBeingTold(unittest.TestCase):
    """The gap that made `summarise_segments` optional in practice.

    It existed and a caller had to know to use it, which meant the default
    path -- an analysis reading a joined trajectory -- still lost most of
    the run. A guardrail somebody has to remember is not a guardrail.
    """

    def test_the_convergence_assessment_takes_joins(self):
        from fastmdxplora.report.convergence import assess_series

        joined, joins = _ten_segments()
        naive = assess_series("rmsd", joined)
        aware = assess_series("rmsd", joined, joins=joins)

        truth = 10.0 + float(np.mean(_OFFSETS))
        self.assertLess(abs(aware.mean - truth), abs(naive.mean - truth))

    def test_a_run_that_went_through_is_unaffected(self):
        # Most runs are not segmented, and the join-aware path must not
        # change what they report.
        from fastmdxplora.report.convergence import assess_series

        series = np.random.default_rng(2).normal(size=2000)
        self.assertEqual(assess_series("rmsd", series).mean,
                         assess_series("rmsd", series, joins=[]).mean)

    def test_assess_run_passes_them_through(self):
        from fastmdxplora.report.convergence import assess_run

        joined, joins = _ten_segments()
        aware = assess_run({"rmsd": joined}, joins=joins)
        naive = assess_run({"rmsd": joined})
        truth = 10.0 + float(np.mean(_OFFSETS))
        # assess_run's shape is its own business; what is asserted is that
        # the joins reached the assessment underneath it.
        self.assertLess(
            abs(_mean_of(aware, "rmsd") - truth),
            abs(_mean_of(naive, "rmsd") - truth))

    def test_the_joins_can_be_found_from_the_trajectory_path(self):
        # So a caller holding a path does not have to have been told.
        import json
        import tempfile
        from pathlib import Path

        from fastmdxplora.analysis.joining import joins_beside

        root = Path(tempfile.mkdtemp())
        trajectory = root / "joined.dcd"
        trajectory.write_bytes(b"")
        (root / "joined.dcd.join.json").write_text(
            json.dumps({"joins": [400, 800]}), encoding="utf-8")
        self.assertEqual(joins_beside(trajectory), [400, 800])

    def test_a_trajectory_that_was_never_joined_reports_no_joins(self):
        # Empty rather than raising, so the result goes straight into
        # summarise_segments without a caller branching on it.
        import tempfile
        from pathlib import Path

        from fastmdxplora.analysis.joining import joins_beside

        lone = Path(tempfile.mkdtemp()) / "production.dcd"
        lone.write_bytes(b"")
        self.assertEqual(joins_beside(lone), [])

    def test_an_unreadable_record_is_treated_as_absent(self):
        # Guessing at where the joins were would put an invented number
        # into the decision about how to read the run.
        import tempfile
        from pathlib import Path

        from fastmdxplora.analysis.joining import joins_beside

        root = Path(tempfile.mkdtemp())
        trajectory = root / "joined.dcd"
        trajectory.write_bytes(b"")
        (root / "joined.dcd.join.json").write_text("{not json",
                                                   encoding="utf-8")
        self.assertEqual(joins_beside(trajectory), [])
