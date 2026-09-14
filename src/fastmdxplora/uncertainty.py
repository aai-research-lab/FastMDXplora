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

Two decisions are worth stating because they are the ones that make the
number honest rather than merely present:

**The block length is measured, not chosen.** It comes from the
statistical inefficiency of the series itself, at ``ceil(2g)``: g is
frames per independent sample, so a block of 2g carries a whole
correlation time and a little more, and blocks that long are near enough
independent to shuffle.

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
    "DEFAULT_RESAMPLES",
]

#: Resamples taken unless the caller says otherwise. Two hundred settles a
#: standard error to a few percent, which is finer than the quantity it
#: describes; the count is exposed so a converged-enough answer can be had
#: cheaply and a publication figure can afford more.
DEFAULT_RESAMPLES = 200

#: Blocks per correlation time. A block of 2g spans a whole correlation
#: time with margin, which is the usual choice and the conservative one:
#: shorter blocks break correlations that are really there and shrink the
#: error bar.
BLOCKS_PER_CORRELATION_TIME = 2

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
        }


def block_length_for(series: np.ndarray) -> int:
    """Frames per bootstrap block, from the series' own correlation time."""
    values = np.asarray(series, dtype=float).ravel()
    if values.size < 2:
        return 1
    g = statistical_inefficiency(values)
    if not np.isfinite(g) or g < 1.0:
        g = 1.0
    return max(1, min(values.size, int(np.ceil(BLOCKS_PER_CORRELATION_TIME * g))))


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

    blocks = [block_length or block_length_for(s) for s in series]
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

    tail = (1.0 - float(confidence)) / 2.0
    # A bin no window sampled is NaN in every resample. That is the honest
    # answer for it -- there is no free energy there to put an error on --
    # and numpy's warning about an all-NaN slice would report it once per
    # bin per call, burying the run's own output.
    with np.errstate(invalid="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        # ddof=1: the resamples are a sample of the sampling distribution,
        # not the whole of it.
        error = np.nanstd(drawn, axis=0, ddof=1)
        low = np.nanpercentile(drawn, 100.0 * tail, axis=0)
        high = np.nanpercentile(drawn, 100.0 * (1.0 - tail), axis=0)

    return Bootstrap(
        value=value,
        standard_error=error,
        low=np.asarray(low, dtype=float),
        high=np.asarray(high, dtype=float),
        resamples=int(resamples),
        block_length=int(max(blocks)),
        resolved=bool(resolved),
        note=note,
    )


def paired_block_bootstrap(
    arrays: "Sequence[np.ndarray]",
    statistic: Callable[..., Any],
    *,
    resamples: int = DEFAULT_RESAMPLES,
    block_length: int | None = None,
    seed: int | None = 0,
    confidence: float = 0.95,
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
    the right reading.
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
    block = block_length or block_length_for(series[0])
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

    tail = (1.0 - float(confidence)) / 2.0
    with np.errstate(invalid="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        error = np.nanstd(drawn, axis=0, ddof=1)
        low = np.nanpercentile(drawn, 100.0 * tail, axis=0)
        high = np.nanpercentile(drawn, 100.0 * (1.0 - tail), axis=0)

    return Bootstrap(
        value=value, standard_error=error,
        low=np.asarray(low, dtype=float), high=np.asarray(high, dtype=float),
        resamples=int(resamples), block_length=int(block),
        resolved=bool(resolved), note=note,
    )
