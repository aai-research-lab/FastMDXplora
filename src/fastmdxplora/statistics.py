"""Where a run equilibrated, and how many independent samples it holds.

At the top level rather than under ``analysis``, because it is not an
analysis: nothing registers it, it produces no figure, and both the analyses
and the report ask it the same question. It sat in the analysis package
briefly and the report imported it from there, which reads as though measuring
a correlation were something analyses do and reports borrow.

Ten analyses averaged over the whole production run without asking either
question. A mean root-mean-square deviation of 2.3 Å is not a measurement
until two things are known about it: whether the system had stopped changing
by the time the averaging started, and how many independent observations the
average rests on.

**The first.** A structure that has just been minimised, heated and pressure-
equilibrated is still relaxing when production begins. Averaging from the
first frame averages the approach to equilibrium together with equilibrium
itself, and the answer depends on how long the run was rather than on the
system.

**The second.** Frames are not independent. A trajectory written every
picosecond from a system whose fluctuations decorrelate over a hundred
picoseconds has a hundred times fewer independent samples than it has frames,
and a standard error computed as if each frame counted is wrong by a factor
of ten. That is how a difference between two systems becomes significant on
paper without being real.

Both follow from one quantity. The statistical inefficiency ``g`` is the
number of frames per independent sample, so a series of ``n`` frames carries
``n / g`` of them. Chodera's method chooses where to start averaging by
maximising that count: discard too little and the equilibration is still in
the average, discard too much and there is nothing left to average.

    Chodera, J. D. A simple method for automated equilibration detection in
    molecular simulations. J. Chem. Theory Comput. 2016, 12, 1799-1805.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

__all__ = [
    "Equilibrated",
    "Withholding",
    "Pooled",
    "drift_across_segments",
    "heterogeneity_ratio",
    "Shortfall",
    "summarise_segments",
    "sampling_shortfall",
    # The old name, kept importable so nothing outside has to move at once.
    "Settled",
    "MEAN_RULES",
    "judged_by_earlier_rules",
    "too_few_by_these_rules",
    "withheld_reason",
    "withheld_code",
    "drift_unseen_said",
    "DRIFTING_CODES",
    "statistical_inefficiency",
    "correlation_is_resolved",
    "detect_equilibration",
    "summarise",
    "drift_in_spread",
    "trend_in_errors",
    "mean_record",
    "shared_start",
    "with_its_error",
    "independent_samples_said",
    "four_figures",
    "convergence_of",
]

#: Below this many independent samples, a mean and its error describe the
#: run's accidents rather than the system. Ten is not generous -- it is the
#: point below which an error bar stops meaning anything at all -- and it is
#: a judgement, so a study can set its own.
MINIMUM_EFFECTIVE_SAMPLES = 10.0

#: A correlated series must hold this many of its own inefficiencies before
#: it measures its correlation time well enough to put an error on its mean.
#: Calibrated (``preregistration/stopping-calibration.md``): shorter than about
#: 25 times its inefficiency, the estimate of it reads low, and the error with
#: it. This replaced a check that halved the series and compared the two
#: estimates, which on series 50 to 250 times their inefficiency withheld 19%
#: to 41% at random and passed the ones whose estimate had read low.
RESOLVED_SAMPLES = 25.0

#: Frames this close to independent (a statistical inefficiency, as first
#: estimated and before :func:`_for_the_mean`'s correction, under this) have
#: no correlation time for a longer run to pin down, so they are not held to
#: :data:`RESOLVED_SAMPLES`: their error may be given at fewer independent
#: samples than that, never fewer than :data:`MINIMUM_EFFECTIVE_SAMPLES`.
UNCORRELATED_INEFFICIENCY = 2.0


def independent_samples_said(count: float, *, about: bool = False) -> str:
    """A count of independent samples in words, the same on every page: a
    count under one is "fewer than one independent sample", never rounded
    to 0; any other is a whole number, and one is a sample, not samples. A
    count under :data:`MINIMUM_EFFECTIVE_SAMPLES` is never rounded up to
    it: 9.6 is said as 9, beside a rule that withholds an error under 10.
    ``about`` hedges it, as an estimate is."""
    count = float(count)
    if not np.isfinite(count):
        return "a number of independent samples that could not be counted"
    if count < 1:
        return "fewer than one independent sample"
    whole = independent_samples_whole(count)
    return (("about " if about else "") + f"{whole} independent "
            + ("sample" if whole == 1 else "samples"))


def independent_samples_whole(count: float) -> int:
    """A count of independent samples as the whole number
    :func:`independent_samples_said` says, for a table's column: 0 under
    one, 9 for 9.5 (never rounded up to :data:`MINIMUM_EFFECTIVE_SAMPLES`)."""
    count = float(count)
    if count < 1:
        return 0
    whole = int(round(count))
    if count < MINIMUM_EFFECTIVE_SAMPLES <= whole:
        whole = int(np.floor(count))
    return whole


#: Equilibration is detected as the latest start that keeps at least this
#: share of the most independent samples any start keeps. The start that
#: keeps the most leaves the most of a relaxation in the mean: on a transient
#: shared by three replicas it left a bias about the size of their error.
EQUILIBRATION_TOLERANCE = 0.1

#: A series whose averaged frames still move by this many times their own
#: spread between their first and last thirds is still drifting, and its
#: mean is not one of a state. Against the frames' spread, not the mean's
#: error: on a stationary series long enough to resolve its correlation the
#: two thirds' means differ by about half a spread at most, so this is
#: crossed by a trend, almost never by noise.
DRIFT_IN_SPREAD = 2.0

#: The fewest frames averaged that can be split into thirds with two in
#: each, the least that says anything about a trend.
DRIFT_FRAMES = 6

#: The rules a mean is judged by, written into every mean recorded as
#: determined or not (``rules``), so what reads a record made by an
#: earlier version can say its verdict may not be this version's. 2: a
#: mean whose averaged frames still move one way is withheld
#: (``analysis.sampling.still_drifting``). 3: so is one whose frames trend
#: by :data:`DRIFT_IN_ERRORS` of the trend's own error. A record without
#: the key was written by an earlier version, most of which did not
#: withhold such a mean, so it may call one determined.
#: 4 also checks recorded joins for a resolved trend between segments.
#: 5 calls three or four segments drifting only where the trend also stands
#: against the segment means' own scatter about the line, each mean placed
#: at its centre frame in time (and that scatter alone where a line does
#: not fit the segments' errors).
MEAN_RULES = 5

#: A series whose averaged frames trend by this many times the trend's own
#: error is still drifting, however small the trend is against the frames'
#: spread. The spread's test (:data:`DRIFT_IN_SPREAD`) does not tighten as a
#: run grows while the mean's error does, so a relaxation slower than the
#: run passed it: on AR(1) frames 500 inefficiencies long under a decay of
#: one spread over half the run, every mean was determined and its median
#: five errors from the value the decay was heading to. Judged against the
#: trend's error it is withheld in about two of five such runs, and a
#: stationary series in about one in a hundred (measured 0.1% to 1.7%,
#: fewer for a short series, whose trend's error is read wide; P(|z| >=
#: 2.5) is 1.2%); a run
#: extended until it is determined is asked again at each look (counted in
#: ``preregistration/stopping-calibration.md``, the third remedy). It
#: lowers how often a wrong mean is given; it does not make the ones still
#: given right, and a relaxation much slower than the run it does not see.
DRIFT_IN_ERRORS = 2.5

#: The error of a mean after a discard is taken from the whole run, scaled to
#: the frames kept, unless discarding gained at least this many times the
#: independent samples. Choosing the start where the most remain chooses
#: where the inefficiency reads lowest, and on stationary series the error
#: from what was kept covered the truth in 60% of cases where 68% is honest.
#: A real relaxation gains far more than twice; noise rarely does.
WHOLE_RUN_UNLESS_GAIN = 2.0


@dataclass(frozen=True)
class Equilibrated:
    """What a series supports, once the equilibration is out of it.

    Named for the method it implements. `detect_equilibration` below is
    Chodera's automated equilibration detection, and the field calls the
    discarded transient the equilibration period -- so this is the
    equilibrated part, not a "settled" one. The two words were doing one
    job, and only one of them is a term a reader of the literature already
    knows.
    """

    #: Frames discarded before averaging.
    discard: int
    #: Frames per independent sample. One means every frame counts.
    inefficiency: float
    #: Independent samples in what is left.
    effective_samples: float
    mean: float
    #: The uncertainty on the mean, from the effective samples rather than
    #: from the frame count. Using the frame count is what makes a difference
    #: look significant when it is not.
    standard_error: float
    #: The spread of the series itself, which is a property of the system
    #: rather than of how long it was watched.
    standard_deviation: float
    #: How well the error itself is known: about ``N / (2M + 1)`` for ``N``
    #: frames and ``M`` lags of autocorrelation summed. An error resting on
    #: eight degrees of freedom holds the truth within itself 65% of the
    #: time, not 68%; the stopping rule judges with that.
    degrees_of_freedom: float = float("nan")

    def as_record(self) -> dict[str, Any]:
        return {
            "discard": self.discard,
            "statistical_inefficiency": self.inefficiency,
            "effective_samples": self.effective_samples,
            "mean": self.mean,
            "standard_error": self.standard_error,
            "standard_deviation": self.standard_deviation,
            "degrees_of_freedom": self.degrees_of_freedom,
            "rules": MEAN_RULES,
        }


#: The name this carried before it was matched to the method it implements.
#: Kept as an alias rather than deleted, because a rename that breaks an
#: import teaches nothing and costs a user an afternoon.
Settled = Equilibrated


def statistical_inefficiency(series: np.ndarray) -> float:
    """Frames per independent sample: ``g = 1 + 2 sum (1 - t/n) C(t)``.

    ``C`` is the normalised fluctuation autocorrelation. Past the lag where
    it has decayed the estimates are noise, and summing them adds variance
    rather than information, so the sum stops there. Where it stops is taken
    from adjacent pairs, ``C(2k) + C(2k+1)``, not from single lags: see
    :func:`_inefficiency_and_reach`.

    A constant series has no fluctuations to correlate, so ``g`` is one: every
    frame agrees, and there is nothing for a correlation time to describe.

    Values that are not finite are left out first. One NaN made every
    correlation NaN, no pair of lags compared as non-positive, and
    ``max(1.0, nan)`` returned 1.0: a series with a true ``g`` of 39 read as
    independent frames, and the bootstrap block built on it was 2 frames
    rather than 83.
    """
    values = _finite(series)
    n = values.size
    if n < 3:
        return 1.0

    fluctuation = values - values.mean()
    variance = float(np.mean(fluctuation ** 2))
    if variance <= 0.0:
        # A series that never changes is perfectly correlated, and no number
        # of frames of it is more than one observation. Returning one instead
        # would say a constant has as many independent samples as it has
        # frames. (The reasoning is the report layer's, which had this right
        # before this module existed.)
        return float(n)

    return _inefficiency_and_reach(fluctuation, variance, n)[0]


def _finite(series: np.ndarray) -> np.ndarray:
    """The series as floats, with values that are not finite left out."""
    values = np.asarray(series, dtype=float).ravel()
    return values[np.isfinite(values)]


def _correlation(fluctuation: np.ndarray, variance: float) -> np.ndarray:
    """``C(t) = sum_i f[i] f[i+t] / (n var)`` at every lag, by Fourier
    transform: the numbers a sum over each lag gives, in ``n log n`` rather
    than ``n`` times the lags."""
    n = fluctuation.size
    size = 1 << (2 * n - 1).bit_length()
    spectrum = np.fft.rfft(fluctuation, size)
    correlation = np.fft.irfft(spectrum * np.conj(spectrum), size)[:n] / (n * variance)
    correlation[0] = 1.0
    return correlation


def _geyer(fluctuation: np.ndarray, variance: float, n: int) -> tuple[float, bool, int]:
    """The initial positive sequence: the inefficiency, whether a pair went
    non-positive before the run ran out, and how many pairs were summed."""
    g, decayed, kept, _decided = _geyer_from(_correlation(fluctuation, variance), n)
    return g, decayed, kept


def _geyer_from(correlation: np.ndarray, n: int) -> tuple[float, bool, int, bool]:
    """Geyer's initial positive sequence from the correlation at lags
    ``0 .. len(correlation) - 1`` of a series of ``n`` frames.

    Returns the inefficiency, whether a pair went non-positive, how many
    pairs were summed, and whether the lags given were enough to decide: a
    correlation cut short of the series, with every pair in it positive,
    gives a lower bound and ``False``. Given every lag, it is always
    decided, and the numbers are :func:`_geyer`'s.
    """
    total = (n - 1) // 2
    available = min(total, len(correlation) // 2)
    pairs = np.arange(available)
    summed = correlation[2 * pairs] + correlation[2 * pairs + 1]
    stops = np.flatnonzero(summed <= 0.0)
    kept = int(stops[0]) if stops.size else int(pairs.size)
    decided = bool(stops.size) or available == total
    return (max(1.0, -1.0 + 2.0 * float(summed[:kept].sum())), bool(stops.size),
            kept, decided)


def _inefficiency_and_reach(
    fluctuation: np.ndarray, variance: float, n: int
) -> tuple[float, bool]:
    """The inefficiency, and whether the correlation decayed within the run.

    Summed over adjacent pairs of lags, ``P(k) = C(2k) + C(2k+1)``, up to the
    first pair that is not positive: Geyer's initial positive sequence
    (Statistical Science 7, 473, 1992). His monotone variant, which holds
    each pair no larger than the one before, was tried and left out: on a
    run joined from segments with small offsets between them it read the
    weak, long correlation lower still than the single-lag rule did, and an
    inefficiency read low is a standard error read small. The sum ran over single lags and stopped at the first ``C``
    that was not positive, and a series whose frames alternate stops there
    at once. A stiff restraint sampled slower than the mode it restrains
    does exactly that, so a window read ``g = 1`` whatever moved underneath:
    on a series whose true inefficiency is 120, a slow mode under a fast
    one that alternates frame to frame, the single-lag rule returned 1.0.
    That is a correlation time of nothing, which the halving test then
    passes as having nothing to resolve. A pair of lags cancels the
    alternation and keeps what is slow. Where the correlation decays without
    alternating, the two rules stop at the same place and agree.

    The second value says whether a pair went non-positive before the series
    ran out. When it did not, the correlation was still positive at the
    longest lag the run can measure, and everything past that is missing from
    the sum: the inefficiency comes back too small and the independent-sample
    count too large. On a series of 4000 frames with a true inefficiency of
    2000, the estimate was 361 -- and 11 apparent independent samples cleared
    a threshold of 10. So it is not a detail. It is the difference between a
    measurement and a lower bound, and a run in that state is too short to
    say how short it is.
    """
    g, decayed, _pairs = _geyer(fluctuation, variance, n)
    return g, decayed


def _for_the_mean(series: np.ndarray) -> tuple[float, float]:
    """The inefficiency an error of the mean should use, and the degrees of
    freedom of that error.

    An autocorrelation taken about the sample mean rather than the true one
    is low by about ``g / N`` at every lag, since the sample mean has followed
    the series' own slow fluctuation. Summed over the ``M`` lags kept, the
    inefficiency comes out low by a factor of about
    ``(1 - (2M + 1) / N) / (1 - g / N)``, which is undone here. On series 25
    times their inefficiency the estimate's median went from 0.90 of the
    truth to 0.99.

    The degrees of freedom are ``N / (2M + 1)``: the variance of a sum of
    ``2M + 1`` correlation estimates, each about as noisy as ``1 / N``.
    Values that are not finite are left out first, as in
    :func:`statistical_inefficiency`.
    """
    values = _finite(series)
    n = values.size
    if n < 3:
        return 1.0, float(max(n - 1, 1))
    fluctuation = values - values.mean()
    variance = float(np.mean(fluctuation ** 2))
    if variance <= 0.0:
        return float(n), 1.0
    g, _decayed, pairs = _geyer(fluctuation, variance, n)
    return _corrected_for_the_mean(g, pairs, n)


def _corrected_for_the_mean(g: float, pairs: int, n: int) -> tuple[float, float]:
    """:func:`_for_the_mean`'s correction and degrees of freedom, from the
    inefficiency and the pairs of lags summed for it."""
    if pairs == 0:
        return g, float(n - 1)
    window = 4 * pairs - 1
    room = 1.0 - window / n
    corrected = g / (room + g / n) if room > 0.0 else float(n)
    return min(corrected, float(n)), n / window


def correlation_is_resolved(series: np.ndarray) -> bool:
    """Whether the series is long enough to resolve its own correlation time:
    at least :data:`RESOLVED_SAMPLES` times its inefficiency, or so little
    correlated that its inefficiency is under :data:`UNCORRELATED_INEFFICIENCY`.

    A run shorter than a few times its correlation time reads that time low,
    and its own error with it: on an AR(1) series with a true inefficiency of
    2000, four thousand frames gave 361, and the independent-sample count
    built from it said eleven when the truth was two. The count is checked
    against 25 because the estimate reads low until about then, from a third
    of the truth at five times to nine tenths at 25 (calibrated in
    ``preregistration/stopping-calibration.md``), so a run that reaches 25 by
    its own estimate is not far short of 25 in truth.

    The check this replaced halved the series and asked whether the estimate
    moved by more than 15%. The estimate's own noise is larger than that
    until a run is far longer than most: on stationary series 50 to 250 times
    their inefficiency it withheld 19% to 41% at random, and the means it
    gave were those whose whole-run estimate had read low against its half,
    the ones whose errors were too small.

    The obvious check does not work either, and it is worth saying why. A
    sample autocorrelation sums to roughly -1/2 whatever the series, so it
    goes negative on its own: on that series it crossed zero at lag 334 while
    the real correlation there was still 0.7. Asking where the correlation
    decayed answers a question about the estimator rather than the run.
    """
    values = _finite(series)

    # Nothing to resolve. Frames this close to independent have no
    # correlation time for a longer run to pin down.
    if statistical_inefficiency(values) < UNCORRELATED_INEFFICIENCY:
        return True
    g, _dof = _for_the_mean(values)
    return values.size / g >= RESOLVED_SAMPLES


def detect_equilibration(
    series: np.ndarray, *, steps: int = 40
) -> tuple[int, float, float]:
    """Where to start averaging, and what is left once you do.

    Returns the number of frames to discard, the statistical inefficiency of
    what remains, and the effective sample count. The discard point is the one
    maximising that count, which is the trade Chodera's method makes explicit:
    keeping the equilibration costs independence, and discarding it costs
    frames.

    Candidate points are strided rather than exhaustive, because the count
    varies smoothly with where the average starts and evaluating every frame
    is quadratic for an answer no better.

    Of the starts, the latest that keeps within
    :data:`EQUILIBRATION_TOLERANCE` of the most independent samples is taken,
    not the one that keeps the most. Near its maximum the count is flat, and
    the start that maximises it leaves the most of a relaxation in the
    average: on three replicas sharing one relaxation, their mean was biased
    by about its own error. Starting a little later costs at most a tenth of
    the samples. Values that are not finite are left out first.
    """
    values = _finite(series)
    n = values.size
    if n < 10:
        return 0, 1.0, float(n)

    counted = []
    for start in _equilibration_candidates(n, steps):
        remaining = values[start:]
        if remaining.size < 3:
            continue
        g = statistical_inefficiency(remaining)
        counted.append((int(start), g, float(remaining.size / g)))
    return _latest_within_tolerance(counted)


def _equilibration_candidates(n: int, steps: int = 40) -> np.ndarray:
    """The starts :func:`detect_equilibration` weighs. Never past two thirds:
    an answer resting on the last third of a run is not an answer about the
    run."""
    return np.unique(np.linspace(0, int(n * 2 / 3), num=min(steps, n), dtype=int))


def _latest_within_tolerance(
    counted: "list[tuple[int, float, float]]",
) -> tuple[int, float, float]:
    """Of ``(start, g, independent samples)`` rows, the latest keeping within
    :data:`EQUILIBRATION_TOLERANCE` of the most."""
    if not counted:
        return 0, 1.0, 0.0
    most = max(effective for _start, _g, effective in counted)
    return [row for row in counted
            if row[2] >= (1.0 - EQUILIBRATION_TOLERANCE) * most][-1]


class Withholding(str):
    """The reason a mean was withheld, carrying its code.

    A ``str`` subclass so that nothing which already reads this changes:
    it prints, formats, compares and tests truthy exactly as the plain
    string it replaces did, and ``summarise``'s signature is unaltered.

    What it adds is ``.refusal`` -- the same fact in the form a program
    can branch on. A caller that can extend a run wants to distinguish
    "the correlation time is not resolved, run longer" from "there are
    three frames here" without matching on prose, and the three
    withholdings below are different conditions with different remedies.

    Read it with :func:`fastmdxplora.refusals.refusal_of`, which takes
    anything carrying a ``refusal`` attribute.
    """

    __slots__ = ("refusal",)

    def __new__(cls, message: str, *, code: str, **details: Any):
        from fastmdxplora.refusals import Refusal, known

        obj = super().__new__(cls, message)
        obj.refusal = Refusal(
            code=code if known(code) else "unclassified",
            message=message,
            details={k: v for k, v in details.items() if v is not None},
        )
        return obj


def drift_in_spread(values: Any) -> float:
    """How far a series moved between its first and last thirds, against
    how much it wobbles within them: the last third's mean less the first's,
    over the root-mean-square of their spreads. A raw slope says nothing
    without a scale, and this means the same for any observable. Zero for a
    series too short to split into thirds with two frames in each."""
    series = np.asarray(values, dtype=np.float64)
    series = series[np.isfinite(series)]
    if series.size < DRIFT_FRAMES:
        return 0.0
    third = series.size // 3
    first, last = series[:third], series[-third:]
    noise = np.sqrt((first.var() + last.var()) / 2)
    if noise <= 0:
        return 0.0 if np.isclose(first.mean(), last.mean()) else float("inf")
    return float((last.mean() - first.mean()) / noise)


def _trend(values: np.ndarray, inefficiency: float) -> "tuple[float, float]":
    """The least-squares slope of ``values`` against frame number, and its
    error with the residuals taken as correlated over ``inefficiency``
    frames: ``sqrt(g var(r) / sum (t - t_bar)^2)``. In closed form, from
    the sums the slope needs, so it costs one pass over the series. The
    error is NaN for fewer than three values, and zero where none vary."""
    n = values.size
    if n < 3:
        return 0.0, float("nan")
    t = np.arange(n, dtype=float) - 0.5 * (n - 1)
    spread = n * (n * n - 1.0) / 12.0
    centred = values - values.mean()
    # Sums rather than dot products: a dot product runs on every BLAS thread,
    # and the convergence view asks this for fifty prefixes of a series.
    slope = float(np.sum(t * centred)) / spread
    # Residual sum of squares: sum c^2 - slope^2 sum t^2.
    left = max(float(np.sum(centred * centred)) - slope * slope * spread, 0.0)
    variance = left / (n - 2)
    return slope, float(np.sqrt(max(float(inefficiency), 1.0) * variance / spread))


def trend_in_errors(values: Any, inefficiency: float) -> float:
    """How steadily a series moves, against the error of that movement: the
    least-squares slope over frame number divided by its error, the frames
    taken as correlated over ``inefficiency`` frames (the series' own, as
    the mean's error takes it before any discard is weighed; the trend's
    residuals decorrelate no slower than the series, so the error is if
    anything wide). Zero for a series too short, or one that does not vary.

    Unlike :func:`drift_in_spread` it tightens as a run grows, as the
    mean's error does: a run long enough to give a mean an error of a
    hundredth of its spread can show a trend of a tenth of a spread, and
    that trend is the mean's bias. What it cannot see is curvature: a
    relaxation much slower than the run, or a slow oscillation, shows as a
    trend only in part, and a mean left after it can still be biased.
    """
    series = np.asarray(values, dtype=np.float64)
    series = series[np.isfinite(series)]
    if series.size < DRIFT_FRAMES:
        return 0.0
    slope, error = _trend(series, inefficiency)
    if not np.isfinite(error) or error <= 0.0:
        return 0.0
    return float(slope / error)


def summarise(
    series: np.ndarray,
    *,
    minimum_effective_samples: float = MINIMUM_EFFECTIVE_SAMPLES,
    start_at_least: int = 0,
) -> tuple[Equilibrated | None, str | None]:
    """A mean with an honest error on it, or a reason there is not one.

    ``start_at_least`` is a start found elsewhere that this series should not
    average before: replicas from one structure share their relaxation, and
    the start found on their average (:func:`shared_start`) sees it where one
    run's noise hides it. Never past two thirds of the series.

    The refusal is about independence, not length: a long run of highly
    correlated frames can hold fewer independent samples than a short run of
    uncorrelated ones, and it is the second number that decides what the mean
    is worth.
    """
    values = np.asarray(series, dtype=float)
    values = values[np.isfinite(values)]
    if values.size < 3:
        return None, Withholding(
            f"{values.size} usable frame(s): there is nothing to average, and "
            "nothing to say about how it varies.",
            code="analysis.sampling.too_few_frames",
            found=int(values.size), needed=3,
        )

    discard, raw, _most = detect_equilibration(values)
    later = min(int(start_at_least), int(values.size * 2 / 3))
    if later > discard:
        discard, raw = later, statistical_inefficiency(values[later:])
    return _summary(values, discard, raw, _for_the_mean(values[discard:]),
                    lambda: _for_the_mean(values), minimum_effective_samples)


def _summary(
    values: np.ndarray,
    discard: int,
    raw: float,
    for_kept: tuple[float, float],
    for_whole: "Any",
    minimum_effective_samples: float,
) -> tuple[Equilibrated, "Withholding | None"]:
    """The rest of :func:`summarise`, once the start is chosen: ``raw`` is
    the inefficiency of what is kept, ``for_kept`` its :func:`_for_the_mean`
    and ``for_whole`` a callable giving the whole series'."""
    kept = values[discard:]
    spread = float(np.std(kept, ddof=1))
    g, dof = for_kept
    error = spread * float(np.sqrt(g / kept.size))
    if discard:
        # The start was chosen where the most independent samples remain,
        # which is where the inefficiency of what remains reads lowest; the
        # mean after it is sound, the error from it is not. Unless the
        # discard removed a relaxation that dominated the run, the error is
        # the whole run's, scaled to the frames kept.
        g_whole, dof_whole = for_whole()
        if kept.size / g < WHOLE_RUN_UNLESS_GAIN * values.size / g_whole:
            whole = float(np.std(values, ddof=1)) * float(np.sqrt(g_whole / kept.size))
            if whole > error:
                error, dof = whole, dof_whole
    effective = (spread / error) ** 2 if error > 0.0 else kept.size / g
    resolved = raw < UNCORRELATED_INEFFICIENCY or effective >= RESOLVED_SAMPLES

    equilibrated = Equilibrated(
        discard=discard,
        inefficiency=float(kept.size / effective) if effective > 0 else float(kept.size),
        effective_samples=float(effective),
        mean=float(np.mean(kept)),
        # Withheld where the correlation is unresolved, rather than printed
        # beside a warning that it cannot be trusted. An effective-sample
        # count that is an upper bound makes an error computed from it a
        # lower bound, and a number wrong in a knowable direction is worse
        # than no number: the caveat is read once and the figure is used
        # thereafter. Measured on ten replicas of one system differing only
        # by integrator seed, 20 ns each, errors computed from one run came
        # out about ten times smaller than the spread of the ten means.
        standard_error=error if (effective > 1 and resolved) else float("nan"),
        standard_deviation=spread,
        degrees_of_freedom=float(dof),
    )

    if not resolved:
        return equilibrated, Withholding(
            f"This run is not long against its own correlation time: "
            f"{kept.size} frames hold {effective:.1f} independent samples by "
            f"their own estimate, and a correlated run shorter than "
            f"{RESOLVED_SAMPLES:g} of them reads its correlation time low (only frames "
            "whose statistical inefficiency, before the correction for the sample "
            f"mean, is under {UNCORRELATED_INEFFICIENCY:g} are exempt). "
            "That count is an upper bound, so an error computed from it would "
            "be a lower bound -- and none is reported here rather than one "
            "that is wrong in a knowable direction. On ten replicas of one "
            "system differing only by seed, errors computed from one run were "
            "about ten times smaller than the spread of the ten means. The "
            "remedy is a longer run, or better, replicas.",
            code="analysis.sampling.correlation_unresolved",
            frames=int(kept.size), independent=float(effective),
            statistical_inefficiency=float(equilibrated.inefficiency),
            needed=float(RESOLVED_SAMPLES),
        )

    drift = drift_in_spread(kept)
    slope, slope_error = _trend(kept, g) if kept.size >= DRIFT_FRAMES else (0.0, float("nan"))
    trend = (slope / slope_error
             if np.isfinite(slope_error) and slope_error > 0.0 else 0.0)
    if kept.size >= DRIFT_FRAMES and abs(trend) >= DRIFT_IN_ERRORS \
            and abs(drift) < DRIFT_IN_SPREAD:
        import dataclasses

        change = slope * (kept.size - 1)
        return dataclasses.replace(equilibrated, standard_error=float("nan")), Withholding(
            f"Still drifting after its equilibration: over the {kept.size} frames "
            f"averaged the series moves steadily, by {change:+.4g} from first to "
            f"last, a trend {abs(trend):.1f} times its own error with the frames' "
            "correlation allowed for. Small against how much the frames wobble, it "
            "is not small against the mean's error, and a relaxation slower than "
            "the run puts the mean where the run happened to stop rather than "
            "where the system is going; the remedy is a longer run. (A relaxation "
            "far slower than the run may show no trend at all: runs from "
            "different structures are what test that.)",
            code="analysis.sampling.still_drifting",
            frames=int(kept.size), drift_in_spread=round(float(drift), 2),
            trend_in_errors=round(float(trend), 2), needed=DRIFT_IN_ERRORS,
        )
    if kept.size >= DRIFT_FRAMES and abs(drift) >= DRIFT_IN_SPREAD:
        # Its own error cannot say so: a series moving steadily through the
        # frames averaged holds the start of a trend, not a state, and the
        # report said "still moving" beside a mean every page called
        # determined.
        import dataclasses

        return dataclasses.replace(equilibrated, standard_error=float("nan")), Withholding(
            f"Still drifting after its equilibration: over the {kept.size} frames "
            f"averaged, the last third's mean differs from the first third's by "
            f"{abs(drift):.1f} times their spread. A mean of a quantity still moving "
            "describes where the run happened to stop, not a state; the remedy is a "
            "longer run.",
            code="analysis.sampling.still_drifting",
            frames=int(kept.size), drift_in_spread=round(float(drift), 2),
            needed=DRIFT_IN_SPREAD,
        )

    if effective < minimum_effective_samples:
        return equilibrated, Withholding(
            f"{effective:.1f} independent samples in {kept.size} frames "
            f"(one every {equilibrated.inefficiency:.0f}). Below "
            f"{minimum_effective_samples:g} a mean "
            "and its error describe how this particular run happened to go "
            "rather than the system it was run on. The frames are correlated, "
            "so recording them more often will not help -- the run has to be "
            "longer.",
            code="analysis.sampling.too_few_independent",
            independent=float(effective), frames=int(kept.size),
            statistical_inefficiency=float(equilibrated.inefficiency),
            needed=float(minimum_effective_samples),
        )
    return equilibrated, None


@dataclass(frozen=True)
class Shortfall:
    """How much more of a run a claim would need.

    A refusal for want of sampling is only half an answer. The other half
    is the number that turns "not enough" into a decision: how much
    longer, and is that an afternoon or a fortnight.

    Both numbers here rest on the same ``g`` the refusal did. Frames are
    worth ``1/g`` of an independent sample each, so reaching ``target``
    of them takes ``target * g`` frames past equilibration -- and the
    frames already in hand count, which is why this is a shortfall rather
    than a total.

    ``more_ns`` is ``None`` where the caller did not say how far apart the
    frames are. It is not guessed: a frame interval is a fact about how
    the run was written out, and inventing one would put a plausible
    duration in front of somebody who would then plan around it.
    """

    #: Independent samples asked for.
    target: float
    #: Independent samples in hand.
    have: float
    #: Frames per independent sample, from the series itself.
    inefficiency: float
    #: Further frames needed. Zero where the target is already met.
    more_frames: int
    #: The same, in nanoseconds, where a frame interval was given.
    more_ns: float | None = None
    #: Whether the frames in hand resolve their own correlation time. Where
    #: they do not, ``inefficiency`` is read low, the figure is a lower
    #: bound, and it is at least as many frames again as there are: the
    #: least that could show whether the correlation time holds.
    resolved: bool = True

    @property
    def met(self) -> bool:
        return self.more_frames == 0

    def as_record(self) -> dict[str, Any]:
        record = {
            "target_independent": self.target,
            "independent": self.have,
            "statistical_inefficiency": self.inefficiency,
            "more_frames": self.more_frames,
        }
        if self.more_ns is not None:
            record["more_ns"] = self.more_ns
        if not self.resolved:
            record["lower_bound"] = True
        return record

    def __str__(self) -> str:
        if self.met:
            return (f"{self.have:.1f} independent samples, which meets the "
                    f"{self.target:g} asked for.")
        duration = ("" if self.more_ns is None
                    else f", about {self.more_ns:.3g} ns more")
        if not self.resolved:
            return (
                f"The run is too short to resolve its own correlation time, so "
                f"its {self.have:.1f} independent samples are an upper bound. "
                f"At least {self.more_frames} further frames{duration} (as "
                f"long again, or to {self.target:g} samples at one every "
                f"{self.inefficiency:.0f} frames if that is longer), then "
                "estimate again."
            )
        return (
            f"{self.have:.1f} independent samples of the {self.target:g} "
            f"needed. At one every {self.inefficiency:.0f} frames, that is "
            f"{self.more_frames} further frames{duration}."
        )


def sampling_shortfall(
    series: np.ndarray,
    *,
    target_independent: float = MINIMUM_EFFECTIVE_SAMPLES,
    frame_interval_ns: float | None = None,
) -> Shortfall:
    """What it would take to support a claim this run does not yet support.

    The companion to :func:`summarise`'s refusal. Where that says a mean is
    not worth reporting, this says how much further the run has to go
    before it is.

    Measured from the series rather than assumed, so it costs nothing to
    ask and it answers for this system rather than for a typical one. A
    system whose fluctuations decorrelate in 5 frames and one that takes
    500 need very different amounts of further sampling for the same
    claim, and the difference is not visible in the trajectory length.

    Note what this does *not* do. It reads the correlation from the
    frames in hand, so where those frames are too few to resolve it --
    the condition ``correlation_is_resolved`` names -- ``g`` is an
    underestimate and the shortfall is a lower bound. It is a planning
    figure, not a guarantee, and the honest use of it is to run at least
    that much and measure again.
    """
    values = np.asarray(series, dtype=float)
    values = values[np.isfinite(values)]
    if values.size < 3:
        # Nothing to read a correlation time from. Reporting g = 1 here
        # would say the frames are independent, which is the most
        # optimistic possible answer at the moment there is least reason
        # for optimism.
        return Shortfall(
            target=float(target_independent), have=0.0, inefficiency=float("nan"),
            more_frames=0, more_ns=None,
        )

    discard, g, effective = detect_equilibration(values)
    kept = int(values.size - discard)
    wanted_frames = int(np.ceil(float(target_independent) * g))
    more = max(0, wanted_frames - kept)
    # A run that cannot resolve its correlation time reads it low, so ten
    # samples at that rate can already be "in hand" while the mean is
    # withheld for exactly this reason: a shortfall of nothing, for a run
    # the analysis said must be longer. The least that could settle it is as
    # long again, which is what the halving test compares.
    resolved = correlation_is_resolved(values[discard:])
    if not resolved:
        more = max(more, kept)
    return Shortfall(
        target=float(target_independent),
        have=float(effective),
        inefficiency=float(g),
        more_frames=more,
        more_ns=(None if frame_interval_ns is None
                 else float(more * frame_interval_ns)),
        resolved=resolved,
    )


#: The withholdings a longer run of the same system answers, for which the
#: record therefore says how much more. Three usable frames is not one of
#: them: that is a run with almost nothing written.
WANT_A_LONGER_RUN = frozenset({
    "analysis.sampling.correlation_unresolved",
    "analysis.sampling.too_few_independent",
})


def shared_start(series: "list[np.ndarray]") -> int:
    """Where replicas started from one structure have all equilibrated: the
    start detected on their frame-by-frame average.

    Replicas over a seed share the relaxation from the structure they began
    in, and each run's own detection sees it through that run's noise. On
    the average the relaxation is the same and the noise is smaller by the
    square root of the replicas, so the start found there is later where
    the relaxation is real and no later where it is not. On three replicas
    sharing a relaxation, each run's own start left their mean biased by
    about half its error.
    """
    runs = [np.asarray(s, dtype=float) for s in series if np.asarray(s).size]
    if len(runs) < 2:
        return 0
    n = min(r.size for r in runs)
    stacked = np.vstack([r[:n] for r in runs])
    average = np.nanmean(stacked, axis=0) if np.isnan(stacked).any() else stacked.mean(axis=0)
    average = average[np.isfinite(average)]
    return int(detect_equilibration(average)[0]) if average.size else 0


def _shortfall_as_summarised(values: np.ndarray, equilibrated: "Equilibrated", target: float,
                             frame_interval_ns: float | None, *, unresolved: bool) -> Shortfall:
    """The shortfall from the count the mean was withheld on.

    The shortfall reads the frames' correlation once more, without the
    corrections the withholding made (the sample mean's bias, the whole
    run's error widened): a mean withheld for 18 independent samples read
    the count met and said no figure for how much longer, and one withheld
    at 1 sample in 5 frames asked for 5 more frames where 45 were wanted.
    Read from the summary's own count, an unresolved correlation needs at
    least as long again, and only that figure is a floor."""
    finite = int(np.isfinite(np.asarray(values, dtype=float)).sum())
    kept = max(finite - int(equilibrated.discard), 1)
    have = float(equilibrated.effective_samples)
    g = kept / have if have > 0 else float(kept)
    more = max(int(np.ceil(float(target) * g)) - kept, kept if unresolved else 1)
    return Shortfall(
        target=float(target), have=have, inefficiency=float(g), more_frames=more,
        more_ns=None if frame_interval_ns is None else float(more * frame_interval_ns),
        resolved=not unresolved,
    )


def mean_record(series: np.ndarray, *, frame_interval_ns: float | None = None,
                start_at_least: int = 0, joins: list[int] | tuple[int, ...] = ()) -> dict[str, Any]:
    """What an analysis records of a per-frame series: its mean and error
    after equilibration, or why there is none and how much longer would give
    one. One function for every analysis and for whatever reads the records
    as the analyses would write them (the stopping rule's calibration among
    them), so the two cannot differ. ``start_at_least`` is a start shared
    with replicas (:func:`shared_start`); the record says where it came from.
    Known ``joins`` additionally guard against drift between segments. This
    guard does not change the existing single-series estimate into a pooled
    estimate: it withholds its error when the joined run is still moving."""
    values = np.asarray(series, dtype=float)
    equilibrated, reason = summarise(values, start_at_least=start_at_least)
    boundaries = sorted({int(j) for j in joins if 0 < int(j) < values.size})
    unseen: dict[str, Any] | None = None
    if boundaries:
        tested: dict[str, Any] = {}
        _, joined_reason = summarise_segments(values, boundaries, tested_into=tested)
        if _code_of(joined_reason) == "analysis.sampling.drifting":
            reason = joined_reason
        else:
            # Whether or not the joined summary was pooled: the single-series
            # mean can stand determined where it was withheld for a segment
            # whose correlation is not resolved, and the drift test ran on
            # the rest, or on too few to run.
            unseen = _drift_unseen(tested)
    record: dict[str, Any] = {}
    if equilibrated is not None:
        record.update(equilibrated.as_record())
    if reason is not None:
        record["not_a_measurement"] = reason
        if _code_of(reason) and _code_of(reason) != "unclassified":
            # Saved beside the text, so a reader branches on the verdict
            # rather than on its words: the report matched "still drifting"
            # and called a mean drifting between segments equilibrated.
            record["withheld_code"] = _code_of(reason)
        if _code_of(reason) == "analysis.sampling.drifting":
            record["standard_error"] = float("nan")
            record["drift_test"] = reason.refusal.details["drift_test"]
        # And what would make it one: "the remedy is a longer run" left the
        # length to guess, and the correlation the series already shows
        # says how much longer.
        code = getattr(getattr(reason, "refusal", None), "code", "")
        finite = values[np.isfinite(values)]
        # The same in every frame (a strand fraction of 0 throughout): no
        # spread, so no correlation time either, and a longer run answers
        # nothing; it asked 816 frames, the frame count as an inefficiency.
        constant = finite.size > 0 and float(np.ptp(finite)) == 0.0
        if code in WANT_A_LONGER_RUN and not constant:
            # An unresolved correlation is answered at the count that
            # resolves it, not at the fewest a mean may rest on.
            target = (RESOLVED_SAMPLES if code == "analysis.sampling.correlation_unresolved"
                      else MINIMUM_EFFECTIVE_SAMPLES)
            shortfall = sampling_shortfall(values, target_independent=target,
                                           frame_interval_ns=frame_interval_ns)
            if equilibrated is not None:
                # The larger of the two: the count the mean was withheld
                # on, and the frames' own reading where it asks for more.
                summarised = _shortfall_as_summarised(
                    values, equilibrated, target, frame_interval_ns,
                    unresolved=code == "analysis.sampling.correlation_unresolved")
                if shortfall.met or summarised.more_frames >= shortfall.more_frames:
                    shortfall = summarised
            if not shortfall.met:
                record["shortfall"] = shortfall.as_record()
    record["n_frames"] = int(values.size)
    if boundaries:
        record["segment_joins"] = boundaries
    if unseen is not None and reason is None and equilibrated is not None:
        record["drift_unseen"] = unseen
    if start_at_least:
        record["start_shared_with_replicas"] = int(start_at_least)
    return record


#: The smallest steady drift, in pooled errors from the first segment's mean
#: to the last's, that the three- and four-segment rule calls in at least
#: 80% of runs of equal segments whose errors hold (counted by
#: ``tests/_short_segment_drift_check.py``, 10,000 sets each; segments of
#: unequal length need more: ``docs/short-segment-drift.md``).
DRIFT_UNSEEN_IN_POOLED_ERRORS = {3: 41, 4: 16}

#: Segments whose errors differ by at most this fraction of the largest are
#: said to be equal, for the drift that could go unseen.
EQUAL_SEGMENTS_WITHIN = 0.1


def _drift_unseen(tested: "dict[str, Any]") -> "dict[str, Any] | None":
    """What a joined run's drift test could miss, from what it tested
    (:func:`summarise_segments`'s ``tested_into``): on three or four
    segments tested and not called drifting, how large a steady drift could
    go unseen; where fewer than three of three or more could be tested,
    that drift between them was not tested; None otherwise (drift called,
    five or more tested, or a run of fewer than three segments).

    The figure is counted where the segment errors hold, for errors equal
    to a tenth (``equal_segments``; a larger one is said for errors as
    unequal as these). Where the means scatter about the line more than
    those errors allow, the rule is decided by that scatter, and the figure
    is scaled by its square root (:func:`_line_scatter`), as counted in
    ``docs/short-segment-drift.md``."""
    if not tested or tested.get("called"):
        return None
    count, total = int(tested.get("tested") or 0), int(tested.get("segments") or 0)
    said = {"segments": total, "tested": count, "left_out": str(tested.get("left_out") or "")}
    if count < 3:
        return {**said, "not_tested": True} if total >= 3 else None
    if count not in DRIFT_UNSEEN_IN_POOLED_ERRORS:
        return None
    error = float(tested.get("pooled_error", float("nan")))
    if not (math.isfinite(error) and error > 0):
        return None
    scatter = float(tested.get("line_scatter", float("nan")))
    scale = math.sqrt(scatter) if math.isfinite(scatter) and scatter > 1.0 else 1.0
    counted = DRIFT_UNSEEN_IN_POOLED_ERRORS[count]
    # Equal by their errors, which X is counted on, not by their lengths:
    # equal pieces with an imprecise middle one need half as much again.
    errors = [float(e) for e in tested.get("errors") or []]
    return {**said, "counted_in_pooled_errors": counted,
            "scatter_about_line": scatter if math.isfinite(scatter) else None,
            "in_pooled_errors": float(counted * scale),
            "pooled_error": error, "change": float(counted * scale * error),
            "equal_segments": bool(errors) and
            (max(errors) - min(errors)) <= EQUAL_SEGMENTS_WITHIN * max(errors)}


def _two_figures(value: float) -> str:
    """A positive value to two significant figures, grouped, never as an
    exponent: an "about" needs no more."""
    rounded = float(f"{value:.2g}")
    places = max(0, 1 - math.floor(math.log10(abs(rounded))))
    return f"{rounded:,.{places}f}"


def _number(value: Any) -> "float | None":
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(float(value)) else None


def drift_unseen_said(record: Any, unit: "str | None" = None) -> str:
    """Beside a mean of a joined run, determined, whose drift test between
    segments did not call drift (``drift_unseen`` on its record): on three
    or four segments tested, how large a steady drift could go unseen with
    that few, in the quantity's own units first segment to last, and the
    error it is reckoned from; where fewer than three could be tested, that
    drift between them was not tested, and why. A sentence, without its
    full stop; an empty string for any other record. One reader for every
    page, the report, Compare and the Agent. ``unit`` is the quantity's,
    where the record does not say it."""
    if not isinstance(record, dict) or withheld_reason(record):
        return ""
    unseen = record.get("drift_unseen")
    if not isinstance(unseen, dict):
        return ""
    total, count = _number(unseen.get("segments")), _number(unseen.get("tested"))
    # A record saved before the count tested was: all of its segments were.
    count = total if count is None else count
    left_out = str(unseen.get("left_out") or "")
    if total is None or count is None:
        return ""
    total, count = int(total), int(count)
    out = f" ({left_out})" if left_out else ""
    if unseen.get("not_tested"):
        could = "none could be" if count == 0 else f"only {count} could be"
        return (f"Drift between its {total} segments was not tested: {could}{out}; "
                "longer segments would let it be")
    counted = _number(unseen.get("counted_in_pooled_errors"))
    counted = _number(unseen.get("in_pooled_errors")) if counted is None else counted
    error, change = _number(unseen.get("pooled_error")), _number(unseen.get("change"))
    if counted is None or error is None or change is None or not (
            change > 0 and error > 0 and counted > 0):
        return ""
    unit = unit if unit is not None else (record.get("unit") or record.get("units"))
    units = f" {unit}" if unit else ""
    which = (f"With {count} segments" if count == total
             else f"With {count} of its {total} segments tested{out}")
    unequal = ("" if unseen.get("equal_segments", True)
               else ", and a larger one with segment errors as unequal as these")
    scale = change / (counted * error)
    basis = (f"{counted:g} times the error of their pooled mean, "
             f"{_two_figures(error)}{units}")
    # Said only where it reads above 1.0: "times 1.0" was said.
    if f"{scale:.1f}" != "1.0" and scale > 1.0:
        basis += (f", times {scale:.1f} for their scatter about a straight line "
                  f"beyond that error, a scale estimated from only {count} means")
    return (f"{which}, a steady drift smaller than about {_two_figures(change)}{units} "
            f"from the first segment's mean to the last's could go unseen{unequal} "
            f"({basis})")


def withheld_code(record: Any) -> str:
    """The refusal code a mean's record withholds it under, or an empty
    string: the code it carries or saved (``withheld_code``); for a record
    saved before the code was, ``analysis.sampling.drifting`` where it
    names its ``drift_test`` (saved with that code alone) and
    ``analysis.sampling.still_drifting`` where its reason is the
    single-series one ("Still drifting after its equilibration")."""
    if not isinstance(record, dict):
        return ""
    reason = record.get("not_a_measurement")
    if not reason:
        return ""
    code = _code_of(reason) or record.get("withheld_code")
    if isinstance(code, str) and code and code != "unclassified":
        return code
    if record.get("drift_test"):
        return "analysis.sampling.drifting"
    if "still drifting after its equilibration" in str(reason).lower():
        return "analysis.sampling.still_drifting"
    return ""


#: The codes of a mean withheld because it is still moving: within the
#: frames it averages, or between the segments of a joined run.
DRIFTING_CODES = frozenset({"analysis.sampling.still_drifting",
                            "analysis.sampling.drifting"})


def judged_by_earlier_rules(record: Any) -> bool:
    """Whether a mean recorded as determined was judged before this
    version's rules (:data:`MEAN_RULES`), so its verdict may not be the one
    analysing again would give. Only a mean called determined is asked: the
    rules since have withheld more, never less."""
    if not isinstance(record, dict) or record.get("not_a_measurement"):
        return False
    error = record.get("standard_error")
    if not isinstance(error, (int, float)) or not math.isfinite(float(error)):
        return False
    rules = record.get("rules")
    return not isinstance(rules, int) or isinstance(rules, bool) or rules < MEAN_RULES


def too_few_by_these_rules(record: Any, *, counted: bool = True) -> str | None:
    """Why a mean an earlier version's rules called determined is not one by
    this version's: it rests on fewer independent samples than this version
    determines a mean on (:data:`MINIMUM_EFFECTIVE_SAMPLES`), which its
    record says. None for any other record. ``counted`` False leaves out
    its count, for a line that has just given it."""
    if not judged_by_earlier_rules(record):
        return None
    samples = record.get("effective_samples")
    if (isinstance(samples, bool) or not isinstance(samples, (int, float))
            or not math.isfinite(float(samples)) or samples >= MINIMUM_EFFECTIVE_SAMPLES):
        return None
    earlier = ("Its record, from an earlier version, called it determined; Analyze again "
               "to record this version's verdict.")
    if not counted:
        return (f"Fewer than the {MINIMUM_EFFECTIVE_SAMPLES:g} independent samples this "
                f"version determines a mean on. {earlier}")
    count = independent_samples_said(samples)
    return (f"{count[:1].upper()}{count[1:]}, fewer than the "
            f"{MINIMUM_EFFECTIVE_SAMPLES:g} this version determines a mean on. {earlier}")


def withheld_reason(record: Any) -> str | None:
    """Whether a recorded mean is withheld, and why: the reason its record
    gives (``not_a_measurement``), or, for a mean an earlier version called
    determined on fewer independent samples than this version determines one
    on, :func:`too_few_by_these_rules`. None where the mean is determined.

    The one reader of a mean's verdict: every page, the Agent, the report and
    Compare ask it, and none gives an error where it returns a reason. A
    mean judged by an earlier version on 7 independent samples read "not
    determined" on the Overview and with its error in the Agent's citation,
    the report and Compare, each of which read ``not_a_measurement`` alone."""
    if not isinstance(record, dict):
        return None
    reason = record.get("not_a_measurement")
    if reason:
        if isinstance(reason, dict):
            reason = reason.get("message") or reason.get("reason") or "not determined"
        return str(reason)
    return too_few_by_these_rules(record)


@dataclass(frozen=True)
class Pooled:
    """What a joined run supports, taking the joins into account.

    A trajectory assembled from segments is contiguous in time and is not
    a single sample path. Each join is a place where the reporters
    restarted and, under a barostat, where the move size re-adapted. That
    matters to two things.

    **The correlation time.** An autocorrelation function computed across
    a discontinuity reads the step as long-time correlation and inflates
    ``g``, which understates the independent samples and makes a real
    difference look unsupported. Conservative in direction, wrong in
    magnitude, and the magnitude is what a caller is deciding on.

    **The equilibration detection.** Chodera's method picks the discard
    that maximises effective samples. A jump at a join is exactly what
    that method is built to find, so on a joined series it will often
    discard everything before the last join -- throwing away nine tenths
    of a ten-segment run and reporting the remainder as if that had been
    the study.

    So each segment is analysed on its own and the results are pooled.
    Effective samples add, because the segments are disjoint in time and
    each is independent of the others by construction. The mean is
    weighted by effective samples, which is the minimum-variance
    combination of estimates with different precisions. The standard error
    comes from the pooled effective count rather than from any one
    segment.
    """

    #: Per-segment results, in order. A segment that supported nothing is
    #: absent, so this can be shorter than the number of segments.
    segments: tuple[Equilibrated, ...]
    mean: float
    standard_error: float
    effective_samples: float
    #: Segments that were dropped, and why. Kept rather than counted: a
    #: run where four of ten segments said nothing is a different object
    #: from one where all ten contributed, and a bare count does not say
    #: which.
    withheld: tuple[tuple[int, str], ...] = ()
    #: Observed scatter of the segment means over what their own standard
    #: errors predict. One means they agree.
    heterogeneity: float = 1.0
    #: The drift test's p-value: for three or four segments the larger of a
    #: slope against their errors and a slope against their own scatter (the
    #: latter alone where a line does not fit the errors); an
    #: order-permutation test for five or more.
    drift_p: float = 1.0
    #: What is true of this mean that would not be true of one from a run
    #: that stayed put. Empty where nothing is.
    qualification: str = ""

    @property
    def contributing(self) -> int:
        return len(self.segments)

    def as_record(self) -> dict[str, Any]:
        return {
            "mean": self.mean,
            "standard_error": self.standard_error,
            "effective_samples": self.effective_samples,
            "segments_contributing": self.contributing,
            "segments_withheld": [
                {"segment": index, "reason": reason}
                for index, reason in self.withheld
            ],
            "heterogeneity": self.heterogeneity,
            "drift_p": self.drift_p,
            "drift_test": _segment_drift_method(self.contributing),
            # Said plainly, because a reader comparing this against a run
            # that went through in one piece should know they are not the
            # same kind of number.
            "pooled_across_joins": True,
            **({"qualified": self.qualification} if self.qualification
               else {}),
        }


#: Why a segment is left out of the test for drift between segments, said of
#: one and of several.
_LEFT_OUT = {"short": ("too short for a mean of its own", "too short for means of their own"),
             "moving": ("still drifting within itself", "still drifting within themselves"),
             "unresolved": ("its correlation not resolved", "their correlation not resolved")}


def _left_out_said(out: "list[tuple[int, str]]") -> str:
    """The segments left out of the test, grouped by why, in order: "3 left
    out, numbered from zero: 0, 1 and 4, their correlation not resolved; 6,
    too short for a mean of its own". Empty for none."""
    if not out:
        return ""
    groups: dict[str, list[int]] = {}
    for index, why in sorted(out):
        groups.setdefault(why, []).append(index)

    def listed(indices: "list[int]") -> str:
        named = [str(i) for i in indices]
        return named[0] if len(named) == 1 else ", ".join(named[:-1]) + " and " + named[-1]

    return (f"{len(out)} left out, numbered from zero: "
            + "; ".join(f"{listed(indices)}, {_LEFT_OUT[why][len(indices) > 1]}"
                        for why, indices in groups.items()))


def _code_of(reason: Any) -> str:
    """The refusal code a withholding carries, or an empty string."""
    return str(getattr(getattr(reason, "refusal", None), "code", "") or "")


def summarise_segments(
    series: np.ndarray,
    joins: "list[int] | tuple[int, ...]",
    *,
    minimum_effective_samples: float = MINIMUM_EFFECTIVE_SAMPLES,
    tested_into: "dict[str, Any] | None" = None,
) -> "tuple[Pooled | None, Withholding | None]":
    """Summarise a joined series, analysing each segment on its own.

    Parameters
    ----------
    series
        The whole joined series, in order.
    joins
        Frame indices where a new segment begins. The first segment starts
        at zero and is not listed. An empty list means the run went
        through in one piece, and this falls through to
        :func:`summarise` -- so a caller need not branch on whether a run
        was segmented.
    tested_into
        Where given, filled with what the test for drift between segments
        tested, whatever is returned: the segments (``segments``), how many
        were tested (``tested``, those whose errors can be read), which were
        left out and why (``left_out``), whether drift was called
        (``called``), the error of the tested means' inverse-variance pooled
        mean (``pooled_error``), their scatter about the weighted line over
        what their errors allow (``line_scatter``, the line's chi-square per
        degree of freedom, at three or four), and their lengths
        (``lengths``). Left empty for a run in one piece.

    Returns
    -------
    (Pooled, None) or (None, Withholding)
        Withheld where no segment supported a mean, where the segment means
        move in order (``analysis.sampling.drifting``), where a segment is
        still moving within itself (``analysis.sampling.still_drifting``),
        where any segment
        that supported a mean did not resolve its own correlation time
        (``analysis.sampling.correlation_unresolved``: its independent-sample
        count is an upper bound, so the pooled error would be a lower one),
        or where the pooled effective count falls short. Pooling does not
        rescue a run that
        was too short: ten segments of two independent samples each is
        twenty, and twenty is twenty however it was collected -- but ten
        segments that each support nothing support nothing together.
    """
    raw = np.asarray(series, dtype=float)
    finite = np.isfinite(raw)
    values = raw[finite]
    # The joins are frame numbers of the series as given. Values that are not
    # finite are left out first, so each join moves back by those before it:
    # left where they were, the segments after a stretch of NaN began
    # several frames late and took frames of the next one with them.
    before = np.concatenate(([0], np.cumsum(finite)))
    boundaries = sorted({int(before[min(int(j), raw.size)]) for j in joins
                         if 0 < int(j) < raw.size}
                        - {0, int(values.size)})

    if not boundaries:
        equilibrated, why = summarise(
            values, minimum_effective_samples=minimum_effective_samples)
        if equilibrated is None:
            return None, why
        return Pooled(segments=(equilibrated,), mean=equilibrated.mean,
                      standard_error=equilibrated.standard_error,
                      effective_samples=equilibrated.effective_samples), None

    edges = [0, *boundaries, values.size]
    pieces: list[Equilibrated] = []
    kept_at: list[int] = []
    reasons: list[Any] = []
    withheld: list[tuple[int, str]] = []
    for index, (start, stop) in enumerate(zip(edges, edges[1:])):
        # Each segment is equilibrated on its own. A segment that begins
        # after a join has its own approach to equilibrium -- under a barostat
        # the move size is re-adapting -- and detecting that per segment is
        # the point, not an inconvenience.
        piece, why = summarise(
            values[start:stop],
            minimum_effective_samples=0.0)
        if piece is None:
            withheld.append((index, str(why)))
            continue
        pieces.append(piece)
        kept_at.append(index)
        reasons.append(why)

    if not pieces:
        if tested_into is not None:
            tested_into.update(segments=len(edges) - 1, tested=0, called=False,
                               left_out=_left_out_said([(index, "short")
                                                        for index, _ in withheld]))
        return None, Withholding(
            "No segment of this joined run supports a mean. Pooling does "
            "not rescue a run that was too short: segments that each say "
            "nothing say nothing together.",
            code="analysis.sampling.too_few_independent",
            independent=0.0, frames=int(values.size),
            needed=float(minimum_effective_samples),
        )

    # A segment whose own error was withheld (its correlation time is not
    # resolved, or it holds one independent sample) has an independent-sample
    # count that is an upper bound. Pooled in, it made the pooled error too
    # small, and its NaN error made the agreement test NaN, which no
    # comparison passes: on joined AR(1) runs with g = 200 in segments of
    # 400 frames the drift refusal never fired on a ramp of four standard
    # deviations, and the pooled error held the truth 21% of the time.
    errors = np.array([p.standard_error for p in pieces], dtype=float)
    resolved = np.isfinite(errors) & (errors > 0.0)
    means = np.array([p.mean for p in pieces], dtype=float)
    # Where each mean sits in time: the centre frame of the frames it is
    # taken over, after its own segment's discard. Against segment index, a
    # drift steady in time over segments of 500, 500 and 4,000 frames bent
    # off a straight line and was missed the more the larger it was.
    centres = np.array([edges[index] + p.discard
                        + (edges[index + 1] - edges[index] - p.discard - 1) / 2.0
                        for index, p in zip(kept_at, pieces)], dtype=float)

    # Before anything is pooled: do these segments agree that they are
    # measuring one thing? Pooling assumes they do, and pooling estimates of
    # a moving target gives a confident number for a quantity that does not
    # exist. Asked first, of the segments whose errors can be read, because
    # a run still moving is the more useful thing to say: longer segments
    # would not cure it.
    # Which segments the test leaves out, and why, for its refusal to say.
    out = [(index, "short") for index, _why in withheld]
    out += [(index, "moving" if _code_of(why) == "analysis.sampling.still_drifting"
             else "unresolved")
            for index, ok, why in zip(kept_at, resolved, reasons) if not ok]
    left_out = _left_out_said(out)
    scatter, drifting, refusal = _drift_test(
        means[resolved], 1.0 / errors[resolved] ** 2, centres[resolved],
        left_out=left_out)
    if tested_into is not None:
        precisions = 1.0 / errors[resolved] ** 2
        count = int(resolved.sum())
        tested_into.update(
            segments=len(edges) - 1, tested=count, left_out=left_out,
            called=refusal is not None,
            pooled_error=(float(1.0 / np.sqrt(precisions.sum())) if count else float("nan")),
            line_scatter=(_line_scatter(means[resolved], precisions, centres[resolved])
                          if 3 <= count <= 4 else float("nan")),
            lengths=[int(edges[index + 1] - edges[index])
                     for index, ok in zip(kept_at, resolved) if ok],
            errors=[float(e) for e in errors[resolved]])
    if refusal is not None:
        return None, refusal

    # A segment still moving within itself is withheld for that, and its
    # remedy is a longer run, not longer segments: reported under its own
    # code rather than as a correlation time it may well have resolved.
    moving = [(index, why) for index, ok, why in zip(kept_at, resolved, reasons)
              if not ok and _code_of(why) == "analysis.sampling.still_drifting"]
    if moving:
        details = [why.refusal.details for _index, why in moving]
        drift = max(abs(float(d.get("drift_in_spread", 0.0))) for d in details)
        trends = [abs(float(d["trend_in_errors"])) for d in details
                  if "trend_in_errors" in d]
        by_spread = drift >= DRIFT_IN_SPREAD
        how = (f"the last third of one differs from its first by "
               f"{drift:.1f} times their spread" if by_spread else
               f"one trends by {max(trends):.1f} times the trend's own error"
               if trends else "their frames still move one way")
        return None, Withholding(
            f"{len(moving)} of {len(pieces)} segments of this joined run are "
            f"still drifting within themselves (numbered "
            f"{', '.join(str(i) for i, _why in moving)} from zero): {how}. Each "
            "segment is judged on its own, so a run of many stationary ones is "
            "now and then withheld for one of them. A mean of a quantity "
            "still moving describes where the run happened to stop, not a "
            "state, and pooling such segments would give that a confident "
            "error. The remedy is a longer run.",
            code="analysis.sampling.still_drifting",
            frames=int(values.size), drift_in_spread=round(drift, 2),
            **({} if by_spread or not trends
               else {"trend_in_errors": round(max(trends), 2)}),
            needed=DRIFT_IN_SPREAD if by_spread else DRIFT_IN_ERRORS,
        )

    if not resolved.all():
        unresolved = [index for index, ok in zip(kept_at, resolved) if not ok]
        worst = min((p for p, ok in zip(pieces, resolved) if not ok),
                    key=lambda p: p.effective_samples)
        return None, Withholding(
            f"{len(unresolved)} of {len(pieces)} segments of this joined run "
            f"are not long against their own correlation time (numbered "
            f"{', '.join(str(i) for i in unresolved)} from zero; the least "
            f"resolved holds {worst.effective_samples:.1f} independent "
            f"samples by its own estimate, against the "
            f"{RESOLVED_SAMPLES:g} that resolve it). "
            "Their independent-sample counts are upper bounds, so a pooled "
            "error built on them would be too small, and the test for drift "
            "between segments cannot be read from them. The remedy is longer "
            "segments.",
            code="analysis.sampling.correlation_unresolved",
            frames=int(values.size),
            independent=float(worst.effective_samples),
            statistical_inefficiency=float(worst.inefficiency),
            needed=float(RESOLVED_SAMPLES),
        )

    weights = np.array([p.effective_samples for p in pieces], dtype=float)
    total = float(weights.sum())

    if total < minimum_effective_samples:
        return None, Withholding(
            f"{total:.1f} independent samples across {len(pieces)} "
            f"segment(s), against the {minimum_effective_samples:g} a mean "
            "needs. The segments are disjoint in time so their independent "
            "samples add, and they still do not reach it. The remedy is "
            "longer segments or more of them.",
            code="analysis.sampling.too_few_independent",
            independent=total, frames=int(values.size),
            needed=float(minimum_effective_samples),
        )

    # Weighting by effective samples is the minimum-variance combination of
    # estimates whose variances differ, which is what segments of unequal
    # usable length give.
    mean = float((weights * means).sum() / total)
    variances = np.array([p.standard_deviation ** 2 for p in pieces])
    pooled_variance = float((weights * variances).sum() / total)
    standard_error = float(np.sqrt(pooled_variance / total))

    qualification = ""
    if scatter > HETEROGENEITY_QUALIFY_ABOVE:
        if 3 <= len(pieces) <= 4 and not _beyond_chance(scatter, len(pieces)):
            # A ratio above 2 arises by chance in 1 of 7 stationary runs of
            # three segments: qualified, but not said to be more than that.
            qualification = (
                f"The segment means scatter {scatter:.1f} times more than their "
                "own standard errors predict. Treat the error on this mean as a "
                "lower bound.")
        elif 3 <= len(pieces) <= 4:
            qualification = (
                f"The segment means scatter {scatter:.1f} times more than their "
                "own standard errors predict, without a straight-line trend "
                "that stands against both those errors and their own scatter "
                "about the line. This does not establish "
                "stationarity: unordered offsets or nonlinear movement can "
                "also disagree this way. Treat the error on this mean as a "
                "lower bound.")
        elif len(pieces) == 2:
            # Two means have no order to test: "that is not drift" was said
            # of a temperature climbing from 177.8 to 185.5 K.
            qualification = (
                "The two segment means disagree beyond their own standard "
                f"errors (they scatter {scatter:.1f} times more than those "
                "predict), and two segments cannot tell a drift from offsets "
                "between them. Treat the error on this mean as a lower bound.")
        else:
            # Five or more: the order test, which can reach 2/120 at five,
            # did not call drift.
            qualification = (
                f"The segment means scatter {scatter:.1f} times more than their "
                "own standard errors predict, in no order the test for drift "
                "calls. That says the per-segment errors are too small, "
                "usually because the statistical inefficiency did not fully "
                "capture the correlation. Treat the error on this mean as a "
                "lower bound.")

    return Pooled(
        segments=tuple(pieces),
        mean=mean,
        standard_error=standard_error,
        effective_samples=total,
        withheld=tuple(withheld),
        heterogeneity=float(scatter),
        drift_p=float(drifting),
        qualification=qualification,
    ), None


def _beyond_chance(scatter: float, count: int) -> bool:
    """Whether segment means scatter beyond chance at
    :data:`DRIFT_SIGNIFICANT_BELOW`: Cochran's Q (``scatter`` times
    ``count - 1``) against its chi-square on ``count - 1`` degrees of
    freedom, for three or four means (survival functions exp(-Q/2) and
    erfc(sqrt(Q/2)) + sqrt(2Q/pi) exp(-Q/2)). Not a number counts as beyond."""
    q = float(scatter) * (count - 1)
    if not math.isfinite(q):
        return True
    survival = (math.exp(-q / 2.0) if count == 3 else
                math.erfc(math.sqrt(q / 2.0)) + math.sqrt(2.0 * q / math.pi) * math.exp(-q / 2.0))
    return survival < DRIFT_SIGNIFICANT_BELOW


def _drift_test(means: np.ndarray, precisions: np.ndarray,
                positions: "np.ndarray | None" = None, *, left_out: str = "",
                ) -> "tuple[float, float, Withholding | None]":
    """The heterogeneity, the drift p-value, and the refusal for segment
    means that move in order (None where they do not). ``positions`` is
    where each mean sits in time (its centre frame); segment order where
    not given. ``left_out`` says which segments were not tested and why,
    for the refusal to say beside how many were.

    Both a trend (``drift_across_segments`` at most 0.05) and a
    disagreement (``heterogeneity_ratio`` above one) are needed, because
    either alone is not drift: a low p on segments that agree is a trend of
    nothing, and scatter with no order is underestimated error rather than
    movement. A heterogeneity that is not a finite number counts as
    disagreement rather than agreement, so a NaN cannot switch the test off.
    """
    scatter = heterogeneity_ratio(means, precisions)
    if positions is None:
        positions = np.arange(means.size, dtype=float)
    positions = np.asarray(positions, dtype=float)
    drifting = drift_across_segments(means, precisions, positions=positions)
    agree = bool(np.isfinite(scatter) and scatter <= 1.0)
    # At the level, not only below it: an exact count of 120 orderings
    # reaches 6/120 = 0.05 itself.
    if not drifting <= DRIFT_SIGNIFICANT_BELOW or agree:
        return scatter, drifting, None
    span = float(means[-1] - means[0])
    chance = (f"about {drifting:.1%}" if drifting >= 0.001
              else "less than 0.1%")
    method = _segment_drift_method(means.size)
    steps = np.diff(means)
    # "In order" only where every step goes the same way: a trend can be
    # called on means that step back once, and saying they move one way
    # would describe means the reader can see do not.
    one_way = bool(np.all(steps > 0.0) or np.all(steps < 0.0))
    # How many were tested, and any left out and why: "first to last" on
    # six pieces read as all six where one was not among them.
    tested = (f"the means of the {means.size} segments tested"
              + (f" ({left_out})" if left_out else ""))
    if method == "slope_against_errors_and_scatter":
        change = (_weighted_slope(means, precisions, positions)
                  * float(positions[-1] - positions[0]))
        movement = (
            f"A straight line through {tested} changes by {change:+.4g} "
            f"across the run (first to last mean {span:+.4g}"
            + ("" if one_way else ", though they do not move one way at every step")
            + ")")
        readable = bool(np.all(np.isfinite(precisions)) and np.all(precisions > 0.0))
        fits = readable and _short_segment_halves(means, precisions, positions)[0] is not None
        evidence = (
            "its slope stands both against the segments' own standard errors, "
            "with the line consistent with those errors, and against the "
            f"means' own scatter about it: the larger p-value is {chance}"
            if fits else
            f"its slope stands against the means' own scatter about it (p {chance}), "
            "though they stray from a straight line by more than their own "
            "standard errors allow")
    else:
        movement = (
            f"{tested[:1].upper()}{tested[1:]} move in order across the run, by "
            f"{span:+.4g} from first to last" if one_way else
            f"{tested[:1].upper()}{tested[1:]} trend across the run, by "
            f"{span:+.4g} from first to last, though not one way at every step")
        evidence = f"an ordering this clean arises by chance {chance} of the time"
    # The verdict first, with its p and remedy: a page that shows the first
    # sentence alone (the Overview's card) showed the line's arithmetic and
    # not that the mean is drifting.
    said = (
        f"Drifting between its joined segments (p {chance}), so the remedy is "
        f"a longer run. {movement}, and {evidence}. The system had not "
        "equilibrated at the scale of the whole run, so a pooled mean "
        "would be the mean of a moving target with a confident error "
        "bar on it, which more pooling does not cure.")
    return scatter, drifting, Withholding(
        said,
        code="analysis.sampling.drifting",
        drift_p=float(drifting), heterogeneity=float(scatter),
        span=span, segments=int(means.size),
        drift_test=method,
    )


#: Observed scatter of segment means over what their own standard errors
#: predict. One means they agree. Above this they do not, which says the
#: per-segment errors are too small -- the usual cause being correlation
#: the inefficiency did not fully capture.
HETEROGENEITY_QUALIFY_ABOVE = 2.0

#: The significance threshold for a segment-mean trend, and the lack-of-fit
#: threshold below which a short run's straight-line explanation is rejected.
DRIFT_SIGNIFICANT_BELOW = 0.05

#: Orderings counted exactly rather than sampled, at most: every ordering
#: of up to seven segments (5040).
EXACT_ORDERINGS_AT_MOST = 5040



def _weighted_slope(means: np.ndarray, weights: np.ndarray,
                    positions: np.ndarray) -> float:
    """Weighted least-squares slope of segment mean against ``positions``."""
    total = weights.sum()
    mean_x = float((weights * positions).sum() / total)
    mean_y = float((weights * means).sum() / total)
    spread = float((weights * (positions - mean_x) ** 2).sum())
    if spread <= 0:
        return 0.0
    return float((weights * (positions - mean_x) * (means - mean_y)).sum()
                 / spread)


def _segment_drift_method(count: int) -> str:
    if count < 3:
        return "not_tested"
    return "slope_against_errors_and_scatter" if count < 5 else "permutation"


def _own_scatter_slope_p(means: np.ndarray, positions: np.ndarray) -> float:
    """The slope's two-sided p-value against the means' own scatter about
    the line: an unweighted fit, Student's t on k - 2 degrees of freedom.

    Unweighted, because this half of the test is for the case where the
    segments' errors do not hold (offsets between segments beyond them), and
    then the errors are not the right weights either: weighted by them, an
    imprecise segment between two precise ones leaves almost no residual to
    judge the slope against. For k = 3 and 4 the t tails are
    1 - (2/pi) atan|t| and 1 - |t| / sqrt(2 + t^2). ``positions`` is where
    each mean sits in time.
    """
    count = means.size
    x = positions - float(positions.mean())
    y = means - means[0]
    y = y - float(y.mean())
    spread = float(x @ x)
    slope = float(x @ y) / spread
    residual = y - slope * x
    variance = float(residual @ residual) / (count - 2)
    if slope == 0.0:
        return 1.0
    if variance <= 0.0:
        return math.nextafter(0.0, 1.0)
    t = abs(slope) * math.sqrt(spread / variance)
    if not math.isfinite(t):
        return math.nextafter(0.0, 1.0) if math.isinf(t) else 1.0
    p = (1.0 - 2.0 / math.pi * math.atan(t) if count == 3
         else 1.0 - t / math.sqrt(2.0 + t * t))
    return max(p, math.nextafter(0.0, 1.0))


def _short_segment_halves(means: np.ndarray, precisions: np.ndarray,
                          positions: np.ndarray) -> "tuple[float | None, float]":
    """The two halves of the three- and four-segment test: the slope's
    p-value against the segments' errors (None where a line does not fit
    those errors) and against the means' own scatter about the line."""
    own = _own_scatter_slope_p(means, positions)
    scale = float(precisions.max())
    weights = precisions / scale
    total = float(weights.sum())
    x = positions - float(weights @ positions / total)
    # A common offset is irrelevant to a slope and can otherwise lose
    # precision when large compared with the differences being tested.
    relative = means - means[0]
    y = relative - float(weights @ relative / total)
    spread = float(weights @ (x * x))
    if spread <= 0 or not np.all(np.isfinite(y)):
        return None, 1.0
    slope = float(weights @ (x * y) / spread)
    residual = y - slope * x
    q = float(weights @ (residual * residual)) * scale
    if not math.isfinite(q):
        return None, own
    fit_p = (math.erfc(math.sqrt(q / 2.0)) if means.size == 3
             else math.exp(-q / 2.0))
    if fit_p < DRIFT_SIGNIFICANT_BELOW:
        return None, own
    z = abs(slope) * math.sqrt(scale) * math.sqrt(spread)
    # An extreme normal tail can underflow, rather than be exactly zero.
    return max(math.erfc(z / math.sqrt(2.0)), math.nextafter(0.0, 1.0)), own


def _line_scatter(means: np.ndarray, precisions: np.ndarray,
                  positions: np.ndarray) -> float:
    """The means' scatter about a straight line over what their errors
    allow, about one where the errors hold: the larger of the weighted
    line's chi-square over its k - 2 degrees of freedom, and the scatter
    the rule's own-scatter half judges the slope against (the unweighted
    line's residual variance) over the means' mean error variance. The
    weighted one alone gave an imprecise middle segment little weight and
    read the scatter the rule decides on as smaller than it is."""
    if means.size < 3:
        return float("nan")
    total = float(precisions.sum())
    x = positions - float(precisions @ positions / total)
    relative = means - means[0]
    y = relative - float(precisions @ relative / total)
    spread = float(precisions @ (x * x))
    if spread <= 0:
        return float("nan")
    residual = y - float(precisions @ (x * y) / spread) * x
    weighted = float(precisions @ (residual * residual)) / (means.size - 2)
    u = positions - float(positions.mean())
    v = relative - float(relative.mean())
    plain = v - float(u @ v) / float(u @ u) * u
    unweighted = (float(plain @ plain) / (means.size - 2)) / float(np.mean(1.0 / precisions))
    return max(weighted, unweighted)


def _short_segment_drift_p(means: np.ndarray, precisions: np.ndarray,
                           positions: np.ndarray) -> float:
    """A slope test against the segments' errors and against their own
    scatter where a line fits those errors (the larger p-value), and
    against their own scatter alone where it does not.

    For independent Gaussian mean errors, Var(slope) = 1 / Sxx, with
    inverse-variance weights. Its two-sided normal tail uses the supplied
    errors, not a variance estimated from the line's residuals. The latter
    would give a perfect line zero error however uncertain its means were.

    A jump or unordered offsets can have a significant fitted slope too.
    So the errors' half is read only where the weighted residual sum is
    compatible with chi-square on k - 2 degrees of freedom at 0.05 (for
    k = 3 or 4 the survival functions are erfc(sqrt(Q/2)) and exp(-Q/2)).

    That alone trusts the errors, and one residual degree of freedom (two at
    k = 4) cannot tell errors that hold from errors too small for offsets
    between segments: on unordered offsets about three times the errors it
    called 12% to 40% of runs drifting (``docs/short-segment-drift.md``).
    So the slope must also stand against the means' own scatter
    (:func:`_own_scatter_slope_p`), which holds about 5% whatever the size
    of such offsets. Where the line does not fit the errors, that half
    alone decides: returning 1 there pooled a large monotone drift off a
    straight line (four temperatures rising 177.8, 185.4, 186.6, 192.6 K
    over errors of 0.5 to 0.7 K), and the larger the drift against the
    errors, the surer it failed the fit. The price is power at three
    segments, where the scatter has one degree of freedom: a line must be
    close to straight to be called drift.

    Both halves fit the means against ``positions``, where each sits in
    time, so a drift steady in time is a straight line however unequal
    the segments.
    """
    against_errors, own = _short_segment_halves(means, precisions, positions)
    return own if against_errors is None else max(against_errors, own)


def drift_across_segments(means: np.ndarray, weights: np.ndarray, *,
                          positions: "np.ndarray | None" = None,
                          permutations: int = 4999,
                          seed: int = 0) -> float:
    """A p-value for drift across segment means, using their own errors.

    Scatter and drift look the same in a list of numbers and mean
    different things. Segment means that disagree but in no order are
    saying the per-segment errors are too small. Segment means that climb
    are saying the system was still moving, and then a pooled mean is the
    mean of a moving target with a confident error bar on it -- the worst
    of the three outcomes, because it is the one that looks most like a
    measurement.

    With three or four means and finite positive inverse-variance weights,
    use a two-sided Gaussian slope test (:func:`_short_segment_drift_p`).
    It assumes approximately independent, Gaussian segment-mean errors,
    whose supplied standard errors approximate their absolute scale. The
    slope must also stand against the means' own scatter about the line
    (Student's t on k - 2 degrees of freedom), and the larger of the two
    p-values is returned, so offsets between segments larger than their
    errors are not read as a trend. Where a line does not fit those errors
    at 0.05, the scatter's p-value alone is returned. Both fit the means against
    ``positions``, where each sits in time (the centre frame of the frames
    it is taken over); segment order where not given.

    With five or more segments, permute their order as before, without a
    distributional assumption. The statistic is the weighted least-squares
    slope against segment index; the null is what that slope looks like
    when the same segment means are put in another order. Every ordering is
    counted where there are at most :data:`EXACT_ORDERINGS_AT_MOST` of them
    (seven segments), and ``permutations`` are drawn at random beyond that.

    Reversing an order negates the slope, so on ``k`` segments a permutation
    test can say no less than ``2 / k!``: a third for three segments and a twelfth
    for four, both above :data:`DRIFT_SIGNIFICANT_BELOW`. It cannot call
    three or four segments drifting, whatever their means, which is why
    those counts use their errors rather than their ordering alone.

    Returns 1.0 for fewer than three segments: two points always lie on a
    line, and calling that a trend would refuse every two-segment run; and
    1.0 where a mean is not a number. Weights that are not all positive
    numbers fall back to the order-permutation test with equal weights,
    including at three or four means: an unknown error is not a unit error.
    """
    if means.size < 3:
        return 1.0
    if not np.all(np.isfinite(means)):
        return 1.0

    order = np.arange(means.size, dtype=float)
    readable = np.all(np.isfinite(weights)) and np.all(weights > 0.0)
    if means.size < 5 and readable:
        at = order if positions is None else np.asarray(positions, dtype=float)
        if at.shape != means.shape or not np.all(np.isfinite(at)):
            at = order
        return _short_segment_drift_p(means, weights, at)
    # The order test is rank-free: it asks the order alone, not the times.
    positions = order
    if not readable:
        # Weights that cannot be read leave the order alone to judge, so a
        # NaN cannot switch the test off.
        weights = np.ones(means.size)
    observed = abs(_weighted_slope(means, weights, positions))
    if not np.isfinite(observed):
        return 1.0
    # The mirror ordering gives the same slope to the last bit only by luck
    # of rounding; a slope equal within rounding is as extreme.
    level = observed * (1.0 - 1e-9)

    if math.factorial(means.size) <= EXACT_ORDERINGS_AT_MOST:
        from itertools import permutations as orderings

        slopes = [abs(_weighted_slope(means[list(o)], weights[list(o)], positions))
                  for o in orderings(range(means.size))]
        return float(sum(1 for c in slopes if c >= level)) / len(slopes)

    rng = np.random.default_rng(seed)
    order = np.arange(means.size)
    at_least_as_extreme = 0
    for _ in range(permutations):
        shuffled = rng.permutation(order)
        candidate = abs(_weighted_slope(means[shuffled], weights[shuffled],
                                        positions))
        if candidate >= level:
            at_least_as_extreme += 1
    # The +1s are the standard correction: the observed ordering is itself
    # one of the orderings, and leaving it out lets a p-value of exactly
    # zero be reported, which no permutation test can support.
    return (at_least_as_extreme + 1) / (permutations + 1)


def heterogeneity_ratio(means: np.ndarray, weights: np.ndarray) -> float:
    """Observed scatter of segment means over what their errors predict.

    Cochran's Q divided by its degrees of freedom. One means the segments
    agree as well as their own standard errors say they should. Much above
    one means they do not, and the honest reading is that the per-segment
    errors are too small rather than that the segments disagree about
    physics.

    Reported as a ratio rather than a p-value on purpose. A ratio of three
    is plainly too much and needs no distribution to say so, and reaching
    for a chi-squared here would import a dependency to dress up a number
    that is already legible.
    """
    if means.size < 2:
        return 1.0
    pooled = float((weights * means).sum() / weights.sum())
    q = float((weights * (means - pooled) ** 2).sum())
    return q / (means.size - 1)


def four_figures(value: float) -> str:
    """A value to four significant figures, grouped, and as an exponent only
    where it is very large or very small: -45,472 rather than -4.547e+04,
    which the Overview, the report and the Agent each printed differently.
    A trailing zero is one of the four figures and stays: 1.0004 nm is
    "1.000 nm", where dropping it said "1 nm", a value known to one."""
    if not math.isfinite(value) or value == 0:
        return f"{value:g}"
    magnitude = math.floor(math.log10(abs(value)))
    if magnitude >= 6 or magnitude < -4:
        return f"{value:#.4g}"
    places = max(0, 3 - magnitude)
    said = f"{value:,.{places}f}"
    # Rounding up to the next power of ten (9.9996 to "10.000") gives a fifth.
    if len(said.lstrip("-").replace(",", "").replace(".", "").lstrip("0")) > 4 and places:
        said = f"{value:,.{places - 1}f}"
    return said


def with_its_error(value: float, error: float | None, *, sign: bool = False) -> str:
    """A value and its standard error as every page gives them: the error to
    two figures and the value to the same decimal place, so neither says
    more than the other. With no error, four significant figures; with an
    error of zero, that zero.

    One function for the report, the slides, the campaign's tables, the GUI
    and the Agent. The report printed ``0.112 ± 0.00201`` where the GUI
    beside it printed ``0.1120 ± 0.0020``: the same record, two numbers a
    reader has to reconcile, and the first gives the error three figures it
    does not have.
    """
    if error is None or not math.isfinite(error) or error < 0:
        said = four_figures(value)
    elif error == 0:
        said = f"{four_figures(value)} \u00b1 0"
    else:
        places = max(0, 1 - math.floor(math.log10(error)))
        said = f"{value:,.{places}f} \u00b1 {error:,.{places}f}"
    return f"+{said}" if sign and not said.startswith("-") else said


# ---------------------------------------------------------------------------
# How a series converged, for the page that shows it
# ---------------------------------------------------------------------------
#: Points on the running mean. Fifty shows its shape without a point per
#: frame, and each costs a correlation estimate.
RUNNING_POINTS = 50

#: Most bins a histogram is given. Freedman-Diaconis on a million values
#: asks for hundreds, finer than a figure can show.
HISTOGRAM_BINS_AT_MOST = 60

#: Fewest finite values :func:`convergence_of` reads. Below this the
#: equilibration detector does not look (it needs ten), and a running mean,
#: a blocking curve and a correlation function would each be a handful of
#: points that say nothing about convergence.
CONVERGENCE_FEWEST_VALUES = 10

#: Lags of the first pass at the correlation of many suffixes at once. A
#: series whose correlation has not decided by 16 times this is finished
#: exactly, one suffix at a time.
_FIRST_LAGS = 1024

#: Shortest transform the pieces are computed in; each piece is as long as
#: the transform less the lags, so little of it is padding.
_TRANSFORM = 16384


def _lagged_sums(x: np.ndarray, bounds: np.ndarray, lags: int) -> np.ndarray:
    """``D[j, t] = sum x[i] x[i + t]`` over ``i`` in piece ``j``
    (``bounds[j] <= i < bounds[j + 1]``) and ``i + t < n``, for
    ``t < lags``: each piece's cross-correlation with what follows it, by
    Fourier transform, so the sum over a suffix is a sum over pieces."""
    n = x.size
    pieces = bounds.size - 1
    longest = int(np.max(np.diff(bounds)))
    size = 1 << int(longest + lags).bit_length()
    sums = np.empty((pieces, lags), dtype=float)
    batch = max(1, (1 << 22) // size)
    for first in range(0, pieces, batch):
        rows = range(first, min(pieces, first + batch))
        u = np.zeros((len(rows), size))
        v = np.zeros((len(rows), size))
        for row, j in enumerate(rows):
            a, b = int(bounds[j]), int(bounds[j + 1])
            u[row, :b - a] = x[a:b]
            end = min(n, b + lags - 1)
            v[row, :end - a] = x[a:end]
        product = np.conj(np.fft.rfft(u, axis=1)) * np.fft.rfft(v, axis=1)
        sums[first:first + len(rows)] = np.fft.irfft(product, size, axis=1)[:, :lags]
    return sums


def _suffix_correlations(x: np.ndarray, starts: "list[int]",
                         lags: int) -> "dict[int, np.ndarray]":
    """``C(t)`` of ``x[s:]`` about its own mean for each start ``s``, at lags
    ``t < min(lags, n - s)``: the numbers :func:`_correlation` gives for that
    suffix, from one pass over the series rather than one per start.

    ``x`` is centred on its overall mean first, so the sums stay near the
    size of the fluctuations. A suffix that is constant is left out.
    """
    n = x.size
    x = x - x.mean()
    lowest = min(starts)
    piece = max(_TRANSFORM, 1 << int(4 * lags - 1).bit_length()) - lags
    bounds = np.unique(np.concatenate([
        np.asarray(starts, dtype=int), np.arange(lowest, n, piece), [n]]))
    sums = _lagged_sums(x, bounds, lags)
    from_here = np.cumsum(sums[::-1], axis=0)[::-1]
    row_of = {int(b): j for j, b in enumerate(bounds[:-1])}
    running = np.concatenate(([0.0], np.cumsum(x)))
    changes = np.flatnonzero(x[1:] != x[:-1])
    constant_from = int(changes[-1]) + 1 if changes.size else 0

    out: dict[int, np.ndarray] = {}
    for s in starts:
        count = n - s
        if count < 1 or s >= constant_from:
            continue
        t = np.arange(min(lags, count))
        mean = (running[n] - running[s]) / count
        covariance = (from_here[row_of[s], :t.size]
                      - mean * ((running[n - t] - running[s])
                                + (running[n] - running[s + t]))
                      + mean ** 2 * (count - t))
        if covariance[0] <= 0.0:
            continue
        correlation = covariance / covariance[0]
        correlation[0] = 1.0
        out[s] = correlation
    return out


def _suffix_inefficiencies(values: np.ndarray, starts: "list[int]",
                           correlations_of: "dict[int, np.ndarray] | None" = None,
                           *, first_lags: int = _FIRST_LAGS,
                           ) -> "dict[int, tuple[float, int]]":
    """For each start, :func:`statistical_inefficiency` of ``values[s:]``
    and the pairs of lags :func:`_geyer` summed for it, which is what
    :func:`_for_the_mean` corrects with. The same numbers, computed for
    many suffixes together: lags are added until each suffix's sum of pairs
    has stopped, and a suffix still undecided at 16384 lags is computed
    on its own. ``correlations_of``, where given, receives each suffix's
    correlation as far as it was computed; ``first_lags`` is where the
    lags start."""
    n = values.size
    found: dict[int, tuple[float, int]] = {}
    store = correlations_of if correlations_of is not None else {}
    pending = []
    for s in sorted({int(s) for s in starts}):
        count = n - s
        if count < 3:
            found[s] = (1.0, 0)
        else:
            pending.append(s)
    lags = int(first_lags)
    while pending and lags <= 16 * _FIRST_LAGS:
        correlations = _suffix_correlations(values, pending, lags)
        still = []
        for s in pending:
            count = n - s
            if s not in correlations:
                # Constant from here: frames that never change are one
                # observation, as `statistical_inefficiency` says.
                found[s] = (float(count), 0)
                continue
            g, _decayed, pairs, decided = _geyer_from(correlations[s], count)
            store[s] = correlations[s]
            if decided:
                found[s] = (g, pairs)
            else:
                still.append(s)
        pending = still
        lags *= 16
    for s in pending:
        kept = values[s:]
        fluctuation = kept - kept.mean()
        variance = float(np.mean(fluctuation ** 2))
        correlation = _correlation(fluctuation, variance)
        g, _decayed, pairs, _decided = _geyer_from(correlation, kept.size)
        store[s] = correlation
        found[s] = (g, pairs)
    return found


def _plain(value: Any) -> Any:
    """A float for JSON: ``None`` for what is not a finite number."""
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _plain_list(values: Any) -> "list[float | None]":
    return [_plain(v) for v in np.asarray(values, dtype=float).ravel()]


def _histogram(values: np.ndarray) -> "dict[str, list[Any]] | None":
    """Counts and edges, Freedman-Diaconis bins (width ``2 IQR / n^(1/3)``),
    at most :data:`HISTOGRAM_BINS_AT_MOST` and at least one."""
    if values.size == 0:
        return None
    low, high = float(values.min()), float(values.max())
    q75, q25 = np.percentile(values, [75.0, 25.0])
    width = 2.0 * float(q75 - q25) / values.size ** (1.0 / 3.0)
    bins = (int(np.ceil((high - low) / width)) if width > 0.0 and high > low
            else 1)
    bins = max(1, min(HISTOGRAM_BINS_AT_MOST, bins))
    counts, edges = np.histogram(values, bins=bins)
    return {"edges": _plain_list(edges), "counts": [int(c) for c in counts]}


def _blocking(values: np.ndarray) -> dict[str, Any]:
    """Flyvbjerg-Petersen block averaging of ``values``.

    For block lengths ``b = 1, 2, 4, ...`` up to ``n / 4``, the ``n_b =
    floor(n / b)`` means of consecutive blocks of the first ``n_b b`` values
    give the standard error of the mean ``sqrt(var(block means) / n_b)``
    (``ddof = 1``), itself uncertain by ``SE / sqrt(2 (n_b - 1))``. Blocks
    longer than the correlation make the error stop growing.

    No plateau is named. The first block length every longer one agrees
    with, within their uncertainties, was tried and read the error low: on
    AR(1) series its median was 0.81 to 0.86 of the true error and its worst
    half of it, since the long blocks' uncertainties are wide enough to agree
    with blocks still too short. The curve is given, to be read beside the
    error the package records, which is calibrated (statistics.summarise).
    """
    lengths, counts, errors, uncertainties = [], [], [], []
    means = values.astype(float)
    length = 1
    while means.size >= 4:
        nb = means.size
        error = float(np.sqrt(np.var(means, ddof=1) / nb))
        lengths.append(length)
        counts.append(nb)
        errors.append(error)
        uncertainties.append(error / math.sqrt(2.0 * (nb - 1)))
        pairs = nb // 2
        means = 0.5 * (means[0:2 * pairs:2] + means[1:2 * pairs:2])
        length *= 2
    return {
        "block_length": [int(b) for b in lengths],
        "n_blocks": [int(c) for c in counts],
        "standard_error": _plain_list(errors),
        "standard_error_uncertainty": _plain_list(uncertainties),
    }


def _autocorrelation(kept: np.ndarray, inefficiency: float,
                     interval: "float | None",
                     known: "np.ndarray | None" = None) -> dict[str, Any]:
    """``C(t)`` of the equilibrated part up to its first lag at or below
    zero, or ``n / 4``; and the integrated correlation time from the
    package's own inefficiency, ``tau_int = (g - 1) / 2``. ``known`` is the
    correlation already computed for it, as far as it goes."""
    n = kept.size
    cap = max(1, n // 4)
    correlation = np.ones(1)
    crossed = None
    if np.ptp(kept) > 0.0:
        lags = min(_FIRST_LAGS, cap + 1)
        while True:
            if known is not None and known.size >= min(lags, n):
                correlation = known
            else:
                correlation = _suffix_correlations(kept, [0], lags).get(
                    0, np.ones(1))
            below = np.flatnonzero(correlation[:cap + 1] <= 0.0)
            if below.size:
                crossed = int(below[0])
                correlation = correlation[:crossed + 1]
                break
            if correlation.size > cap or correlation.size >= n:
                correlation = correlation[:cap + 1]
                break
            lags = min(max(lags, correlation.size) * 16, cap + 1)
    tau = (float(inefficiency) - 1.0) / 2.0
    lag = np.arange(correlation.size)
    return {
        "lag_frames": [int(t) for t in lag],
        "lag_time": None if interval is None else _plain_list(lag * interval),
        "correlation": _plain_list(correlation),
        "zero_crossing_frames": crossed,
        "statistical_inefficiency": _plain(inefficiency),
        "tau_int_frames": _plain(tau),
        "tau_int_time": None if interval is None else _plain(tau * interval),
    }


def convergence_of(values: Any, times: Any = None) -> dict[str, Any]:
    """How a series converged: what the GUI's convergence view plots.

    Values that are not finite are left out first, with their times; frame
    numbers below count the finite values, as :func:`summarise`'s do.
    ``times``, where given, is one time per value in any unit; the frame
    interval is the median step between them, and every ``*_time`` key is
    in that unit. Without times those keys are ``None``.

    Returns a JSON-ready dict (plain floats and ints, NaN as ``None``):

    ``ok``, ``reason``
        ``ok`` is False, with ``reason`` and nothing else computed, where
        fewer than :data:`CONVERGENCE_FEWEST_VALUES` finite values remain.
    ``n_values``, ``n_not_finite``, ``frame_interval``
    ``equilibration``
        What :func:`summarise` (and so :func:`mean_record`) gives for the
        series: ``discard_frames`` and ``start_time`` (Chodera's start),
        ``statistical_inefficiency``, ``effective_samples``, ``mean``,
        ``standard_error`` (``None`` where withheld), ``standard_deviation``,
        ``degrees_of_freedom``, and ``withheld`` and ``refusal`` (the reason
        and its code) where it withholds.
    ``running_mean``
        The cumulative mean of the equilibrated part at ``frames`` (counted
        from its start, about :data:`RUNNING_POINTS` evenly spaced) and
        ``time``, with ``standard_error`` from the package's own estimator
        applied to each prefix: ``sd sqrt(g / n)``, ``g`` from
        :func:`_for_the_mean`, ``None`` where :func:`summarise` would
        withhold it (fewer than :data:`RESOLVED_SAMPLES` independent samples
        with ``g`` of 2 or more, or one sample or fewer).
    ``blocking``
        Flyvbjerg-Petersen block averaging of the equilibrated part; see
        :func:`_blocking` for the keys.
    ``autocorrelation``
        The normalised autocorrelation of the equilibrated part (FFT based,
        about its mean, divided by ``n var`` as the inefficiency uses it) at
        ``lag_frames`` and ``lag_time``, up to the first lag where it is at
        or below zero (``zero_crossing_frames``) or ``n / 4``; and
        ``tau_int_frames`` and ``tau_int_time`` from
        ``g = 1 + 2 tau_int``, ``g`` being ``equilibration``'s
        ``statistical_inefficiency``.
    ``histogram``
        ``equilibrated`` and ``discarded`` (``None`` where nothing was
        discarded), each ``edges`` and ``counts`` with its own
        Freedman-Diaconis bins, at most :data:`HISTOGRAM_BINS_AT_MOST`.

    The correlations are computed for many suffixes and prefixes in one
    pass over the series, so a million values with a correlation time of
    hundreds of frames take under a second. A series whose correlation has
    not decayed within 16384 frames falls back to one transform per
    suffix, and takes longer.

        Flyvbjerg, H.; Petersen, H. G. Error estimates on averages of
        correlated data. J. Chem. Phys. 1989, 91, 461-466.
    """
    raw = np.asarray(values, dtype=float).ravel()
    finite = np.isfinite(raw)
    clock = None
    if times is not None:
        clock = np.asarray(times, dtype=float).ravel()
        if clock.size != raw.size:
            from fastmdxplora.refusals import StudyError

            raise StudyError(
                f"{clock.size} times against {raw.size} values: a convergence "
                "view needs one time per value.", code="config.option.wrong_type")
        finite &= np.isfinite(clock)
        clock = clock[finite]
    series = raw[finite]
    n = int(series.size)
    record: dict[str, Any] = {
        "ok": False, "reason": None, "n_values": n,
        "n_not_finite": int(raw.size - n), "frame_interval": None,
    }
    if n < CONVERGENCE_FEWEST_VALUES:
        record["reason"] = (
            f"{n} finite value(s): convergence is not read from fewer than "
            f"{CONVERGENCE_FEWEST_VALUES}, since the equilibration detector "
            "needs that many and a running mean of fewer says nothing.")
        return record
    interval = (float(np.median(np.diff(clock))) if clock is not None and n > 1
                else None)
    record["frame_interval"] = _plain(interval)

    # (a) The start summarise chooses, from the same numbers.
    candidates = [int(s) for s in _equilibration_candidates(n)]
    correlations: dict[int, np.ndarray] = {}
    suffix = _suffix_inefficiencies(series, candidates, correlations)
    counted = [(s, suffix[s][0], float((n - s) / suffix[s][0]))
               for s in candidates if n - s >= 3]
    discard, g_raw, _most = _latest_within_tolerance(counted)
    kept = series[discard:]
    g_kept, pairs_kept = suffix[discard]
    g_whole, pairs_whole = suffix[0]
    equilibrated, why = _summary(
        series, discard, g_raw,
        _corrected_for_the_mean(g_kept, pairs_kept, kept.size)
        if kept.size >= 3 and np.ptp(kept) > 0 else _for_the_mean(kept),
        lambda: (_corrected_for_the_mean(g_whole, pairs_whole, n)
                 if np.ptp(series) > 0 else _for_the_mean(series)),
        MINIMUM_EFFECTIVE_SAMPLES)
    refusal = getattr(why, "refusal", None)
    record["equilibration"] = {
        "discard_frames": int(discard),
        "start_time": None if clock is None else _plain(clock[discard]),
        "statistical_inefficiency": _plain(equilibrated.inefficiency),
        "effective_samples": _plain(equilibrated.effective_samples),
        "mean": _plain(equilibrated.mean),
        "standard_error": _plain(equilibrated.standard_error),
        "standard_deviation": _plain(equilibrated.standard_deviation),
        "degrees_of_freedom": _plain(equilibrated.degrees_of_freedom),
        "withheld": None if why is None else str(why),
        "refusal": None if refusal is None else refusal.code,
    }

    # (b) The running mean of the equilibrated part, prefix by prefix. A
    # prefix of the series is a suffix of it reversed, with the same
    # correlation, so the suffix machinery serves.
    size = kept.size
    ends = sorted({int(round(j * size / RUNNING_POINTS))
                   for j in range(1, RUNNING_POINTS + 1)} - {0})
    reversed_kept = kept[::-1].copy()
    # Started at twice the lags the equilibrated part needed, which a
    # prefix of it seldom exceeds, so one pass usually decides them all.
    reach = 4 * pairs_kept + 4
    prefix = _suffix_inefficiencies(
        reversed_kept, [size - e for e in ends if e >= 3],
        first_lags=min(16 * _FIRST_LAGS, max(_FIRST_LAGS, 2 * reach)))
    # The whole run up to each point, for summarise's whole-run rule, in the
    # same one pass over the series reversed.
    whole_prefix = (_suffix_inefficiencies(
        series[::-1].copy(), [n - (discard + e) for e in ends if e >= 3],
        first_lags=min(16 * _FIRST_LAGS, max(_FIRST_LAGS, 2 * (4 * pairs_whole + 4))))
        if discard else {})
    total = np.concatenate(([0.0], np.cumsum(kept)))
    running_means, running_errors = [], []
    for end in ends:
        mean = total[end] / end
        running_means.append(mean)
        if end == size:
            # The whole equilibrated part: the record itself.
            running_errors.append(_plain(equilibrated.standard_error))
            continue
        if end < 3:
            running_errors.append(None)
            continue
        # What summarise would have recorded had the run ended here, from
        # the same start: its own rule (_summary), the whole run's error
        # substituted where the start gained too little, withheld where it
        # would withhold. An error by a rule of the view's own gave a band
        # where the record gave none (73 of 200 correlated runs).
        g_prefix, pairs = prefix[size - end]
        piece = kept[:end]
        for_kept = (_corrected_for_the_mean(g_prefix, pairs, end)
                    if g_prefix < end and np.ptp(piece) > 0 else _for_the_mean(piece))
        def whole(end: int = end) -> tuple[float, float]:
            run = series[:discard + end]
            g_run, pairs_run = whole_prefix.get(n - (discard + end), (float(run.size), 0))
            if g_run < run.size and np.ptp(run) > 0:
                return _corrected_for_the_mean(g_run, pairs_run, run.size)
            return _for_the_mean(run)

        ended, _why = _summary(series[:discard + end], discard, g_prefix, for_kept,
                               whole, MINIMUM_EFFECTIVE_SAMPLES)
        running_errors.append(_plain(ended.standard_error))
    record["running_mean"] = {
        "frames": [int(e) for e in ends],
        "time": (None if clock is None
                 else _plain_list(clock[discard + np.asarray(ends) - 1])),
        "mean": _plain_list(running_means),
        "standard_error": [_plain(e) for e in running_errors],
    }

    # (c) Block averaging.
    record["blocking"] = _blocking(kept)

    # (d) The autocorrelation, and the correlation time the package uses.
    record["autocorrelation"] = _autocorrelation(
        kept, equilibrated.inefficiency, interval, correlations.get(discard))

    # (e) What the equilibrated values and the discarded ones look like.
    record["histogram"] = {
        "equilibrated": _histogram(kept),
        "discarded": _histogram(series[:discard]) if discard else None,
    }
    record["ok"] = True
    return record
