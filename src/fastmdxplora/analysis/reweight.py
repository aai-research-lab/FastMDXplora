"""Recovering unbiased averages from a metadynamics trajectory.

A metadynamics run deliberately distorts the ensemble: that is how it escapes
minima. A state the bias filled early is visited more often than equilibrium
would give, and one filled late less. An average over the frames is therefore
an average over a distribution nobody wanted, and reporting it as a property
of the system is wrong in a way that looks entirely plausible.

The distortion is known, which is what makes this recoverable. Each frame was
sampled under a bias V(s) at its own value of the collective variable, so
weighting it by exp(V(s)/kT) undoes the tilt and the weighted average is the
unbiased one.

Two things make that statement less simple than it looks.

**The bias grows.** V is not one function but a sequence of them: the hills
deposited by frame ten are not the hills deposited by frame ten thousand.
Weighting every frame by the *final* bias is the common shortcut and it is
wrong early in a run, where the bias the frame actually experienced was much
smaller. This uses the bias as it stood when each frame was written, summing
only the hills deposited before it.

**The bias grows everywhere, not only where the system is.** Weighted by
exp(V/kT) alone, frames are ranked by when they were written, and a
well-tempered run's bias approaches -(1 - 1/gamma) F rather than -F. Tiwary
and Parrinello's time-dependent offset c(t) removes both, and PLUMED's stored
hill heights carry a factor gamma/(gamma - 1) that has to be undone first.
That is done in :mod:`fastmdxplora.analysis.reweighted_averages`, whose
``weights_for_run`` is the one way the package weights a run's frames: it
passes ``V - c(t)`` to :func:`weights_from_bias` here. This module holds the
arithmetic that does not depend on PLUMED's conventions. (A second
``weights_for_run`` here, with neither c(t) nor the gamma factor undone, read
P(x < 0) as 0.965 against an exact 0.893 on a well-tempered test run, and
nothing called it; it was removed.)

    Tiwary, P.; Parrinello, M. A time-independent free energy estimator for
    metadynamics. *J Phys Chem B* **2015**, 119, 736.
    Branduardi, D.; Bussi, G.; Parrinello, M. Metadynamics with adaptive
    Gaussians. *J Chem Theory Comput* **2012**, 8, 2247.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from fastmdxplora.refusals import StudyError

#: Boltzmann's constant in kJ/mol/K, as the rest of the package uses it.
KB_KJ_PER_MOL_K = 0.008314462618


@dataclass(frozen=True)
class Weights:
    """Per-frame weights, and what they can and cannot be trusted for."""

    values: np.ndarray
    """One weight per frame, normalised to sum to the frame count."""

    effective_sample_size: float
    """Weight-concentration effective frames: Kish's (sum w)^2 / sum w^2.

    A weighted mean over a thousand frames whose weight is concentrated in
    five of them is a mean over five, and quoting it as a thousand overstates
    it by a factor of fourteen. It counts frames as though each were
    independent, which frames of a trajectory are not: on a well-tempered
    metadynamics run with a bias factor of 8 it read 2445 of 6000 frames
    while the collective variable's statistical inefficiency was 91, so the
    average rested on about 27 independent samples. That count, this one
    divided by the inefficiency, is :func:`independent_samples`.
    """

    converged: bool
    """Whether the bias had stopped growing when the run ended.

    Weights from a surface still filling are approximately right rather than
    right, because the simple estimator assumes a converged bias.
    """

    note: str = ""

    @property
    def usable_fraction(self) -> float:
        """The effective sample size as a fraction of the frames."""
        if not len(self.values):
            return 0.0
        return self.effective_sample_size / len(self.values)


def bias_at_each_frame(
    hills_times_ps: np.ndarray,
    hills_centres: np.ndarray,
    hills_sigmas: np.ndarray,
    hills_heights: np.ndarray,
    frame_times_ps: np.ndarray,
    frame_values: np.ndarray,
) -> np.ndarray:
    """The bias each frame actually felt, from the hills laid down before it.

    Not the final bias applied to every frame. A frame written in the first
    picosecond was sampled under almost no bias, and weighting it as though
    it had felt the whole of a run's deposition inflates it by orders of
    magnitude -- which shows up as an effective sample size of one or two.
    """
    hills_times_ps = np.asarray(hills_times_ps, dtype=float)
    hills_centres = np.asarray(hills_centres, dtype=float)
    hills_sigmas = np.asarray(hills_sigmas, dtype=float)
    hills_heights = np.asarray(hills_heights, dtype=float)
    frame_times_ps = np.asarray(frame_times_ps, dtype=float)
    frame_values = np.asarray(frame_values, dtype=float)

    if not len(hills_times_ps) or not len(frame_times_ps):
        return np.zeros(len(frame_values), dtype=float)

    bias = np.empty(len(frame_values), dtype=float)
    for index, (when, where) in enumerate(zip(frame_times_ps, frame_values)):
        # Hills laid down at or before this frame. `searchsorted` on a
        # monotonic deposition time is exact and costs nothing.
        laid = int(np.searchsorted(hills_times_ps, when, side="right"))
        if laid == 0:
            bias[index] = 0.0
            continue
        offsets = where - hills_centres[:laid]
        widths = hills_sigmas[:laid]
        bias[index] = float(np.sum(
            hills_heights[:laid]
            * np.exp(-0.5 * (offsets / widths) ** 2)))
    return bias


def weights_from_bias(
    bias_kjmol: np.ndarray,
    *,
    temperature_K: float = 300.0,
    converged: bool = True,
) -> Weights:
    """Turn a per-frame bias into weights that undo it.

    Normalised by the largest bias before exponentiating, because exp of a
    hundred kilojoules over RT overflows a float and returns infinity for
    every frame -- from which the weighted mean is a nan and the effective
    sample size a nan, and nothing says why.
    """
    bias = np.asarray(bias_kjmol, dtype=float)
    if not len(bias):
        return Weights(np.asarray([]), 0.0, converged, "no frames")

    kT = KB_KJ_PER_MOL_K * float(temperature_K)
    exponent = (bias - float(np.max(bias))) / kT
    raw = np.exp(exponent)
    total = float(np.sum(raw))
    if total <= 0 or not np.isfinite(total):
        return Weights(
            np.ones(len(bias)), float(len(bias)), converged,
            "the bias could not be turned into weights, so the frames are "
            "counted equally -- which is the biased average, not the "
            "unbiased one")

    values = raw * (len(bias) / total)
    effective = float(np.sum(values) ** 2 / np.sum(values ** 2))
    note = ""
    if not converged:
        note = (
            "the bias had not converged when the run ended, so these weights "
            "are approximate: the simple estimator assumes a converged bias")
    return Weights(values, effective, converged, note)


def weighted_mean(values: np.ndarray, weights: Weights) -> float:
    """A weighted average, and nothing more clever than that."""
    array = np.asarray(values, dtype=float)
    if not len(array) or len(array) != len(weights.values):
        return float("nan")
    return float(np.sum(array * weights.values) / np.sum(weights.values))


def weighted_uncertainty(
    values: "np.ndarray",
    weights: "Weights",
    *,
    resamples: int = 200,
    seed: int = 0,
    block_length: int | None = None,
) -> "dict[str, object]":
    """A standard error on a reweighted average, and whether to trust it.

    `weighted_standard_deviation` describes the spread of the reweighted
    distribution. It is not an error on the mean, and it barely moves when
    the sampling behind that mean collapses: measured on this package's own
    output it changed 3% for a 37-fold drop in effective sample size, while
    the report printed it as a plus-or-minus. That is the defect this
    function exists to answer.

    The estimate is a moving-block bootstrap over the frames, resampling
    values and weights together -- the pairing is the whole content of a
    reweighted average, and shuffling them apart would put one frame's
    value with another's weight. The block length is taken from the values
    unless ``block_length`` is given; a caller that knows the collective
    variable decorrelates more slowly passes ``ceil(2 g)`` of it.

    **It is reported as a floor where the weights concentrate.** Against the
    spread of the estimator over 200 independent realisations it holds to
    about 15% while a tenth of the frames still carry the average, and
    returns 0.36 of the true spread once that falls to a hundredth. The
    limit is structural: resampling one run's frames cannot see how much the
    weights themselves would differ in another run. Where that matters the
    note says so and names replicas as the answer.
    """
    import numpy as np

    from fastmdxplora.uncertainty import WEIGHT_ESS_FLOOR, paired_block_bootstrap

    v = np.asarray(values, dtype=float).ravel()
    w = np.asarray(weights.values, dtype=float).ravel()
    if v.size != w.size or v.size == 0:
        raise StudyError(
            f"{v.size} values against {w.size} weights: a reweighted average "
            "needs one weight per frame.", code="simulation.bias.dimension_mismatch")

    def _mean(a: "np.ndarray", b: "np.ndarray") -> float:
        total = float(np.sum(b))
        return float(np.sum(a * b) / total) if total else float("nan")

    result = paired_block_bootstrap([v, w], _mean, resamples=resamples,
                                    seed=seed,
                                    block_length=block_length).as_dict()
    ess = float(weights.effective_sample_size)
    fraction = ess / float(v.size) if v.size else 0.0
    result["effective_sample_size"] = ess
    result["effective_fraction"] = fraction
    result["is_a_floor"] = bool(fraction < WEIGHT_ESS_FLOOR)
    if result["is_a_floor"]:
        floor_note = (
            f"{ess:.0f} effective samples from {v.size} frames "
            f"({100 * fraction:.1f}%). Below {100 * WEIGHT_ESS_FLOOR:.0f}% a "
            "resampling error bar on a weighted average is a floor rather "
            "than an estimate: it cannot see how much the weights "
            "themselves would differ in another run, and compared with "
            "independent realisations it returned as little as a third of "
            "the true spread. Independent replicas are the honest route.")
        result["note"] = (f"{result['note']} {floor_note}" if result.get("note")
                          else floor_note)
    return result


def independent_samples(
    weights: Weights, *series: np.ndarray, inefficiency: float = 1.0,
) -> tuple[float, float]:
    """Independent samples a weighted average rests on, and the inefficiency
    used to count them.

    ``n_independent = n_Kish / g``, where ``n_Kish`` is the weights'
    :attr:`Weights.effective_sample_size` and ``g`` the largest statistical
    inefficiency among the given per-frame series and ``inefficiency``.
    Kish's count says how evenly the weight is spread over the frames and
    treats every frame as independent; consecutive frames are not, and a
    weighted mean is correlated through both its values and its weights, so
    the slower of the observable and the collective variable the weights
    depend on sets ``g``. On a well-tempered run with a bias factor of 8,
    2445 weight-concentration effective frames held about 27 independent
    samples.
    """
    from fastmdxplora.statistics import statistical_inefficiency

    g = float(inefficiency) if np.isfinite(inefficiency) else 1.0
    for values in series:
        array = np.asarray(values, dtype=float)
        if array.ndim == 2:
            columns = [array[:, k] for k in range(array.shape[1])]
        else:
            columns = [array.ravel()]
        for column in columns:
            column = column[np.isfinite(column)]
            if column.size >= 3:
                g = max(g, statistical_inefficiency(column))
    g = max(g, 1.0)
    return float(weights.effective_sample_size) / g, g


def reweighted_estimate(
    values: np.ndarray,
    weights: Weights,
    *,
    inefficiency: float = 1.0,
    resamples: int = 200,
    seed: int = 0,
) -> dict[str, Any]:
    """A reweighted mean with its standard error, or the reason it has none.

    The error is :func:`weighted_uncertainty`'s paired block bootstrap. It is
    withheld (``standard_error`` None, ``not_a_measurement`` the reason,
    ``refusal`` its code) where the observable or the collective variable,
    whose inefficiency is passed as ``inefficiency``, is not long against its
    own correlation time (``analysis.sampling.correlation_unresolved``: at
    least 25 inefficiencies, as :func:`fastmdxplora.statistics.summarise`
    asks), or where :func:`independent_samples` is below
    :data:`fastmdxplora.statistics.MINIMUM_EFFECTIVE_SAMPLES`
    (``analysis.sampling.too_few_independent``).
    """
    from fastmdxplora.statistics import (
        MINIMUM_EFFECTIVE_SAMPLES,
        RESOLVED_SAMPLES,
        Withholding,
        correlation_is_resolved,
    )

    array = np.asarray(values, dtype=float).ravel()
    independent, g = independent_samples(weights, array, inefficiency=inefficiency)
    record: dict[str, Any] = {
        "mean": weighted_mean(array, weights),
        "standard_error": None,
        "weight_concentration_effective_frames": float(weights.effective_sample_size),
        "statistical_inefficiency": g,
        "independent_samples": independent,
    }
    n = int(array.size)
    cv_resolved = (not np.isfinite(inefficiency) or inefficiency < 2.0
                   or n / float(inefficiency) >= RESOLVED_SAMPLES)
    reason = None
    if n < 3 or not (correlation_is_resolved(array) and cv_resolved):
        reason = Withholding(
            f"{n} frames are not long against their own correlation time "
            f"(one independent sample every {g:.0f} frames, and "
            f"{RESOLVED_SAMPLES:g} of them resolve it), so a resampling error "
            "on this reweighted mean would be too small by an unknown factor. "
            "The remedy is a longer run.",
            code="analysis.sampling.correlation_unresolved",
            frames=n, independent=independent, statistical_inefficiency=g,
            needed=float(RESOLVED_SAMPLES))
    elif independent < MINIMUM_EFFECTIVE_SAMPLES:
        reason = Withholding(
            f"{weights.effective_sample_size:.0f} weight-concentration "
            f"effective frames, correlated over {g:.0f} frames, hold "
            f"{independent:.1f} independent samples. Below "
            f"{MINIMUM_EFFECTIVE_SAMPLES:g} an error on a reweighted mean "
            "describes how this run happened to go rather than the system. "
            "The remedy is a longer run, or replicas.",
            code="analysis.sampling.too_few_independent",
            independent=independent, frames=n, statistical_inefficiency=g,
            needed=float(MINIMUM_EFFECTIVE_SAMPLES))
    if reason is not None:
        record["not_a_measurement"] = str(reason)
        record["refusal"] = reason.refusal.code
        return record

    # Blocks of 2g of the slower series, as `block_length_for` would choose
    # from the values alone: the weights carry the collective variable's
    # correlation, and blocks shorter than it break what is really there.
    block = max(1, min(n, int(np.ceil(2.0 * g))))
    bootstrap = weighted_uncertainty(array, weights, resamples=resamples,
                                     seed=seed, block_length=block)
    record["standard_error"] = float(bootstrap["standard_error"])
    record["is_a_floor"] = bool(bootstrap["is_a_floor"])
    if bootstrap.get("note"):
        record["note"] = bootstrap["note"]
    return record


def weighted_standard_deviation(values: np.ndarray, weights: Weights) -> float:
    """The spread about the weighted mean, on the effective sample size.

    Dividing by the frame count would understate it: a thousand frames whose
    weight sits in fifty of them carry the uncertainty of fifty.
    """
    array = np.asarray(values, dtype=float)
    if len(array) != len(weights.values) or weights.effective_sample_size <= 1:
        return float("nan")
    mean = weighted_mean(array, weights)
    variance = float(
        np.sum(weights.values * (array - mean) ** 2) / np.sum(weights.values))
    correction = weights.effective_sample_size / (
        weights.effective_sample_size - 1.0)
    return float(np.sqrt(variance * correction))
