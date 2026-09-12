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


def _ten_segments(shift: float = 0.05, n: int = 400, seed: int = 3):
    rng = np.random.default_rng(seed)
    pieces = [rng.normal(loc=10.0 + shift * i, scale=0.3, size=n)
              for i in range(10)]
    return np.concatenate(pieces), [n * i for i in range(1, 10)]


class TestReadingAJoinedRunAsOneLosesIt(unittest.TestCase):

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

    def test_and_lands_much_closer_to_the_truth(self):
        # True mean of the construction: 10 + 0.05 * 4.5.
        joined, joins = _ten_segments()
        truth = 10.0 + 0.05 * 4.5

        naive, _ = summarise(joined)
        pooled, _ = summarise_segments(joined, joins)

        naive_error = abs(naive.mean - truth)
        pooled_error = abs(pooled.mean - truth)
        self.assertLess(pooled_error, naive_error / 10)
        # And the naive one is confidently wrong, which is the part that
        # matters: it is not merely off, it is off by many times its own
        # stated uncertainty.
        self.assertGreater(naive_error / naive.standard_error, 5.0)

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
