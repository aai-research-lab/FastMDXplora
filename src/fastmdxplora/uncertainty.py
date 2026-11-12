"""An error bar on a quantity that is not a mean.

`statistics.py` gives an honest standard error for an average over a
trajectory: it measures the statistical inefficiency, divides the sample
count by it, and refuses where the series is too short to measure its own
correlation time. That covers RMSD, radius of gyration, an occupancy --
anything that is the mean of a per-frame number.

It does not cover a free energy. A PMF comes out of WHAM's self-consistent
solution over every window at once; a metadynamics surface is a sum over
deposited hills; a reweighted average divides by a partition function
estimated from the same samples. None of them is a mean of anything, so
none of them can be handed to `summarise`, and the package accordingly
reported no uncertainty on any free energy at all -- no bootstrap, no
block averaging, no statistical inefficiency anywhere in the
enhanced-sampling code. `reweighted_std` moved 3% for a 37-fold drop in
effective sample size while the report printed it as a `±`.

The general tool for this is a block bootstrap: resample the underlying
samples in contiguous blocks, recompute the derived quantity from each
resample, and read the spread. Contiguous, because the samples are
correlated and resampling single frames would treat 100 correlated frames
as 100 independent ones and return an error bar too small by the square
root of the correlation time -- the same optimism this module exists to
remove.

Three decisions are worth stating because they are the ones that make the
number honest rather than merely present:

**The block length is computed, not chosen.** It comes from the
statistical inefficiency of the series itself, at
``ceil(BLOCKS_PER_CORRELATION_TIME * g)``: g is frames per independent
sample, so a block of a few g carries the correlation, and blocks that
long are near enough independent to shuffle.

**The spread is corrected for what blocks of that length miss.** A
block bootstrap's variance runs low on correlated series, by two terms:
the correlation that reaches across the cut between blocks, about
``g / (2 b)`` of it for blocks of ``b`` frames, and the resamples'
centring on the run's own mean, ``b / n``. At blocks of 2g, the rule
before, the two put a correlated series' standard error 11% to 13% low,
and its 95% interval held the answer 89% to 93% of the time (AR(1)
series, :data:`BLOCKS_PER_CORRELATION_TIME` gives the counts).
Longer blocks shrink the first and grow the second, and no block length
removes both, so each is undone instead: the spread is widened by
:func:`spread_factor`.

**Where the correlation time cannot be measured, this says so instead of
returning a number.** A series shorter than its own correlation time gives
a g that is a lower bound, so the bootstrap built on it is optimistic by
an unknown factor. That is exactly the case the calibration experiment
(V5) is pre-registered to expose, and printing a tight error bar there
would be the defect rather than the fix. `resolved` is False and `note`
says why; the caller decides whether to report the value without a bar or
to sample longer.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

import numpy as np

from fastmdxplora.statistics import correlation_is_resolved, statistical_inefficiency
from fastmdxplora.refusals import StudyError

__all__ = [
    "Bootstrap",
    "block_length_for",
    "block_bootstrap",
    "paired_block_bootstrap",
    "spread_factor",
    "DEFAULT_RESAMPLES",
]

#: Resamples taken unless the caller says otherwise. Two hundred determines a
#: standard error to a few percent, which is finer than the quantity it
#: describes; the count is exposed so a converged-enough answer can be had
#: cheaply and a publication figure can afford more.
DEFAULT_RESAMPLES = 200

#: Blocks per correlation time: a block is ``ceil(3 g)`` frames. Shorter
#: blocks break correlations that are really there and shrink the error
#: bar; longer ones leave fewer blocks, whose spread is noisier. What blocks
#: of either length miss, :func:`spread_factor` undoes. Counted on AR(1)
#: series (rho 0.5, 0.9 and 0.98, 3,000 to 30,000 frames, 1,000 to 1,500
#: series each, 200 resamples, t on 199 degrees of freedom), the 95%
#: interval on the mean held the answer 92.6% to 95.7% of the time at 3g
#: with the factor, and 88.9% to 93.4% at 2g without it (the rule before).
#: The low end is 3,000 frames at rho 0.98 (30 correlation times, 10 to 12
#: blocks): 93.5% +/- 0.6 and 93.2% +/- 0.5 on two runs of this harness,
#: 92.6% +/- 0.5 on a second harness, the shortfall the noise of an error
#: read off so few blocks. 2g to 5g with the factor were within a point of
#: each other except where the blocks were few: there 93.5% at 3g, 92.7%
#: at 4g and 91.6% at 5g. Where the correlation has a
#: slow tail (half of it at g = 66 under a fast decay), 3g gave 94.6% on
#: 10,000 frames and 91.9% on 3,000.
BLOCKS_PER_CORRELATION_TIME = 3

#: Below this ratio of effective to actual samples, a resampling error bar
#: on a *weighted* average stops being an estimate and becomes a floor.
#:
#: Measured, not assumed. Against the spread of the estimator over 200
#: independent realisations -- correlated values, lognormal weights, 3,000
#: frames -- the block bootstrap returned this fraction of the true
#: run-to-run spread:
#:
#:     ESS/n   1.00   0.78   0.39   0.15   0.056   0.012
#:     ratio   0.92   0.92   0.91   0.85   0.66    0.36
#:
#: It holds to within about 15% while a tenth of the frames still carry the
#: estimate, and falls away below that. The reason is structural rather
#: than fixable: resampling frames from one run cannot see how much the
#: *weights themselves* would differ in another run, and when a handful of
#: frames carry the average that variation is most of the answer.
#: Independent replicas are the honest route there, which is what the
#: calibration experiment exists to establish.
#:
#: That table is from blocks of 2g with no widening (:func:`spread_factor`).
#: Recounted on a harness of the same description (AR(1) values at rho 0.9,
#: weights ``exp(sigma z)`` of the same series, 3,000 frames, 200
#: realisations), blocks of 3g with the widening raised the ratio from 0.82
#: to 0.96 at ESS/n 1.0, 0.85 to 0.96 at 0.19 and 0.73 to 0.81 at 0.033: the
#: fall below a tenth is the weights', and stands.
WEIGHT_ESS_FLOOR = 0.10


@dataclass(frozen=True)
class Bootstrap:
    """A value, its uncertainty, and whether that uncertainty means anything.

    `value` is the statistic computed on the real data, not the mean of the
    resamples: the resamples estimate the *spread*, and using their mean as
    the answer would add a bias the bootstrap was not asked to introduce.
    """

    value: np.ndarray
    standard_error: np.ndarray
    low: np.ndarray
    high: np.ndarray
    resamples: int
    block_length: int
    resolved: bool
    note: str | None = None
    #: What the resamples' spread was widened by (:func:`spread_factor`).
    spread_factor: float = 1.0

    def as_dict(self) -> dict[str, Any]:
        """A plain-data form for a manifest or a report."""
        def out(a: np.ndarray) -> Any:
            arr = np.asarray(a, dtype=float)
            return float(arr) if arr.ndim == 0 else [float(x) for x in arr.ravel()]

        return {
            "value": out(self.value),
            "standard_error": out(self.standard_error),
            "confidence_low": out(self.low),
            "confidence_high": out(self.high),
            "resamples": int(self.resamples),
            "block_length": int(self.block_length),
            "correlation_resolved": bool(self.resolved),
            "note": self.note,
            "spread_factor": float(self.spread_factor),
        }


def _inefficiency_of(series: np.ndarray) -> tuple[float, int]:
    """``g`` of the finite values, at least one, and how many there are."""
    values = np.asarray(series, dtype=float).ravel()
    values = values[np.isfinite(values)]
    if values.size < 2:
        return 1.0, int(values.size)
    g = statistical_inefficiency(values)
    if not np.isfinite(g) or g < 1.0:
        g = 1.0
    return float(g), int(values.size)


def block_length_for(series: np.ndarray) -> int:
    """Frames per bootstrap block, from the series' own correlation time:
    ``ceil(BLOCKS_PER_CORRELATION_TIME * g)``, with ``g`` read from the
    finite values. One NaN used to make ``g`` read 1 and the block 2 frames
    on a series whose ``g`` was 39.
    """
    return _block(*_inefficiency_of(series))


def _block(g: float, n: int) -> int:
    """``ceil(BLOCKS_PER_CORRELATION_TIME * g)``, within 1 and ``n``."""
    if n < 2:
        return 1
    return max(1, min(n, int(np.ceil(BLOCKS_PER_CORRELATION_TIME * g))))


def spread_factor(frames: int, block: int, inefficiency: float) -> float:
    """What a moving-block bootstrap's standard error on ``frames``
    correlated frames, in blocks of ``block``, is multiplied by to be the
    standard error of the series' mean: ``1 / sqrt(A - b / n)``.

    ``b / n`` is the centring: each block's mean is read against the run's
    own mean, which has followed the run's slow fluctuations, and that
    takes ``b / n`` of the long-run variance off (the overlapping-batch-means
    correction). It comes off the long-run variance, not off what the
    blocks hold, so it is subtracted from ``A`` rather than dividing by
    ``1 - b / n``: the product form, used first, was 1% low at 10 blocks
    and 3% low at 5 against the package's resampler's exact expectation on
    AR(1) series. ``A`` is the share of the variance a block of ``b`` holds
    when the correlation decays as one exponential, ``r^t`` with ``r = (g -
    1) / (g + 1)``, the decay with the same ``g``: ``1 - 2 r (1 - r^b) /
    ((1 - r)^2 b g)``. About ``1 - 1 / (2k)`` for blocks of ``k g``, so the
    factor is 1.15 at blocks of 2g and 1.10 at 3g, and exactly ``sqrt(g)``
    for blocks of one frame on a long series. ``sqrt(n / (n - b))`` for
    independent frames, and one where the block is the whole series (there
    is no spread to widen). The denominator is held at no less than
    ``(1 - b / n) / g``, what blocks of single frames would give.

    Computed against AR(1) series (rho 0.5, 0.9 and 0.98, 3,000 to 30,000
    frames, 1,000 to 1,500 series each): with it the standard error of the
    mean came within 2% of the exact one at blocks of 2g to 8g wherever
    there were 25 blocks or more, and 3% low on 3,000 frames at rho 0.98
    (10 to 17 blocks); without it, it was 11% to 13% low at 2g and still
    3% to 5% low at 8g. For the fraction of frames below a
    cutoff, whose correlation decays faster than the series', it came
    1% to 3% high. Where the correlation has a slow tail beside a fast
    decay, ``A`` is too large and the error is still low (3% to 5% at 3g
    with half of it at g = 66 under a fast decay); where it stops dead (a
    boxcar), it is high (4% at 3g).
    """
    n, b, g = int(frames), int(block), float(inefficiency)
    if n < 2 or b >= n or b < 1:
        return 1.0
    centred = b / n
    if not np.isfinite(g) or g <= 1.0:
        return float(1.0 / np.sqrt(1.0 - centred))
    r = (g - 1.0) / (g + 1.0)
    held = 1.0 - 2.0 * r * (1.0 - r ** b) / ((1.0 - r) ** 2 * b * g)
    # A block of one frame holds 1/g of it, and a longer block more.
    held = max(held, 1.0 / g)
    return float(1.0 / np.sqrt(max(held - centred, (1.0 - centred) / g)))


def _resample_one(values: np.ndarray, block: int, rng: np.random.Generator) -> np.ndarray:
    """One moving-block resample, the same length as the original.

    Overlapping blocks rather than a fixed partition: a series holding only
    a few non-overlapping blocks has very few distinct resamples to draw
    from, and the bootstrap then reports the coarseness of its own
    partition rather than the spread of the data.
    """
    n = values.size
    if block >= n:
        return values.copy()
    starts = rng.integers(0, n - block + 1, size=int(np.ceil(n / block)))
    return np.concatenate([values[s:s + block] for s in starts])[:n]


def block_bootstrap(
    samples: Mapping[Any, np.ndarray] | Sequence[np.ndarray] | np.ndarray,
    statistic: Callable[[Any], Any],
    *,
    resamples: int = DEFAULT_RESAMPLES,
    block_length: int | None = None,
    seed: int | None = 0,
    confidence: float = 0.95,
) -> Bootstrap:
    """The spread of `statistic` under resampling of correlated samples.

    `samples` is whatever `statistic` consumes: one series, a sequence of
    them, or a mapping of key to series -- an umbrella campaign's windows,
    say. Each series is resampled independently, because separate windows
    are separate simulations and share no frames.

    `statistic` is called once on the real data and once per resample. It
    may return a scalar or an array; an array is treated element-wise, so a
    PMF comes back with an error bar per bin.
    """
    rng = np.random.default_rng(seed)

    if isinstance(samples, Mapping):
        keys = list(samples)
        series = [np.asarray(samples[k], dtype=float).ravel() for k in keys]
        rebuild = lambda drawn: dict(zip(keys, drawn))  # noqa: E731
    elif isinstance(samples, np.ndarray) and samples.ndim == 1:
        series = [np.asarray(samples, dtype=float)]
        rebuild = lambda drawn: drawn[0]  # noqa: E731
    else:
        series = [np.asarray(s, dtype=float).ravel() for s in samples]
        rebuild = list  # type: ignore[assignment]

    if not series or all(s.size == 0 for s in series):
        raise StudyError("block_bootstrap needs at least one non-empty series", code="analysis.sampling.too_few_frames")

    read = [_inefficiency_of(s) for s in series]
    blocks = [block_length or _block(g, n) for g, n in read]
    # One factor for the whole statistic, the largest the series need: the
    # resamples of a statistic of several series cannot be widened series
    # by series, and an error read low is the one this module exists to
    # remove.
    factor = max((spread_factor(s.size, b, g)
                  for s, (g, _n), b in zip(series, read, blocks)), default=1.0)
    resolved = all(correlation_is_resolved(s) for s in series if s.size > 1)
    note = None
    if not resolved:
        note = (
            "At least one series is shorter than its own correlation time, "
            "so the block length is a lower bound and this error bar is "
            "optimistic by an unknown factor. Report it as a floor, or "
            "sample longer."
        )

    value = np.asarray(statistic(rebuild(series)), dtype=float)
    drawn = np.empty((int(resamples),) + value.shape, dtype=float)
    for i in range(int(resamples)):
        shuffled = [_resample_one(s, b, rng) for s, b in zip(series, blocks)]
        drawn[i] = np.asarray(statistic(rebuild(shuffled)), dtype=float)

    error, low, high = _spread(drawn, factor, confidence)
    return Bootstrap(
        value=value,
        standard_error=error,
        low=low,
        high=high,
        resamples=int(resamples),
        block_length=int(max(blocks)),
        resolved=bool(resolved),
        note=note,
        spread_factor=float(factor),
    )


def _spread(resampled: np.ndarray, factor: float, confidence: float
            ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The standard error and percentiles of the resamples, each resample's
    departure from their mean widened by `factor`."""
    tail = (1.0 - float(confidence)) / 2.0
    # A bin no window sampled is NaN in every resample. That is the honest
    # answer for it -- there is no free energy there to put an error on --
    # and numpy's warning about an all-NaN slice would report it once per
    # bin per call, burying the run's own output.
    with np.errstate(invalid="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        # ddof=1: the resamples are a sample of the sampling distribution,
        # not the whole of it.
        error = np.nanstd(resampled, axis=0, ddof=1)
        low = np.nanpercentile(resampled, 100.0 * tail, axis=0)
        high = np.nanpercentile(resampled, 100.0 * (1.0 - tail), axis=0)
        if factor != 1.0:
            # Percentiles of the widened resamples, which are the widened
            # percentiles: widening about the mean keeps their order.
            middle = np.nanmean(resampled, axis=0)
            error = error * factor
            low = middle + factor * (low - middle)
            high = middle + factor * (high - middle)
    return (np.asarray(error, dtype=float), np.asarray(low, dtype=float),
            np.asarray(high, dtype=float))


def paired_block_bootstrap(
    arrays: "Sequence[np.ndarray]",
    statistic: Callable[..., Any],
    *,
    resamples: int = DEFAULT_RESAMPLES,
    block_length: int | None = None,
    seed: int | None = 0,
    confidence: float = 0.95,
    inefficiency: float | None = None,
) -> Bootstrap:
    """One index sequence, applied to every array, so paired quantities stay
    paired.

    A reweighted average is a value and a weight per frame, and the pairing
    is the whole content of the estimator: resampling the two independently
    would put one frame's value with another frame's weight, which is not a
    resample of anything. The same holds for a coordinate and its bias, or
    a distance and the frame time it was measured at.

    The block length is taken from the first array, which is by convention
    the quantity being averaged; a weight series inherits its correlation
    from the trajectory that produced it, so measuring g on the values is
    the right reading. `inefficiency` gives ``g`` instead, where the
    caller has a better one (a reweighted mean takes the slower of the
    quantity's and the collective variable's); the block and
    :func:`spread_factor` are both read from it.
    """
    series = [np.asarray(a, dtype=float).ravel() for a in arrays]
    if not series:
        raise StudyError("paired_block_bootstrap needs at least one array", code="analysis.sampling.too_few_frames")
    n = series[0].size
    if any(a.size != n for a in series):
        raise StudyError(
            "paired arrays must be the same length; got "
            + ", ".join(str(a.size) for a in series), code="config.option.wrong_type")
    if n == 0:
        raise StudyError("paired_block_bootstrap needs non-empty arrays", code="analysis.sampling.too_few_frames")

    rng = np.random.default_rng(seed)
    if inefficiency is None:
        g = _inefficiency_of(series[0])[0]
    else:
        try:
            g = float(inefficiency)
        except (TypeError, ValueError):
            g = float("nan")
        if not np.isfinite(g) or g <= 0.0:
            raise StudyError(
                f"`inefficiency` is frames per independent sample, a finite "
                f"positive number; got {inefficiency!r:.40}.",
                code="config.option.wrong_type")
        g = max(1.0, g)
    block = block_length or _block(g, _inefficiency_of(series[0])[1])
    factor = spread_factor(n, block, g)
    resolved = correlation_is_resolved(series[0]) if n > 1 else False
    note = None
    if not resolved:
        note = (
            "The series is shorter than its own correlation time, so the "
            "block length is a lower bound and this error bar is optimistic "
            "by an unknown factor. Report it as a floor, or sample longer."
        )

    value = np.asarray(statistic(*series), dtype=float)
    drawn = np.empty((int(resamples),) + value.shape, dtype=float)
    index = np.arange(n)
    for i in range(int(resamples)):
        if block >= n:
            picked = index
        else:
            starts = rng.integers(0, n - block + 1,
                                  size=int(np.ceil(n / block)))
            picked = np.concatenate(
                [index[s:s + block] for s in starts])[:n]
        drawn[i] = np.asarray(statistic(*[a[picked] for a in series]),
                              dtype=float)

    error, low, high = _spread(drawn, factor, confidence)
    return Bootstrap(
        value=value, standard_error=error, low=low, high=high,
        resamples=int(resamples), block_length=int(block),
        resolved=bool(resolved), note=note, spread_factor=float(factor),
    )
