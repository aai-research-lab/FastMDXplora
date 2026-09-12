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
    "statistical_inefficiency",
    "correlation_is_resolved",
    "detect_equilibration",
    "summarise",
]

#: Below this many independent samples, a mean and its error describe the
#: run's accidents rather than the system. Ten is not generous -- it is the
#: point below which an error bar stops meaning anything at all -- and it is
#: a judgement, so a study can set its own.
MINIMUM_EFFECTIVE_SAMPLES = 10.0


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

    def as_record(self) -> dict[str, Any]:
        return {
            "discard": self.discard,
            "statistical_inefficiency": self.inefficiency,
            "effective_samples": self.effective_samples,
            "mean": self.mean,
            "standard_error": self.standard_error,
            "standard_deviation": self.standard_deviation,
        }


#: The name this carried before it was matched to the method it implements.
#: Kept as an alias rather than deleted, because a rename that breaks an
#: import teaches nothing and costs a user an afternoon.
Settled = Equilibrated


def statistical_inefficiency(series: np.ndarray) -> float:
    """Frames per independent sample: ``g = 1 + 2 sum (1 - t/n) C(t)``.

    ``C`` is the normalised fluctuation autocorrelation. The sum is truncated
    at the first non-positive ``C``, which is the standard convention: past
    that point the estimates are noise, and summing them adds variance rather
    than information.

    A constant series has no fluctuations to correlate, so ``g`` is one: every
    frame agrees, and there is nothing for a correlation time to describe.
    """
    values = np.asarray(series, dtype=float)
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


def _inefficiency_and_reach(
    fluctuation: np.ndarray, variance: float, n: int
) -> tuple[float, bool]:
    """The inefficiency, and whether the correlation decayed within the run.

    The sum truncates at the first non-positive correlation. When it instead
    runs out of series, the correlation was still positive at the longest lag
    the run can measure, and everything past that is missing from the sum: the
    inefficiency comes back too small and the independent-sample count too
    large. On a series of 4000 frames with a true inefficiency of 2000, the
    estimate was 361 -- and 11 apparent independent samples cleared a
    threshold of 10.

    So the second value is not a detail. It is the difference between a
    measurement and a lower bound, and a run in that state is too short to
    say how short it is.
    """
    total = 0.0
    decayed = False
    for lag in range(1, n - 1):
        correlation = float(
            np.mean(fluctuation[: n - lag] * fluctuation[lag:]) / variance)
        if correlation <= 0.0:
            decayed = True
            break
        total += (1.0 - lag / n) * correlation

    return max(1.0, 1.0 + 2.0 * total), decayed


#: How far the inefficiency may move when the series is halved before the
#: correlation is taken as unresolved. Well-resolved series move by under a
#: tenth; a series shorter than a few times its own correlation time moves by
#: half, and its inefficiency is low by a factor of five.
RESOLVED_RATIO = 1.15


def correlation_is_resolved(series: np.ndarray) -> bool:
    """Whether the series is long enough to measure its own correlation time.

    Checked by halving it: an inefficiency the series can resolve does not
    change much when half the frames are taken away, and one it cannot moves
    a great deal. On an AR(1) series with a true inefficiency of 2000, four
    thousand frames gave 361 -- which is not an error to tolerate but a number
    with the wrong meaning, since the independent-sample count built from it
    said eleven when the truth was two.

    The obvious check does not work and it is worth saying why. A sample
    autocorrelation sums to roughly -1/2 whatever the series, so it goes
    negative on its own: on that series it crossed zero at lag 334 while the
    real correlation there was still 0.7. Asking where the correlation decayed
    answers a question about the estimator rather than the run.
    """
    values = np.asarray(series, dtype=float)
    whole = statistical_inefficiency(values)

    # Nothing to resolve. Frames this close to independent have no correlation
    # time for a longer run to pin down, and putting a short uncorrelated
    # series through a halving comparison only measures the noise in the
    # comparison -- discarding a single frame flipped the verdict on
    # twenty-five.
    if whole < 2.0:
        return True

    # There is a correlation time, and a series this short cannot measure it
    # whatever the halves happen to say.
    if values.size < 50:
        return False

    half = statistical_inefficiency(values[: values.size // 2])
    if half <= 0:
        return False
    return (whole / half) <= RESOLVED_RATIO


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
    """
    values = np.asarray(series, dtype=float)
    n = values.size
    if n < 10:
        return 0, 1.0, float(n)

    # Never past two thirds: an answer resting on the last third of a run is
    # not an answer about the run.
    candidates = np.unique(
        np.linspace(0, int(n * 2 / 3), num=min(steps, n), dtype=int))

    best = (0, 1.0, 0.0)
    for start in candidates:
        remaining = values[start:]
        if remaining.size < 3:
            continue
        g = statistical_inefficiency(remaining)
        effective = remaining.size / g
        if effective > best[2]:
            best = (int(start), g, float(effective))
    return best


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


def summarise(
    series: np.ndarray,
    *,
    minimum_effective_samples: float = MINIMUM_EFFECTIVE_SAMPLES,
) -> tuple[Equilibrated | None, str | None]:
    """A mean with an honest error on it, or a reason there is not one.

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

    discard, g, effective = detect_equilibration(values)
    kept = values[discard:]
    resolved = correlation_is_resolved(kept)

    equilibrated = Equilibrated(
        discard=discard,
        inefficiency=g,
        effective_samples=effective,
        mean=float(np.mean(kept)),
        # Withheld where the correlation is unresolved, rather than printed
        # beside a warning that it cannot be trusted. An effective-sample
        # count that is an upper bound makes an error computed from it a
        # lower bound, and a number wrong in a knowable direction is worse
        # than no number: the caveat is read once and the figure is used
        # thereafter. Measured on ten replicas of one system differing only
        # by integrator seed, errors computed this way came out five to eight
        # times smaller than the spread of the ten means.
        standard_error=float(np.std(kept, ddof=1) / np.sqrt(effective))
        if (effective > 1 and resolved) else float("nan"),
        standard_deviation=float(np.std(kept, ddof=1)),
    )

    if not resolved:
        return equilibrated, Withholding(
            f"This run is not long against its own correlation time: taking "
            f"half the frames away changes the estimate, so {kept.size} frames "
            "cannot measure how correlated they are. The independent-sample "
            f"count of {effective:.1f} is an upper bound, so an error computed "
            "from it would be a lower bound -- and none is reported here "
            "rather than one that is wrong in a knowable direction. On ten "
            "replicas of one system differing only by seed, errors of this "
            "kind were five to eight times smaller than the spread of the ten "
            "means. The remedy is a longer run, or replicas.",
            code="analysis.sampling.correlation_unresolved",
            frames=int(kept.size), independent=float(effective),
            statistical_inefficiency=float(g),
        )

    if effective < minimum_effective_samples:
        return equilibrated, Withholding(
            f"{effective:.1f} independent samples in {kept.size} frames "
            f"(one every {g:.0f}). Below {minimum_effective_samples:g} a mean "
            "and its error describe how this particular run happened to go "
            "rather than the system it was run on. The frames are correlated, "
            "so recording them more often will not help -- the run has to be "
            "longer.",
            code="analysis.sampling.too_few_independent",
            independent=float(effective), frames=int(kept.size),
            statistical_inefficiency=float(g),
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
        return record

    def __str__(self) -> str:
        if self.met:
            return (f"{self.have:.1f} independent samples, which meets the "
                    f"{self.target:g} asked for.")
        duration = ("" if self.more_ns is None
                    else f", about {self.more_ns:.3g} ns more")
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
    return Shortfall(
        target=float(target_independent),
        have=float(effective),
        inefficiency=float(g),
        more_frames=more,
        more_ns=(None if frame_interval_ns is None
                 else float(more * frame_interval_ns)),
    )


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
    #: How unusual the *ordering* of the segment means is. Low means they
    #: climb or fall rather than scatter.
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
            # Said plainly, because a reader comparing this against a run
            # that went through in one piece should know they are not the
            # same kind of number.
            "pooled_across_joins": True,
            **({"qualified": self.qualification} if self.qualification
               else {}),
        }


def summarise_segments(
    series: np.ndarray,
    joins: "list[int] | tuple[int, ...]",
    *,
    minimum_effective_samples: float = MINIMUM_EFFECTIVE_SAMPLES,
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

    Returns
    -------
    (Pooled, None) or (None, Withholding)
        Withheld where no segment supported a mean, or where the pooled
        effective count falls short. Pooling does not rescue a run that
        was too short: ten segments of two independent samples each is
        twenty, and twenty is twenty however it was collected -- but ten
        segments that each support nothing support nothing together.
    """
    values = np.asarray(series, dtype=float)
    values = values[np.isfinite(values)]
    boundaries = sorted({int(j) for j in joins if 0 < int(j) < values.size})

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
    withheld: list[tuple[int, str]] = []
    for index, (start, stop) in enumerate(zip(edges, edges[1:])):
        # Each segment is equilibrated on its own. A segment that begins
        # after a join has its own approach to settle -- under a barostat
        # the move size is re-adapting -- and detecting that per segment is
        # the point, not an inconvenience.
        piece, why = summarise(
            values[start:stop],
            minimum_effective_samples=0.0)
        if piece is None:
            withheld.append((index, str(why)))
            continue
        pieces.append(piece)

    if not pieces:
        return None, Withholding(
            "No segment of this joined run supports a mean. Pooling does "
            "not rescue a run that was too short: segments that each say "
            "nothing say nothing together.",
            code="analysis.sampling.too_few_independent",
            independent=0.0, frames=int(values.size),
            needed=float(minimum_effective_samples),
        )

    weights = np.array([p.effective_samples for p in pieces], dtype=float)
    means = np.array([p.mean for p in pieces], dtype=float)
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

    # Before reporting it: do these segments agree that they are measuring
    # one thing? Pooling assumes they do, and pooling estimates of a moving
    # target gives a confident number for a quantity that does not exist.
    precisions = np.array(
        [1.0 / max(p.standard_error ** 2, 1e-300) for p in pieces])
    scatter = heterogeneity_ratio(means, precisions)
    drifting = drift_across_segments(means, precisions)

    if drifting < DRIFT_SIGNIFICANT_BELOW and scatter > 1.0:
        # Both conditions, because either alone is not drift. A low p on
        # segments that agree is a trend of nothing, and scatter with no
        # order is underestimated error rather than movement.
        span = float(means[-1] - means[0])
        return None, Withholding(
            f"The segment means move in order across the run, by {span:+.4g} "
            f"from first to last, and an ordering this clean arises by "
            f"chance about {drifting:.1%} of the time. The system had not "
            "settled at the scale of the whole run, so a pooled mean would "
            "be the mean of a moving target with a confident error bar on "
            "it. The remedy is a longer run, not more pooling.",
            code="analysis.sampling.drifting",
            drift_p=float(drifting), heterogeneity=float(scatter),
            span=span, segments=len(pieces),
        )

    qualification = ""
    if scatter > HETEROGENEITY_QUALIFY_ABOVE:
        qualification = (
            f"The segment means scatter {scatter:.1f} times more than their "
            "own standard errors predict, in no particular order. That is "
            "not drift -- it says the per-segment errors are too small, "
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


#: Observed scatter of segment means over what their own standard errors
#: predict. One means they agree. Above this they do not, which says the
#: per-segment errors are too small -- the usual cause being correlation
#: the inefficiency did not fully capture.
HETEROGENEITY_QUALIFY_ABOVE = 2.0

#: How unusual the ordering of the segment means has to look before it is
#: called drift rather than scatter.
DRIFT_SIGNIFICANT_BELOW = 0.05


def _weighted_slope(means: np.ndarray, weights: np.ndarray,
                    positions: np.ndarray) -> float:
    """Weighted least-squares slope of segment mean against segment order."""
    total = weights.sum()
    mean_x = float((weights * positions).sum() / total)
    mean_y = float((weights * means).sum() / total)
    spread = float((weights * (positions - mean_x) ** 2).sum())
    if spread <= 0:
        return 0.0
    return float((weights * (positions - mean_x) * (means - mean_y)).sum()
                 / spread)


def drift_across_segments(means: np.ndarray, weights: np.ndarray, *,
                          permutations: int = 4999,
                          seed: int = 0) -> float:
    """How unusual the ordering of these segment means is, as a p-value.

    Scatter and drift look the same in a list of numbers and mean
    different things. Segment means that disagree but in no order are
    saying the per-segment errors are too small. Segment means that climb
    are saying the system was still moving, and then a pooled mean is the
    mean of a moving target with a confident error bar on it -- the worst
    of the three outcomes, because it is the one that looks most like a
    measurement.

    Tested by permuting the order of the segments rather than by assuming
    a distribution. The observed statistic is the weighted least-squares
    slope against segment index; the null is what that slope looks like
    when the same segment means are put in a random order. Exact for any
    number of segments, which matters because a run is often three or
    four, and a t or normal approximation on three points is a number
    rather than a test.

    Returns 1.0 for fewer than three segments: two points always lie on a
    line, and calling that a trend would refuse every two-segment run.
    """
    if means.size < 3:
        return 1.0

    positions = np.arange(means.size, dtype=float)
    observed = abs(_weighted_slope(means, weights, positions))

    rng = np.random.default_rng(seed)
    order = np.arange(means.size)
    at_least_as_extreme = 0
    for _ in range(permutations):
        shuffled = rng.permutation(order)
        candidate = abs(_weighted_slope(means[shuffled], weights[shuffled],
                                        positions))
        if candidate >= observed:
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
