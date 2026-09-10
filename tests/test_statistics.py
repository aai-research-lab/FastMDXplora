"""Ten analyses averaged over the whole production run without asking whether
it had equilibrated, or how many independent samples the average rested
on.

Both follow from one quantity, so both are fixed by one piece of arithmetic:
the statistical inefficiency is the number of frames per independent sample,
which says where the relaxation ended and what the mean is worth.
"""

from __future__ import annotations

import numpy as np
import pytest

from fastmdxplora.statistics import (
    MINIMUM_EFFECTIVE_SAMPLES,
    correlation_is_resolved,
    detect_equilibration,
    statistical_inefficiency,
    summarise,
)


def _correlated(phi, n=200000, seed=0):
    """An AR(1) series, whose statistical inefficiency is known exactly.

    ``x[t] = phi x[t-1] + noise`` has autocorrelation ``phi**t``, so
    ``g = 1 + 2 sum phi**t = (1 + phi) / (1 - phi)``. That makes it the one
    case where the answer can be checked rather than eyeballed.
    """
    rng = np.random.RandomState(seed)
    values = np.zeros(n)
    for i in range(1, n):
        values[i] = phi * values[i - 1] + rng.normal()
    return values


class TestCountingIndependentSamples:
    """A trajectory written every picosecond, from a system decorrelating over
    a hundred, has a hundred times fewer independent samples than frames."""

    @pytest.mark.parametrize("phi", [0.0, 0.5, 0.8, 0.9])
    def test_it_matches_the_known_answer(self, phi) -> None:
        expected = (1.0 + phi) / (1.0 - phi)
        measured = statistical_inefficiency(_correlated(phi))
        assert measured == pytest.approx(expected, rel=0.05)

    def test_uncorrelated_frames_each_count_once(self) -> None:
        rng = np.random.RandomState(0)
        assert statistical_inefficiency(rng.normal(size=50000)) == \
            pytest.approx(1.0, abs=0.05)

    def test_a_constant_series_is_one_observation(self) -> None:
        """However many frames of it there are. This module first returned
        one, which says a constant has as many independent samples as frames;
        the report layer had it right, and there is now one implementation."""
        assert statistical_inefficiency(np.full(500, 2.5)) == 500.0

    def test_the_report_layer_and_this_one_are_the_same_function(self) -> None:
        """There were briefly two. They agreed on every correlated series and
        disagreed on a constant one, which is how a duplicated statistic
        announces itself -- usually later than this."""
        from fastmdxplora.report.convergence import autocorrelation_time

        rng = np.random.RandomState(0)
        for series in (rng.normal(size=2000), _correlated(0.8, 5000, seed=1),
                       np.full(300, 1.5)):
            assert autocorrelation_time(series) == \
                statistical_inefficiency(series)

    def test_too_few_frames_to_ask(self) -> None:
        assert statistical_inefficiency(np.array([1.0, 2.0])) == 1.0

    def test_it_never_reports_fewer_frames_than_samples(self) -> None:
        """An inefficiency below one would mean frames carrying more than
        their own information."""
        rng = np.random.RandomState(3)
        for series in (rng.normal(size=1000), _correlated(0.7, 5000, seed=4)):
            assert statistical_inefficiency(series) >= 1.0


class TestFindingWhereARunSettled:
    def test_a_relaxation_is_discarded(self) -> None:
        """Several relaxation times of it, which is what leaves the average
        describing the equilibrium rather than the approach to it."""
        rng = np.random.RandomState(0)
        tau = 200.0
        frames = np.arange(6000)
        series = 3.0 * np.exp(-frames / tau) + rng.normal(0, 0.15, frames.size)

        discard, _g, _n = detect_equilibration(series)

        assert 2 * tau < discard < 6 * tau

    def test_an_already_equilibrated_run_keeps_its_frames(self) -> None:
        rng = np.random.RandomState(0)
        discard, _g, effective = detect_equilibration(rng.normal(size=5000))

        assert discard < 500
        assert effective > 4000

    def test_the_answer_never_rests_on_the_last_third(self) -> None:
        """Whatever the series does, an average of its tail is not an average
        of the run."""
        rng = np.random.RandomState(0)
        awkward = np.concatenate([rng.normal(size=3000),
                                  rng.normal(5.0, 0.01, 2000)])
        discard, _g, _n = detect_equilibration(awkward)
        assert discard <= int(awkward.size * 2 / 3)

    def test_a_short_series_is_left_alone(self) -> None:
        assert detect_equilibration(np.arange(5.0))[0] == 0


class TestAnErrorBarThatMeansSomething:
    def test_correlation_widens_it(self) -> None:
        """Computed as though every frame were independent, the error on a
        strongly correlated series is understated several-fold -- which is how
        a difference between two systems becomes significant on paper without
        being real."""
        series = _correlated(0.95, 20000, seed=1)
        equilibrated, refusal = summarise(series)

        assert refusal is None
        naive = float(np.std(series, ddof=1) / np.sqrt(series.size))
        assert equilibrated.standard_error > 4 * naive

    def test_the_spread_is_not_the_error(self) -> None:
        """One is a property of the system, the other of how long it was
        watched, and reporting either for the other is a common way to make a
        result look tighter or looser than it is."""
        equilibrated, _ = summarise(_correlated(0.8, 20000, seed=2))
        assert equilibrated.standard_deviation > equilibrated.standard_error

    def test_a_run_with_too_few_independent_samples_is_refused(self) -> None:
        """Uncorrelated, so the count is measurable -- and there are still not
        enough of them."""
        rng = np.random.RandomState(0)
        equilibrated, refusal = summarise(rng.normal(size=8))

        assert refusal is not None
        assert "independent samples" in refusal
        assert equilibrated is not None, "the numbers are still returned to be read"

    def test_the_refusal_says_recording_more_often_will_not_help(self) -> None:
        """It is the commonest wrong response: the frames are correlated, so
        more of them are more of the same. Said where the count is measurable
        and small, since a run that cannot measure its own correlation gets
        the more specific refusal instead."""
        rng = np.random.RandomState(0)
        _settled, refusal = summarise(rng.normal(size=8))
        assert "recording them more often will not help" in refusal

    def test_length_is_not_the_test(self) -> None:
        """A long correlated run can hold fewer independent samples than a
        short uncorrelated one."""
        rng = np.random.RandomState(6)
        long_and_correlated = _correlated(0.999, 4000, seed=7)
        short_and_clean = rng.normal(size=200)

        _s1, refused = summarise(long_and_correlated)
        _s2, accepted = summarise(short_and_clean)

        assert refused is not None and accepted is None
        assert "not long against its own correlation time" in refused


class TestARunTooShortToMeasureItsOwnCorrelation:
    """The estimate of the correlation is itself unreliable when the run is
    short against the thing being estimated -- and it fails in the flattering
    direction, reporting more independent samples than there are.

    An AR(1) series with a true inefficiency of 2000 gave 361 from four
    thousand frames. The independent-sample count built from it said eleven,
    cleared a threshold of ten, and the truth was two.
    """

    def test_a_series_that_cannot_resolve_its_correlation_is_caught(
        self
    ) -> None:
        assert not correlation_is_resolved(_correlated(0.999, 4000, seed=7))

    def test_one_that_can_is_not(self) -> None:
        assert correlation_is_resolved(_correlated(0.99, 20000, seed=7))
        assert correlation_is_resolved(_correlated(0.9, 20000, seed=7))

    def test_even_a_long_run_can_fail_it(self) -> None:
        """Twenty thousand frames still cannot resolve a correlation time of
        a thousand: the estimate comes back 1129 against a true 1999, and the
        check says so rather than reporting the sample count as measured."""
        assert not correlation_is_resolved(_correlated(0.999, 20000, seed=7))

    def test_asking_where_the_correlation_decayed_does_not_work(self) -> None:
        """Worth pinning, because it is the obvious check. A sample
        autocorrelation sums to about -1/2 whatever the series, so it goes
        negative on its own: on the unresolvable series it crosses zero at a
        lag where the real correlation is still 0.7, and a check built on
        that crossing passes the run it is meant to catch.
        """
        series = _correlated(0.999, 4000, seed=7)
        fluctuation = series - series.mean()
        variance = float(np.mean(fluctuation ** 2))

        crossing = next(
            lag for lag in range(1, series.size - 1)
            if np.mean(fluctuation[:series.size - lag] * fluctuation[lag:])
            / variance <= 0.0)
        true_correlation_there = 0.999 ** crossing

        assert crossing < 500
        assert true_correlation_there > 0.5, (
            "the correlation is still strong where the estimator says it ended")

    def test_the_refusal_says_the_count_is_an_upper_bound(self) -> None:
        _settled, refusal = summarise(_correlated(0.999, 4000, seed=7))
        assert "upper bound" in refusal
        assert "longer run" in refusal
        assert "replicas" in refusal, (
            "a longer run was called the only remedy until ten replicas of one "
            "system, differing only by integrator seed, spread five to eight "
            "times wider than the error computed from within a single run. "
            "Replicas measure that spread directly; the wording now says so")

    def test_no_error_is_reported_where_the_correlation_is_unresolved(
            self) -> None:
        """An effective-sample count that is an upper bound makes an error
        computed from it a lower bound. Printed beside its caveat, such a
        number gets used and the caveat does not: three interaction
        occupancies the analysis had flagged reached a manuscript draft that
        way. It is withheld instead."""
        equilibrated, refusal = summarise(_correlated(0.999, 4000, seed=7))
        assert refusal is not None
        assert np.isnan(equilibrated.standard_error)
        assert not np.isnan(equilibrated.mean), "the mean is still reported"
        assert not np.isnan(equilibrated.standard_deviation), (
            "the spread of the series is a property of the system and does "
            "not depend on how independent the frames are")

    def test_a_short_series_with_a_correlation_cannot_resolve_it(self) -> None:
        assert not correlation_is_resolved(np.arange(30.0))

    def test_but_frames_near_independence_have_nothing_to_resolve(self) -> None:
        """A short uncorrelated series has no correlation time for a longer
        run to pin down, and putting one through the halving comparison only
        measures the noise in the comparison: discarding a single frame
        flipped the verdict on twenty-five."""
        rng = np.random.RandomState(0)
        assert correlation_is_resolved(rng.normal(size=25))
        assert correlation_is_resolved(rng.normal(size=24))

    def test_nothing_to_average_is_said_plainly(self) -> None:
        equilibrated, refusal = summarise(np.array([1.0]))
        assert equilibrated is None
        assert "nothing to average" in refusal

    def test_frames_that_are_not_numbers_are_dropped_first(self) -> None:
        rng = np.random.RandomState(0)
        series = rng.normal(size=5000)
        series[[10, 20, 30]] = np.nan

        equilibrated, refusal = summarise(series)
        assert refusal is None
        assert np.isfinite(equilibrated.mean) and np.isfinite(equilibrated.standard_error)

    def test_the_record_carries_every_number_behind_the_mean(self) -> None:
        """So a reader can check the claim rather than take it."""
        equilibrated, _ = summarise(_correlated(0.5, 20000, seed=8))
        assert set(equilibrated.as_record()) == {
            "discard", "statistical_inefficiency", "effective_samples",
            "mean", "standard_error", "standard_deviation"}

    def test_the_threshold_is_a_judgement_a_study_can_make(self) -> None:
        rng = np.random.RandomState(0)
        marginal = rng.normal(size=40)

        _s, strict = summarise(marginal, minimum_effective_samples=1000)
        _s, lenient = summarise(marginal, minimum_effective_samples=5)

        assert strict is not None and lenient is None
        assert MINIMUM_EFFECTIVE_SAMPLES == 10.0


class TestEveryPerFrameAnalysisSaysWhatItsMeanIsWorth:
    """The summary is recorded once, in the base class, for any analysis
    declaring that it produces one value per frame.

    Declared rather than inferred from the array's length: a per-atom result
    on a trajectory that happened to have as many frames as atoms would
    otherwise be summarised as a time series, and the numbers would look
    right.
    """

    @staticmethod
    def _trajectory(n_frames=400, n_atoms=12, seed=0):
        import mdtraj as md

        rng = np.random.RandomState(seed)
        topology = md.Topology()
        chain = topology.add_chain()
        residue = topology.add_residue("ALA", chain)
        for i in range(n_atoms):
            topology.add_atom(f"C{i}", md.element.carbon, residue)
        # A drift settling into a fluctuation, which is what a production run
        # looks like when equilibration was not quite finished.
        base = rng.normal(0, 0.05, (n_frames, n_atoms, 3))
        relax = np.exp(-np.arange(n_frames) / 40.0)[:, None, None]
        return md.Trajectory(base + 0.5 * relax, topology)

    def test_a_per_frame_analysis_records_it(self, tmp_path) -> None:
        from fastmdxplora.analysis import get_analysis_class

        traj = self._trajectory()
        analysis = get_analysis_class("rg")(output_dir=tmp_path / "rg")
        analysis.run(traj)

        assert "mean" in analysis.findings
        record = analysis.findings["mean"]
        assert "standard_error" in record
        assert "effective_samples" in record
        assert "discard" in record

    def test_the_error_is_not_the_naive_one(self, tmp_path) -> None:
        """Which is the whole point: it is built from independent samples,
        not from the frame count."""
        from fastmdxplora.analysis import get_analysis_class

        traj = self._trajectory()
        analysis = get_analysis_class("rg")(output_dir=tmp_path / "rg")
        analysis.run(traj)

        record = analysis.findings["mean"]
        assert record["effective_samples"] <= traj.n_frames
        assert record["standard_error"] >= (
            record["standard_deviation"] / np.sqrt(traj.n_frames))

    def test_a_per_atom_analysis_records_nothing(self, tmp_path) -> None:
        """RMSF is one value per atom. A mean over atoms is a different
        quantity from a mean over time, and summarising it as though the two
        were the same is the error the declaration exists to prevent."""
        from fastmdxplora.analysis import get_analysis_class

        traj = self._trajectory()
        analysis = get_analysis_class("rmsf")(output_dir=tmp_path / "rmsf")
        analysis.run(traj)

        assert "mean" not in analysis.findings

    def test_the_frame_number_is_not_averaged(self, tmp_path) -> None:
        """Two analyses return a frame of (frame, value). Averaging the first
        column gives the middle of the run, to three decimal places, and it
        would look like a measurement."""
        import inspect

        from fastmdxplora.analysis.base import Analysis

        source = inspect.getsource(Analysis._record_what_the_mean_is_worth)
        assert 'str(c).lower() != "frame"' in source

    def test_the_declarations_match_what_the_analyses_return(
        self, tmp_path
    ) -> None:
        """The check that keeps the two from drifting: anything returning one
        value per frame must say so, and anything saying so must return one.
        """
        from fastmdxplora.analysis import available_analyses, get_analysis_class

        traj = self._trajectory()
        for name in available_analyses():
            cls = get_analysis_class(name)
            if getattr(cls, "requires_ligand", False):
                continue          # no ligand in this trajectory to measure
            try:
                analysis = cls(output_dir=tmp_path / name)
                result = analysis.compute(traj)
            except Exception:     # noqa: BLE001 - not what is under test here
                continue

            if hasattr(result, "columns"):
                columns = [c for c in result.columns
                           if str(c).lower() != "frame"]
                per_frame = (len(columns) == 1
                             and len(result) == traj.n_frames)
            else:
                array = np.asarray(result)
                per_frame = (array.ndim == 1 and array.size == traj.n_frames)

            if per_frame:
                assert cls.time_series, (
                    f"{name} returns one value per frame and does not say so, "
                    "so its mean carries no error and no sample count")


class TestOneRunGetsOneVerdict:
    """The report layer and the analyses both ask whether a series can measure
    its own correlation time, and they asked it differently.

    The report's rule was a tenth of the run, which is the usual working limit
    and lets the flattering case through: a series with a true correlation of
    2000 measured 361 over 4000 frames, and 361 is under a tenth of 4000. So a
    run could be measurable in the report and unresolved in the findings, on
    the same numbers.
    """

    def test_the_case_the_old_rule_missed(self) -> None:
        from fastmdxplora.report.convergence import assess_series

        series = _correlated(0.999, 4000, seed=7)
        old_rule = statistical_inefficiency(series) <= max(2.0, series.size / 10)

        assert old_rule, "the tenth-of-the-run rule passes this series"
        assert not assess_series("cv", series).correlation_is_measurable

    def test_a_well_sampled_series_still_passes(self) -> None:
        from fastmdxplora.report.convergence import assess_series

        assert assess_series("cv", _correlated(0.9, 20000, seed=7)) \
            .correlation_is_measurable

    def test_the_report_and_the_analyses_agree(self) -> None:
        from fastmdxplora.report.convergence import assess_series

        rng = np.random.RandomState(0)
        for series in (rng.normal(size=5000),
                       _correlated(0.9, 20000, seed=1),
                       _correlated(0.999, 4000, seed=2),
                       _correlated(0.999, 20000, seed=3)):
            assert (assess_series("cv", series).correlation_is_measurable
                    == correlation_is_resolved(series))
