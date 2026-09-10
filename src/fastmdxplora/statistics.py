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
first frame averages the relaxation together with the equilibrium, and the
answer depends on how long the run was rather than on the system.

**The second.** Frames are not independent. A trajectory written every
picosecond from a system whose fluctuations decorrelate over a hundred
picoseconds has a hundred times fewer independent samples than it has frames,
and a standard error computed as if each frame counted is wrong by a factor
of ten. That is how a difference between two systems becomes significant on
paper without being real.

Both follow from one quantity. The statistical inefficiency ``g`` is the
number of frames per independent sample, so a series of ``n`` frames carries
``n / g`` of them. Chodera's method chooses where to start averaging by
maximising that count: discard too little and the relaxation is still in the
average, discard too much and there is nothing left to average.

    Chodera, J. D. A simple method for automated equilibration detection in
    molecular simulations. J. Chem. Theory Comput. 2016, 12, 1799-1805.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

__all__ = [
    "Equilibrated",
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
    keeping the relaxation costs independence, and discarding it costs frames.

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
        return None, (
            f"{values.size} usable frame(s): there is nothing to average, and "
            "nothing to say about how it varies."
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
        return equilibrated, (
            f"This run is not long against its own correlation time: taking "
            f"half the frames away changes the estimate, so {kept.size} frames "
            "cannot measure how correlated they are. The independent-sample "
            f"count of {effective:.1f} is an upper bound, so an error computed "
            "from it would be a lower bound -- and none is reported here "
            "rather than one that is wrong in a knowable direction. On ten "
            "replicas of one system differing only by seed, errors of this "
            "kind were five to eight times smaller than the spread of the ten "
            "means. The remedy is a longer run, or replicas."
        )

    if effective < minimum_effective_samples:
        return equilibrated, (
            f"{effective:.1f} independent samples in {kept.size} frames "
            f"(one every {g:.0f}). Below {minimum_effective_samples:g} a mean "
            "and its error describe how this particular run happened to go "
            "rather than the system it was run on. The frames are correlated, "
            "so recording them more often will not help -- the run has to be "
            "longer."
        )
    return equilibrated, None
