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

**Well-tempered runs converge to a scaled free energy**, not to -F: the bias
approaches -(1 - 1/gamma) F. Tiwary and Parrinello's estimator handles this
with a time-dependent offset c(t); what is implemented here is the simpler
form that holds once the bias has converged, and the caller is told when
not. A surface still filling gives weights that are only approximately right,
which is worth having and worth saying.

    Tiwary, P.; Parrinello, M. A time-independent free energy estimator for
    metadynamics. *J Phys Chem B* **2015**, 119, 736.
    Branduardi, D.; Bussi, G.; Parrinello, M. Metadynamics with adaptive
    Gaussians. *J Chem Theory Comput* **2012**, 8, 2247.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

#: Boltzmann's constant in kJ/mol/K, as the rest of the package uses it.
KB_KJ_PER_MOL_K = 0.008314462618


@dataclass(frozen=True)
class Weights:
    """Per-frame weights, and what they can and cannot be trusted for."""

    values: np.ndarray
    """One weight per frame, normalised to sum to the frame count."""

    effective_sample_size: float
    """How many independent frames the weighted average really rests on.

    The Kish estimate: (sum w)^2 / sum w^2. A weighted mean over a thousand
    frames whose weight is concentrated in five of them is a mean over five,
    and quoting it as a thousand overstates it by a factor of fourteen. This
    is the number that says whether a reweighted average means anything.
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
    value with another's weight.

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
        raise ValueError(
            f"{v.size} values against {w.size} weights: a reweighted average "
            "needs one weight per frame.")

    def _mean(a: "np.ndarray", b: "np.ndarray") -> float:
        total = float(np.sum(b))
        return float(np.sum(a * b) / total) if total else float("nan")

    result = paired_block_bootstrap([v, w], _mean, resamples=resamples,
                                    seed=seed).as_dict()
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
            "themselves would differ in another run, and measured against "
            "independent realisations it returned as little as a third of "
            "the true spread. Independent replicas are the honest route.")
        result["note"] = (f"{result['note']} {floor_note}" if result.get("note")
                          else floor_note)
    return result


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


def read_colvar(path: str | Path) -> tuple[np.ndarray, np.ndarray]:
    """Times and collective-variable values, from a PLUMED COLVAR."""
    columns = np.atleast_2d(np.loadtxt(path, comments="#"))
    if columns.shape[1] < 2:
        raise ValueError(
            f"{Path(path).name} holds {columns.shape[1]} column(s); the "
            "collective variable is the second, after the time.")
    return columns[:, 0], columns[:, 1]


def weights_for_run(
    simulation_dir: str | Path,
    frame_times_ps: np.ndarray,
    *,
    temperature_K: float = 300.0,
) -> Weights | None:
    """Weights for a run's trajectory frames, or None where there are none.

    ``frame_times_ps`` are the trajectory's own times, which is what makes
    this correct rather than approximately correct: the collective variable
    is recorded on PLUMED's stride and the trajectory on its own, and the two
    need not coincide. The variable is interpolated onto the frame times
    rather than assumed to line up with them.
    """
    directory = Path(simulation_dir)
    hills_path = directory / "HILLS"
    colvar_path = directory / "COLVAR"
    if not hills_path.is_file() or not colvar_path.is_file():
        return None

    from fastmdxplora.simulation.metad_surface import read_hills

    hills = read_hills(hills_path)
    if not len(hills.time_ps):
        return None
    colvar_times, colvar_values = read_colvar(colvar_path)
    if not len(colvar_times):
        return None

    frames = np.asarray(frame_times_ps, dtype=float)
    at_frames = np.interp(frames, colvar_times, colvar_values)

    bias = bias_at_each_frame(
        hills.time_ps, hills.centre, hills.sigma, hills.height,
        frames, at_frames)

    # Settled where the last hills are a small fraction of the first, the
    # same test the surface uses to decide whether to report one.
    from fastmdxplora.simulation.metad_surface import SETTLED_HEIGHT_FRACTION

    first = float(np.mean(hills.height[:max(1, len(hills.height) // 20)]))
    last = float(np.mean(hills.height[-max(1, len(hills.height) // 20):]))
    converged = bool(first > 0 and last <= SETTLED_HEIGHT_FRACTION * first)

    return weights_from_bias(
        bias, temperature_K=temperature_K, converged=converged)
