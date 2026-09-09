"""Reporting a biased run's analyses as the unbiased averages they are not.

Sixteen analyses run on the production trajectory and each reports a mean. On
a metadynamics run every one of those means is an average over a distribution
the bias deliberately flattened, and reported without qualification it reads
as a measurement of the system. The report already says so in its methods.
This computes the correction it points at.

Only metadynamics gets weights, and the other two biased methods are left
alone deliberately rather than overlooked. An umbrella window is a separate
simulation held where it was put; what combines the windows is the potential
of mean force, not a weighted average across them. A steered pull is not an
equilibrium ensemble at all, so there is no set of weights that turns it into
one. Both are stated in the methods text and neither is fixable here.

What comes out is both numbers side by side -- the raw average over the
biased frames and the reweighted one -- with the effective sample size that
says how much the second rests on. A reweighted mean over a thousand frames
whose weight sits in five of them is a mean over five, and quoting it without
that number is the failure mode this is built to avoid.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from fastmdxplora.analysis.plotting import new_figure, save_figure, colour
from fastmdxplora.analysis.reweight import (
    KB_KJ_PER_MOL_K,
    Weights,
    bias_at_each_frame,
    weighted_mean,
    weighted_standard_deviation,
    weights_from_bias,
)
from fastmdxplora.utils.logging import get_logger

logger = get_logger("analysis.reweighted")

#: Below this many effective frames a reweighted average is reported with a
#: warning attached. The number is not a threshold for correctness -- the
#: estimate is what it is -- but for whether it carries enough frames to be
#: worth reading, and fifty is where a mean stops being dominated by a
#: handful of them.
USABLE_EFFECTIVE_FRAMES = 50.0

#: If the run reached equilibrium temperature somewhere other than 300 K and
#: nothing recorded it, the weights would be silently wrong by the ratio of
#: the temperatures. Falling back is still better than refusing, but it is
#: written into the record so it is not mistaken for a reading.
FALLBACK_TEMPERATURE_K = 300.0


# ---------------------------------------------------------------------------
# Reading what PLUMED left behind
# ---------------------------------------------------------------------------
def read_colvar(path: str | Path) -> dict[str, np.ndarray]:
    """Read a PLUMED COLVAR file by its declared field names.

    The header names the columns, and reading by name rather than position
    matters here: a metadynamics COLVAR is ``time, cv, metad.bias`` but a
    steered one is ``time, cv, pull.work, pull.bias``, and column two means
    something different in each.
    """
    text = Path(path).read_text(encoding="utf-8")
    fields: list[str] = []
    rows: list[list[float]] = []

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            # "#! FIELDS time cv metad.bias"
            parts = stripped.lstrip("#!").split()
            if parts and parts[0] == "FIELDS":
                fields = parts[1:]
            continue
        try:
            rows.append([float(value) for value in stripped.split()])
        except ValueError:
            continue

    if not rows:
        return {}

    width = min(len(row) for row in rows)
    table = np.array([row[:width] for row in rows], dtype=float)
    if not fields or len(fields) < width:
        fields = [f"column_{index}" for index in range(width)]
    return {name: table[:, index] for index, name in enumerate(fields[:width])}


def _run_directory(output_dir: Path) -> list[Path]:
    """Directories a run's simulation outputs might sit under.

    The analysis output directory is ``<run>/analysis``, so the simulation
    files are one level up and then into ``simulation/``. Both the nested and
    flat layouts are checked because the umbrella windows use the other one.
    """
    here = Path(output_dir)
    candidates: list[Path] = []
    for parent in (here, here.parent, here.parent.parent):
        candidates.extend((parent / "simulation", parent))
    return candidates


def _find(output_dir: Path, filename: str) -> Path | None:
    for directory in _run_directory(output_dir):
        candidate = directory / filename
        if candidate.is_file():
            return candidate
    return None


def _temperature(output_dir: Path) -> tuple[float, bool]:
    """The production temperature, and whether it was actually found."""
    path = _find(output_dir, "simulation_parameters.json")
    if path is not None:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            record = {}
        for holder in (record, record.get("plan") or {},
                       record.get("parameters") or {}):
            if isinstance(holder, dict) and holder.get("temperature_K"):
                return float(holder["temperature_K"]), True
    return FALLBACK_TEMPERATURE_K, False


#: What each method leaves beside the run. The script is written under the
#: method's own name, so it says which bias was applied even where the result
#: file is missing because the run stopped early.
METHOD_MARKERS = (
    ("umbrella", "umbrella.plumed"),
    ("steered", "steered.plumed"),
    ("metadynamics", "metadynamics.plumed"),
)

#: Why no set of weights recovers an equilibrium average from these two. Both
#: are stated in the report's methods text; these are the same claims, put
#: where the numbers are.
NOT_REWEIGHTABLE = {
    "umbrella": (
        "This run is one window of an umbrella study, held at its own position "
        "on the coordinate by a harmonic restraint. Its averages describe a "
        "system held there and are not measurements of the unrestrained "
        "system, nor comparable between windows, which differ because the "
        "restraints differ. What combines the windows is the potential of "
        "mean force, not an average of any quantity across them."),
    "steered": (
        "Production was a steered pull, which is not an equilibrium ensemble: "
        "the system was dragged along the coordinate rather than sampling it. "
        "These averages describe the pulling, and no reweighting recovers an "
        "equilibrium average from a single non-equilibrium trajectory."),
}


def _configured_cv(output_dir: Path) -> str | None:
    """What the run was told to bias, as opposed to what PLUMED labelled it."""
    path = _find(output_dir, "simulation_parameters.json")
    if path is None:
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    for holder in (record, record.get("plan") or {},
                   record.get("parameters") or {}):
        if not isinstance(holder, dict):
            continue
        metad = holder.get("metadynamics")
        if isinstance(metad, dict) and metad.get("collective_variable"):
            return str(metad["collective_variable"])
    return None


def before_deposition(hills: Any, times_ps: Any) -> np.ndarray:
    """Frame times nudged so a hill laid *at* time t is not felt at time t.

    PLUMED prints the bias for a step before depositing that step's hill, and
    HILLS and COLVAR usually share a stride, so counting a hill as felt at its
    own deposition time is wrong on every row. On a real run it put the
    reconstruction out by 1.200 kJ/mol against PLUMED's own record -- exactly
    one hill at the configured height, which is what made it identifiable.

    The nudge is a small fraction of the deposition interval, which is the
    smallest time difference that matters here, so it moves each frame off the
    boundary without moving it past the previous hill.
    """
    times = np.asarray(times_ps, dtype=float)
    deposition = np.asarray(hills.time_ps, dtype=float)
    if times.size == 0 or deposition.size < 2:
        return times
    gaps = np.diff(np.sort(deposition))
    interval = float(np.min(gaps[gaps > 0])) if np.any(gaps > 0) else 0.0
    return times - 1e-6 * interval


def biasing_method(output_dir: Path) -> str | None:
    """Which method biased this run, from what it left on disk."""
    for method, marker in METHOD_MARKERS:
        if _find(output_dir, marker) is not None:
            return method
    return None


def deposited_heights(hills: Any) -> np.ndarray:
    """The heights actually added to the bias, from what HILLS records.

    For a well-tempered run PLUMED does not store the height it deposited. It
    stores that height multiplied by y/(y-1), where y is the bias factor, so
    that summing the file gives the free energy directly -- which is the
    convention `metad_surface` relies on and must keep.

    Reconstructing the *bias* needs it undone. Summing the stored heights
    overstates the bias by y/(y-1): 11.1% at a bias factor of 10, which is
    what a real run showed against PLUMED's own record of the same quantity.
    That error does not cancel between V and c(t) -- both scale by the same
    factor, so their difference scales too, and since the weights go as
    exp((V - c(t))/RT) a scaled exponent is an effective-temperature error.
    It sharpens the weights, understates the effective sample size, and
    biases every average that rests on them.

    A run that was not tempered stores what it deposited, and needs nothing.
    """
    heights = np.asarray(hills.height, dtype=float)
    factor = float(getattr(hills, "bias_factor", 1.0) or 1.0)
    if factor <= 1.0:
        return heights
    return heights * (factor - 1.0) / factor


# ---------------------------------------------------------------------------
# The time-dependent offset, without which the weights measure the clock
# ---------------------------------------------------------------------------
#: How many points the offset is evaluated at before being interpolated onto
#: the frames. c(t) is smooth in time -- it tracks the filling of the surface,
#: not the motion of the system -- so a couple of hundred is ample, and
#: evaluating it per frame would cost a great deal for no change in the answer.
C_OF_T_CHECKPOINTS = 200

#: Points across the collective variable for the two integrals below.
C_OF_T_GRID_POINTS = 320


def felt_bias(hills: Any, heights: np.ndarray, *, times: Any, values: Any,
              periodic: "bool | tuple[bool, ...]") -> np.ndarray:
    """The bias each frame felt, measured the short way round on a circle.

    A frame at -175 degrees is fifteen degrees from a hill at +170, not three
    hundred and forty-five, and summed straight it feels none of that hill --
    so its weight ignores the bias that was actually there.

    Done by summing the plain calculation over the coordinate's periodic
    images rather than by teaching the weighting maths about circles. A
    periodic Gaussian is the sum over images, so this is not an approximation
    of the right answer, it is the right answer; and with a hill width of a
    fifth of a radian the next image sits eighteen widths away and contributes
    1e-70, so one image either side is the whole of it. `reweight.py` holds
    generic weighting arithmetic and PLUMED's conventions belong here.
    """
    frames = np.asarray(values, dtype=float)
    if getattr(hills, "n_dims", 1) > 1:
        from fastmdxplora.simulation.metad_surface import bias_from_hills_nd

        if frames.ndim != 2 or frames.shape[1] != hills.n_dims:
            raise ValueError(
                f"The hills hold {hills.n_dims} biased variables but frame "
                f"coordinates have shape {frames.shape}."
            )
        wrap = tuple(periodic) if not isinstance(periodic, bool) else (
            (periodic,) * hills.n_dims)
        frame_times = np.asarray(times, dtype=float)
        total = np.zeros(frames.shape[0], dtype=float)
        for index, (when, where) in enumerate(zip(frame_times, frames)):
            laid = int(np.searchsorted(hills.time_ps, when, side="right"))
            total[index] = bias_from_hills_nd(
                hills, where[None, :], heights=heights, upto=laid,
                periodic=wrap)[0]
        return total

    shifts = (-2.0 * np.pi, 0.0, 2.0 * np.pi) if periodic else (0.0,)
    total = np.zeros(frames.size, dtype=float)
    for shift in shifts:
        total += bias_at_each_frame(
            hills.time_ps, hills.centre, hills.sigma, heights,
            frame_times_ps=times, frame_values=frames + shift)
    return total


def c_of_t(hills: Any, times_ps: np.ndarray, temperature_K: float,
           periodic: "bool | tuple[bool, ...]" = False) -> np.ndarray:
    """The offset that makes a running bias usable for reweighting.

    The bias grows as hills accumulate, so exp(V(s,t)/RT) grows with time
    wherever the system happens to be. Weighting by it alone therefore ranks
    frames by when they were written: on a four-nanosecond well-tempered run
    the last fifth of the frames carried the entire weight, and a reweighted
    average over five hundred frames rested on seven. That is not a bias that
    has not settled -- it happens to a fully converged run -- and no warning
    about convergence covers it.

    Tiwary and Parrinello's c(t) is the term that removes it: the free-energy
    offset of the biased ensemble at time t, so that V - c(t) measures where
    the system is on the coordinate rather than how late it is in the run.
    For a well-tempered run with bias factor y,

        c(t) = RT ln [ int ds e^{(y/(y-1)) V(s,t)/RT}
                       / int ds e^{(1/(y-1)) V(s,t)/RT} ]

    and the y -> infinity limit of that, which is the ratio to a flat
    integral, is the form for a run that was not tempered.
    """
    if getattr(hills, "n_dims", 1) > 1:
        wrap = tuple(periodic) if not isinstance(periodic, bool) else (
            (periodic,) * hills.n_dims)
        return _c_of_t_nd(hills, times_ps, temperature_K, periodic=wrap)

    kT = KB_KJ_PER_MOL_K * float(temperature_K)
    order = np.argsort(hills.time_ps)
    hill_times = np.asarray(hills.time_ps, dtype=float)[order]
    centres = np.asarray(hills.centre, dtype=float)[order]
    sigmas = np.asarray(hills.sigma, dtype=float)[order]
    heights = deposited_heights(hills)[order]

    if periodic:
        # The whole turn. c(t) integrates over the coordinate's domain, and
        # for a circle that is all of it -- a grid bounded by where hills
        # happened to land would leave out the arc they wrap around.
        grid = np.linspace(-np.pi, np.pi, C_OF_T_GRID_POINTS)
    else:
        span = 3.0 * float(np.max(sigmas))
        grid = np.linspace(float(np.min(centres)) - span,
                           float(np.max(centres)) + span,
                           C_OF_T_GRID_POINTS)

    checkpoints = np.linspace(float(np.min(times_ps)),
                              float(np.max(times_ps)),
                              min(C_OF_T_CHECKPOINTS, max(2, times_ps.size)))
    # Same boundary as the frames: a hill is not felt at the instant it
    # is laid. See `before_deposition`.
    upto = np.searchsorted(hill_times, checkpoints, side="left")

    factor = float(hills.bias_factor)
    if factor > 1.0:
        scale_numerator = factor / (factor - 1.0)
        scale_denominator = 1.0 / (factor - 1.0)
    else:
        # Not tempered: the bias converges on -F directly, and the
        # denominator becomes the flat integral over the grid.
        scale_numerator, scale_denominator = 1.0, 0.0

    def log_integral(values: np.ndarray) -> float:
        """log sum exp, shifted so a bias of 100 kJ/mol does not overflow."""
        peak = float(np.max(values))
        return float(np.log(np.sum(np.exp(values - peak))) + peak)

    # Accumulated in place rather than as a hills-by-grid matrix: a long run
    # deposits a hundred thousand hills, and that matrix would be gigabytes.
    bias = np.zeros_like(grid)
    offsets = np.empty(checkpoints.size, dtype=float)
    cursor = 0
    for index, stop in enumerate(upto):
        if stop > cursor:
            block = slice(cursor, stop)
            separation = grid[None, :] - centres[block, None]
            if periodic:
                separation = np.remainder(
                    separation + np.pi, 2.0 * np.pi) - np.pi
            bias += np.sum(
                heights[block, None] * np.exp(
                    -(separation ** 2) / (2.0 * sigmas[block, None] ** 2)),
                axis=0)
            cursor = int(stop)
        offsets[index] = kT * (
            log_integral(scale_numerator * bias / kT)
            - log_integral(scale_denominator * bias / kT))

    return np.interp(np.asarray(times_ps, dtype=float), checkpoints, offsets)


def _c_of_t_nd(
    hills: Any,
    times_ps: np.ndarray,
    temperature_K: float,
    *,
    periodic: tuple[bool, ...],
) -> np.ndarray:
    """Multidimensional c(t), integrated on one axis per biased variable."""
    from fastmdxplora.simulation.metad_surface import Hills, bias_from_hills_nd

    if len(periodic) != hills.n_dims:
        raise ValueError(
            f"The hills hold {hills.n_dims} variables but periodicity has "
            f"{len(periodic)} entries."
        )

    kT = KB_KJ_PER_MOL_K * float(temperature_K)
    order = np.argsort(hills.time_ps)
    ordered = Hills(
        time_ps=np.asarray(hills.time_ps, dtype=float)[order],
        centre=np.asarray(hills.centre, dtype=float)[order],
        sigma=np.asarray(hills.sigma, dtype=float)[order],
        height=np.asarray(hills.height, dtype=float)[order],
        bias_factor=float(hills.bias_factor),
    )
    heights = deposited_heights(hills)[order]

    # Keep the integration budget bounded as dimensions grow. Forty points
    # per axis in 2D gives 1,600 integration points; higher-dimensional runs
    # retain approximately that total with a floor of eight per axis.
    points_per_axis = max(8, int(round(1600 ** (1.0 / hills.n_dims))))
    axes: list[np.ndarray] = []
    for dim in range(hills.n_dims):
        centres = ordered.column(dim)
        sigmas = ordered.sigma_column(dim)
        if periodic[dim]:
            axes.append(np.linspace(-np.pi, np.pi, points_per_axis))
        else:
            span = 3.0 * float(np.max(sigmas))
            axes.append(np.linspace(float(np.min(centres)) - span,
                                    float(np.max(centres)) + span,
                                    points_per_axis))
    mesh = np.meshgrid(*axes, indexing="ij")
    points = np.column_stack([axis.ravel() for axis in mesh])

    times = np.asarray(times_ps, dtype=float)
    checkpoints = np.linspace(
        float(np.min(times)), float(np.max(times)),
        min(C_OF_T_CHECKPOINTS, max(2, times.size)))
    upto = np.searchsorted(ordered.time_ps, checkpoints, side="left")

    factor = float(hills.bias_factor)
    if factor > 1.0:
        scale_numerator = factor / (factor - 1.0)
        scale_denominator = 1.0 / (factor - 1.0)
    else:
        scale_numerator, scale_denominator = 1.0, 0.0

    def log_integral(values: np.ndarray) -> float:
        peak = float(np.max(values))
        return float(np.log(np.sum(np.exp(values - peak))) + peak)

    bias = np.zeros(points.shape[0], dtype=float)
    offsets = np.empty(checkpoints.size, dtype=float)
    cursor = 0
    for index, stop in enumerate(upto):
        if stop > cursor:
            bias += bias_from_hills_nd(
                ordered, points, heights=heights, start=cursor,
                upto=int(stop), periodic=periodic)
            cursor = int(stop)
        offsets[index] = kT * (
            log_integral(scale_numerator * bias / kT)
            - log_integral(scale_denominator * bias / kT))

    return np.interp(times, checkpoints, offsets)


# ---------------------------------------------------------------------------
# The weights for a run
# ---------------------------------------------------------------------------
def weights_for_run(
    output_dir: Path,
    frame_times_ps: Any,
) -> tuple[Weights | None, dict[str, Any]]:
    """Per-frame weights undoing the metadynamics bias, where one was applied.

    Returns ``(None, provenance)`` for any run that was not biased by
    metadynamics -- plain MD, a steered pull, an umbrella window -- because
    none of those has a set of weights that recovers an equilibrium average.

    The bias each frame felt is rebuilt from the hills deposited before it,
    using that frame's own value of the collective variable interpolated from
    COLVAR. Interpolating the variable rather than PLUMED's bias column is
    deliberate: the variable is a smooth physical coordinate, while the bias
    is a sum of narrow Gaussians that interpolation would badly misstate
    between rows.
    """
    from fastmdxplora.simulation.metad_surface import read_hills

    provenance: dict[str, Any] = {"applies": False, "reason": None}

    hills_path = _find(output_dir, "HILLS")
    if hills_path is None:
        provenance["reason"] = (
            "No HILLS beside this run, so no bias was deposited on the "
            "trajectory and its averages are already equilibrium averages.")
        return None, provenance

    colvar_path = _find(output_dir, "COLVAR")
    if colvar_path is None:
        provenance["reason"] = (
            "Hills were deposited but no COLVAR records where the run went, "
            "so the bias each frame felt cannot be worked out. The averages "
            "below are over the biased ensemble.")
        return None, provenance

    try:
        hills = read_hills(hills_path)
    except (OSError, ValueError) as exc:
        provenance["reason"] = f"The hills could not be read: {exc}"
        return None, provenance

    colvar = read_colvar(colvar_path)
    cv_names = [name for name in colvar
                if name != "time" and not name.endswith("bias")]
    if not cv_names or "time" not in colvar:
        provenance["reason"] = (
            "COLVAR holds no collective variable column, so the frames "
            "cannot be placed on the coordinate the bias acted along.")
        return None, provenance
    if len(cv_names) != hills.n_dims:
        provenance["reason"] = (
            f"HILLS contains {hills.n_dims} biased variable(s), but COLVAR "
            f"contains {len(cv_names)} collective-variable column(s) "
            f"({', '.join(cv_names)}). Reweighting needs exactly one COLVAR "
            "coordinate for each HILLS dimension."
        )
        return None, provenance

    times = np.asarray(frame_times_ps, dtype=float)
    if times.size == 0:
        provenance["reason"] = "The trajectory holds no frames to weight."
        return None, provenance

    # Frames written outside the recorded window would extrapolate; numpy
    # clamps to the end values instead, which is the right behaviour for the
    # one or two frames that can fall outside by a single stride.
    frame_columns = [
        np.interp(times, colvar["time"], colvar[name])
        for name in cv_names]
    frame_cv = (frame_columns[0] if hills.n_dims == 1
                else np.column_stack(frame_columns))

    # A torsion is a circle, and every Gaussian sum below has to measure the
    # separation the short way round: a frame at -175 degrees is 15 degrees
    # from a hill at +170, not 345. Decided from the configured name before
    # anything is summed, because both the felt bias and the offset need it.
    from fastmdxplora.simulation.umbrella import PERIODIC_VARIABLES

    if hills.n_dims == 1:
        variable: Any = _configured_cv(output_dir) or cv_names[0]
        periodic: Any = variable in PERIODIC_VARIABLES
    else:
        from fastmdxplora.simulation.metad_surface import periodic_dimensions

        variable = cv_names
        script = _find(output_dir, "plumed.dat")
        periodic = (periodic_dimensions(script, hills.n_dims)
                    if script is not None else (False,) * hills.n_dims)

    heights = deposited_heights(hills)
    felt = felt_bias(
        hills, heights, times=before_deposition(hills, times),
        values=frame_cv, periodic=periodic)

    temperature, measured = _temperature(output_dir)

    # Without this the weights rank frames by when they were written rather
    # than by where the system was, and the average collapses onto the last
    # few. See `c_of_t`.
    offset = c_of_t(hills, times, temperature, periodic=periodic)
    corrected = felt - offset

    # Whether the surface settled is already judged by the simulation phase,
    # and judging it a second time here by a different rule would let a run
    # be converged in the report and provisional in the analyses.
    settled = False
    surface = _find(output_dir, "metadynamics_surface.json")
    if surface is not None:
        try:
            record = json.loads(surface.read_text(encoding="utf-8"))
            settled = not bool(record.get("provisional", True))
        except (OSError, json.JSONDecodeError):
            settled = False

    weights = weights_from_bias(
        corrected, temperature_K=temperature, settled=settled)

    # PLUMED computed the same quantity as it went. Comparing against it is
    # the only independent check available on this reconstruction, and a
    # disagreement means the frames have been placed on the wrong coordinate.
    agreement: float | None = None
    bias_column = next((name for name in colvar if name.endswith("bias")), None)
    if bias_column is not None and colvar[bias_column].size > 1:
        ours = felt_bias(
            hills, heights,
            times=before_deposition(hills, colvar["time"]),
            values=(colvar[cv_names[0]] if hills.n_dims == 1 else
                    np.column_stack([colvar[name] for name in cv_names])),
            periodic=periodic)
        theirs = colvar[bias_column]
        spread = float(np.max(theirs) - np.min(theirs))
        if spread > 0:
            agreement = float(np.max(np.abs(ours - theirs)) / spread)

    provenance.update({
        "applies": True,
        "reason": None,
        "hills": len(hills),
        "bias_factor": hills.bias_factor,
        # PLUMED's column label is whatever the script called it -- "cv" --
        # which tells a reader nothing about what was biased. The configured
        # name is kept where it can be found, with the label beside it.
        "collective_variable": variable,
        "periodic": periodic,
        "colvar_column": (cv_names[0] if hills.n_dims == 1 else cv_names),
        "temperature_K": temperature,
        "temperature_from_record": measured,
        "settled": settled,
        "estimator": "Tiwary-Parrinello c(t)",
        "c_of_t_range_kjmol": [float(np.min(offset)), float(np.max(offset))],
        "effective_sample_size": weights.effective_sample_size,
        "usable_fraction": weights.usable_fraction,
        "note": weights.note,
        "largest_disagreement_with_plumed": agreement,
    })
    return weights, provenance


# ---------------------------------------------------------------------------
# Pulling a per-frame series out of a finished analysis
# ---------------------------------------------------------------------------
def frame_series(result: Any, spec: tuple[str | None, str],
                 n_frames: int) -> np.ndarray | None:
    """The per-frame scalar an analysis reported, or ``None`` if it has none.

    The length check is the point. SASA in per-residue mode returns a row per
    residue per frame, and averaging that against per-frame weights would
    line the two up by position and produce a number that is not wrong so
    much as meaningless.
    """
    column, _ = spec
    data = getattr(result, "data", result)
    if data is None:
        return None

    if isinstance(data, pd.DataFrame):
        if column is None or column not in data.columns:
            return None
        series = data[column].to_numpy(dtype=float)
    else:
        array = np.asarray(data, dtype=float)
        if array.ndim == 2:
            # A breakdown table whose first column is the total.
            array = array[:, 0]
        elif array.ndim != 1:
            return None
        series = array

    if series.size != n_frames or not np.all(np.isfinite(series)):
        return None
    return series


def frame_labels(result: Any, n_frames: int) -> dict[str, np.ndarray]:
    """Per-frame categorical labels an analysis reported, keyed by method.

    Clustering returns a mapping of method name to labels because it runs
    several and they disagree in useful ways; a single array is accepted too
    so this does not assume that shape.
    """
    data = getattr(result, "data", result)
    if data is None:
        return {}

    candidates: dict[str, Any]
    if isinstance(data, dict):
        candidates = data
    else:
        candidates = {"": data}

    labels: dict[str, np.ndarray] = {}
    for method, values in candidates.items():
        try:
            array = np.asarray(values).ravel()
        except (TypeError, ValueError):
            continue
        if array.size != n_frames or array.ndim != 1:
            continue
        # Labels, not measurements: a float column would make every frame its
        # own category and produce a table with one row per frame.
        if not np.issubdtype(array.dtype, np.integer):
            continue
        labels[str(method)] = array
    return labels


def populations(labels: np.ndarray, weights: Weights) -> list[dict[str, Any]]:
    """How often each state was really visited, against how often it appeared.

    A population is the mean of an indicator, so it reweights exactly as any
    other average does -- and it is the quantity a bias distorts most, since
    escaping a well is what the bias is for.
    """
    rows: list[dict[str, Any]] = []
    for value in np.unique(labels):
        indicator = (labels == value).astype(float)
        raw = float(np.mean(indicator))
        corrected = weighted_mean(indicator, weights)
        rows.append({
            "label": int(value),
            "raw_fraction": raw,
            "reweighted_fraction": corrected,
            "shift_percent": (
                100.0 * (corrected - raw) / raw if raw > 0 else float("nan")),
        })
    return rows


# ---------------------------------------------------------------------------
# The pass itself
# ---------------------------------------------------------------------------
def reweight_results(
    results: dict[str, Any],
    registry: dict[str, type],
    *,
    n_frames: int,
    frame_times_ps: Any,
    output_dir: Path,
) -> dict[str, Any] | None:
    """Recompute every reweightable average against the metadynamics bias.

    Returns the record written to disk, or ``None`` where no bias applies and
    there is nothing to correct.
    """
    # Placing a frame in the deposition history compares this clock against
    # PLUMED's, and PLUMED's is real simulated time. A DCD read through
    # MDTraj used to hand over the frame index instead, so on a 100 ns run
    # every frame was matched against only the hills laid in the first 2 ns
    # -- the reweighting then under-corrected a fully biased ensemble, and
    # unlike the failure `felt_bias` documents, this one leaves the effective
    # sample size looking healthy and raises nothing. `loading` now marks an
    # unknown clock with NaN rather than a frame index, and this is where
    # that has to be refused: the comparison is the whole method.
    #
    # Gated on there being a bias at all, using this module's own detector
    # rather than a second reader of the same thing -- an ordinary unbiased
    # run has no correction to refuse and must not acquire a record saying
    # one was unavailable.
    method = biasing_method(Path(output_dir))
    times = np.asarray(frame_times_ps, dtype=float)
    if method is not None and times.size and not np.all(np.isfinite(times)):
        record = {
            "n_frames": int(n_frames),
            "applies": False,
            "biasing_method": method,
            "reason": (
                "How much simulated time separates the saved frames was not "
                "recorded, so they cannot be placed against the hills PLUMED "
                "deposited in real time -- and that placement is what undoing "
                "the bias consists of. Record the trajectory saving interval, "
                "or pass saving_interval_ps when loading, and the correction "
                "becomes available. The averages stand as averages over the "
                "biased ensemble."),
            "quantities": [],
            "populations": [],
            "warnings": [],
        }
        directory = Path(output_dir) / "reweighted"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "reweighted_averages.json").write_text(
            json.dumps(record, indent=2), encoding="utf-8")
        return record

    weights, provenance = weights_for_run(Path(output_dir), frame_times_ps)
    if weights is None:
        method = biasing_method(Path(output_dir))
        if method is None or method not in NOT_REWEIGHTABLE:
            # Either an ordinary run, whose averages are already equilibrium
            # averages, or metadynamics whose bias could not be read -- and
            # `weights_for_run` has already said why in the log.
            logger.debug("No reweighting: %s", provenance.get("reason"))
            return None

        # A biased run whose bias cannot be undone. Writing nothing left its
        # averages looking exactly like an ordinary run's in the report and
        # in the dashboard, which is the failure this whole pass exists to
        # prevent -- and worse here than for metadynamics, because there is
        # no corrected number to put beside them.
        record = {
            "n_frames": int(n_frames),
            "applies": False,
            "biasing_method": method,
            "reason": NOT_REWEIGHTABLE[method],
            "quantities": [],
            "populations": [],
            "warnings": [],
        }
        directory = Path(output_dir) / "reweighted"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "reweighted_averages.json").write_text(
            json.dumps(record, indent=2), encoding="utf-8")
        return record

    quantities: list[dict[str, Any]] = []
    for name, result in results.items():
        if getattr(result, "status", None) != "ok":
            continue
        spec = getattr(registry.get(name), "reweightable", None)
        if not spec:
            continue
        series = frame_series(result, spec, n_frames)
        if series is None:
            continue

        raw = float(np.mean(series))
        corrected = weighted_mean(series, weights)
        quantities.append({
            "analysis": name,
            "label": spec[1],
            "raw_mean": raw,
            "raw_std": float(np.std(series, ddof=1)) if series.size > 1 else float("nan"),
            "reweighted_mean": corrected,
            "reweighted_std": weighted_standard_deviation(series, weights),
            # The number a reader wants: how far the bias moved this.
            "shift_percent": (
                100.0 * (corrected - raw) / raw if raw != 0 else float("nan")),
        })

    occupancies: list[dict[str, Any]] = []
    for name, result in results.items():
        if getattr(result, "status", None) != "ok":
            continue
        if not getattr(registry.get(name), "reweightable_populations", False):
            continue
        for method, labels in frame_labels(result, n_frames).items():
            rows = populations(labels, weights)
            if len(rows) < 2:
                # One state is not a population: nothing was distinguished,
                # and a table saying it was visited all of the time before and
                # after reweighting tells a reader nothing.
                continue
            occupancies.append({
                "analysis": name,
                "method": method,
                "states": rows,
            })

    if not quantities and not occupancies:
        logger.debug("Bias found but no analysis reported a per-frame scalar.")
        return None

    warnings: list[str] = []
    if weights.effective_sample_size < USABLE_EFFECTIVE_FRAMES:
        warnings.append(
            f"The weights concentrate into {weights.effective_sample_size:.0f} "
            f"effective frames of {n_frames}. These reweighted averages rest "
            "on that many, and the spreads beside them are correspondingly "
            "wide; a longer run is what fixes it.")
    if not weights.settled:
        warnings.append(
            "The bias had not settled when the run ended, so these averages "
            "are approximate. The c(t) offset is applied and corrects the "
            "growth of the bias with time, but it is computed from a surface "
            "that is still filling, and a shape that is still moving cannot "
            "give an exact offset. They are worth reading and not worth "
            "quoting to three figures.")
    if not provenance.get("temperature_from_record"):
        warnings.append(
            f"No production temperature was recorded, so the weights assume "
            f"{FALLBACK_TEMPERATURE_K:g} K. A run at another temperature "
            "would need these recomputed.")
    disagreement = provenance.get("largest_disagreement_with_plumed")
    if disagreement is not None and disagreement > 0.05:
        warnings.append(
            f"The reconstructed bias differs from the one PLUMED recorded by "
            f"up to {100 * disagreement:.0f}% of its range, which suggests "
            "the frames and the collective variable have not been lined up "
            "correctly. Treat these as provisional.")

    record = {
        "n_frames": int(n_frames),
        "effective_sample_size": weights.effective_sample_size,
        "usable_fraction": weights.usable_fraction,
        "settled": weights.settled,
        "provenance": provenance,
        "quantities": quantities,
        "populations": occupancies,
        "populations_caveat": (
            "The clustering was performed on the biased frames, so the "
            "states themselves are shaped by where the bias sent the system. "
            "Reweighting corrects how often each was visited, not which "
            "states were found."),
        "warnings": warnings,
        "method": (
            "Each frame is weighted by exp((V - c(t))/RT), where V is the "
            "bias from the hills deposited before that frame, evaluated at "
            "that frame's value of the collective variable, and c(t) is the "
            "Tiwary-Parrinello offset. Subtracting c(t) is what makes the "
            "weight depend on where the system was rather than on how late "
            "in the run the frame was written. This recovers the Boltzmann "
            "ensemble for both standard and well-tempered metadynamics."),
    }

    directory = Path(output_dir) / "reweighted"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "reweighted_averages.json").write_text(
        json.dumps(record, indent=2), encoding="utf-8")
    _write_table(record, directory / "reweighted_averages.dat")
    # A figure of how far each average moved needs averages to have moved.
    # A run whose only reweightable result is a clustering has none, and an
    # empty axes reads as a plot that failed rather than one not called for.
    if quantities:
        _plot(record, directory / "reweighted_averages.png")
    return record


def _write_table(record: dict[str, Any], path: Path) -> None:
    lines = [
        "# Averages over a metadynamics trajectory, before and after "
        "reweighting.",
        f"# effective_sample_size {record['effective_sample_size']:.1f} "
        f"of {record['n_frames']} frames",
    ]
    if not record["settled"]:
        lines.append("# provisional: the bias had not settled")
    lines.append("# analysis raw_mean raw_std reweighted_mean "
                 "reweighted_std shift_percent")
    for item in record["quantities"]:
        lines.append(
            f"{item['analysis']} {item['raw_mean']:.6g} {item['raw_std']:.6g} "
            f"{item['reweighted_mean']:.6g} {item['reweighted_std']:.6g} "
            f"{item['shift_percent']:.3f}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _plot(record: dict[str, Any], path: Path) -> None:
    """How far the bias moved each reported average, as a percentage.

    Percentages rather than the values themselves because RMSD in nanometres
    and a hydrogen bond count share no axis, and normalising them onto one
    would invent a scale. The absolute numbers are in the table beside this.
    """
    quantities = record["quantities"]
    labels = [item["label"] for item in quantities]
    shifts = [item["shift_percent"] for item in quantities]

    fig, ax = new_figure(
        figsize=(7.0, 0.6 * len(labels) + 2.2),
        title="Effect of reweighting the metadynamics bias",
        xlabel="Change from the biased average (%)",
    )
    positions = np.arange(len(labels))
    ax.barh(positions, shifts,
            color=["#c0504d" if abs(s) > 5 else "#4f81bd" for s in shifts])
    ax.axvline(0.0, color=colour("ACCENT"), linewidth=1.0)
    ax.set_yticks(positions)
    ax.set_yticklabels(labels)
    ax.invert_yaxis()

    for position, item in zip(positions, quantities):
        ax.annotate(
            f"{item['raw_mean']:.3g} → {item['reweighted_mean']:.3g}",
            xy=(item["shift_percent"], position),
            xytext=(4 if item["shift_percent"] >= 0 else -4, 0),
            textcoords="offset points", va="center",
            ha="left" if item["shift_percent"] >= 0 else "right",
            fontsize=8, color=colour("ANNOTATION"))

    ess = record["effective_sample_size"]
    caption = (f"{ess:.0f} effective frames of {record['n_frames']}")
    if not record["settled"]:
        caption += " — provisional, the bias had not settled"
    ax.set_xlabel(f"Change from the biased average (%)\n{caption}")

    margin = max([abs(s) for s in shifts] + [1.0]) * 0.45
    ax.set_xlim(min(shifts + [0.0]) - margin, max(shifts + [0.0]) + margin)
    save_figure(fig, path)
