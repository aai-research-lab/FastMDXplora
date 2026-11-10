"""A binding free energy from a potential of mean force along a distance.

The well depth of a PMF is not a binding free energy. Turning one into the
other needs the standard state, because a dissociation constant is defined
against a reference concentration and a curve is not: at 1 M the ligand has
1661 cubic Angstroms to itself, and the free energy of binding is the cost
of giving that up. \\cite{gilson1997}

    K = 4 pi * integral over the bound range of exp(-beta [A(r) - c]) dr
    dG = -kT ln(K / V0),   V0 = 1661 A^3

with c the constant that A(r) + 2kT ln r tends to in bulk.

**Which free energy A(r) is matters, and getting it wrong doubles an
entropy.** Umbrella sampling recombined by histogram gives the free energy
of the radial *distribution*, in which the 4 pi r^2 volume element is
already present: in bulk, where nothing is interacting, A(r) still falls as
-2kT ln r simply because a larger shell holds more places to be. The true
potential of mean force between the two centres is flat there. Writing the
integral above against the distribution form is what makes the r^2 cancel,
and the same expression applied to a Jacobian-removed PMF counts the
translational entropy twice.

**A binding free energy needs the run to have reached bulk.** The reference
A(r_bulk) is only a reference if the ligand is free there, and that is
checkable rather than assumed: in bulk the curve must fall as -2kT ln r. A
run whose windows stopped inside the interaction still produces a smooth
PMF and a plausible number, and the number is wrong by however much of the
well was left outside. Where the outer range does not have that shape, no
binding free energy is reported.

**The bound state is a choice, and its size is reported.** Where the well
ends is a definition, not a measurement. The same curve integrated to
different cutoffs gives different answers, so the sensitivity across a
range of reasonable cutoffs is reported beside the value: a well that is
deep and narrow barely moves, and a shallow one moves a great deal, which
is the reader's cue about how much the definition is doing.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from fastmdxplora.refusals import StudyError

__all__ = ["STANDARD_VOLUME_NM3", "binding_free_energy"]

#: The volume one molecule has to itself at 1 mol/L, in nm^3. 1661 cubic
#: Angstroms, the standard state a dissociation constant is quoted against.
STANDARD_VOLUME_NM3 = 1.660539e0

#: Boltzmann's constant in kJ/(mol K).
KB_KJMOL = 0.008314462618

#: An empty bin whose sampled neighbours both stand this many kT above the
#: curve's minimum is taken as unpopulated: e^-6, a quarter of a percent of
#: the minimum's density, in a bin.
UNPOPULATED_ABOVE_KT = 6.0

#: How much bias the cone's wall may carry where the bound state is before
#: the correction for running inside a cone stops being valid. A tenth of RT
#: at 300 K: the correction assumes the bound pose fits inside the cone, and
#: a wall doing work there has removed part of the population the binding
#: integral is taken over.
WALL_QUIET_KJMOL = 0.25


def _what_the_cone_costs(cone: Any,
                         temperature_K: float) -> "tuple[dict | None, float]":
    """The cone as a record, and the kJ/mol it takes out of the bulk state.

    Accepts the object or the record it writes, because a study recombining
    from what is on disk has the record and a caller in the middle of a run
    has the object.
    """
    if cone is None:
        return None, 0.0
    if hasattr(cone, "as_record"):
        return cone.as_record(temperature_K), cone.correction_kjmol(
            temperature_K)
    if isinstance(cone, dict):
        if cone.get("correction_kjmol") is None:
            raise StudyError(
                "A cone record needs `correction_kjmol`: what the bulk state "
                f"gave up by being confined to it. {sorted(cone)} was given.", code="simulation.bias.parameter_missing")
        return dict(cone), float(cone["correction_kjmol"])
    raise StudyError(
        "`cone` is the angular wall the windows ran under -- an "
        f"umbrella.Cone or the record one writes. {cone!r} was given.", code="simulation.bias.parameter_missing")


#: How well the outer range must follow the bulk form before it counts as
#: bulk. The residual of a fit of A(r) to -2kT ln r + c, in kJ/mol; a
#: quarter of RT at 300 K, which is smaller than any feature a PMF of this
#: kind resolves. Where the curve comes with its bootstrap resamples this is
#: the floor of each allowance, not the whole of it.
BULK_RESIDUAL_KJMOL = 0.6

#: The chance that a curve truly in bulk is refused by its noise alone,
#: split evenly between the two tests on the tail (the points' departures
#: and the tail's tilt), so that by the union bound the two together refuse
#: a bulk tail at most this often.
BULK_FAMILY_CHANCE = 0.05

#: Resamples a tail needs, all of its bins holding a free energy, before
#: their spread is read as the departures' error.
BULK_RESAMPLES_NEEDED = 20


def _two_sided(chance: float, resamples: "int | None") -> float:
    """The two-sided quantile at `chance`: Student's t with `resamples` - 1
    degrees of freedom where the standard deviation is read off that many
    resamples, the normal where `resamples` is None. With 20 to 50 resamples
    the normal's quantile is too small for a spread estimated that way, and a
    tail in bulk was refused 8% of the time at 20 where 5% was meant."""
    if resamples is None:
        from statistics import NormalDist

        return float(NormalDist().inv_cdf(1.0 - chance / 2.0))
    from scipy.stats import t as student

    return float(student.ppf(1.0 - chance / 2.0, max(1, int(resamples) - 1)))


def _sidak_multiplier(count: int, chance: float,
                      resamples: "int | None" = None) -> float:
    """How many standard errors each of `count` points may depart by.

    Sidak's bound: each point is allowed z standard errors, with z the
    two-sided quantile at 1 - (1 - chance)^(1/count), so that the chance of
    any of the `count` departing that far by noise alone is `chance` where
    the departures are independent and normal (Sidak 1967, J. Am. Stat.
    Assoc. 62, 626); Student's t on `resamples` - 1 degrees of freedom where
    the standard errors are read off that many resamples (`_two_sided`). The
    departures of a tail are correlated with each other, so the bound is
    approximate here; for two-sided limits on correlated normal variables
    the inequality runs towards refusing less often than `chance`, not more.
    """
    each = 1.0 - (1.0 - chance) ** (1.0 / max(1, int(count)))
    return _two_sided(each, resamples)


def _tilt(radius: np.ndarray, flattened: np.ndarray) -> np.ndarray:
    """The least-squares slope of each row of `flattened` against r, times
    the tail's span: how far the tail falls or rises across its length."""
    centred = radius - radius.mean()
    slope = (flattened - flattened.mean(axis=-1, keepdims=True)) @ centred \
        / float(centred @ centred)
    return slope * float(radius[-1] - radius[0])


def _judge_the_bulk(radius: np.ndarray, energy: np.ndarray,
                    curves: "np.ndarray | None", kt: float, *,
                    why_fixed: str) -> dict[str, Any]:
    """Whether the tail is flat in f(r) = A(r) + 2kT ln r.

    In bulk f is a constant (Gilson et al. 1997, Biophys. J. 72, 1047: the
    standard-state integral with W(r) flat). Two things are judged on the
    n tail points, each at half of `BULK_FAMILY_CHANCE`:

    - each point's departure d_i = f_i - mean(f), against
      max(0.6 kJ/mol, z_n * s_i), with s_i the standard deviation of d_i
      over the bootstrap's resampled curves and z_n Sidak's multiplier for
      n points at 2.5% (`_sidak_multiplier`), on Student's t with one
      fewer degrees of freedom than the resamples used;
    - the tail's tilt T = b * (r_n - r_1), b the least-squares slope of f
      against r, against max(0.6 kJ/mol, z * s_T), with s_T the standard
      deviation of T over the same resamples and z the two-sided normal
      quantile at 2.5% (2.24 with many resamples; Student's t again).

    s_i and s_T are read off the resamples themselves, so they carry every
    correlation between the bins, and the noise of the bin the curve is
    zeroed at, which every resample shares, cancels in d_i and T as it does
    in the real curve. The per-bin standard errors cannot stand in for them:
    each holds that zero's noise, often most of it, which made an allowance
    about three times the departures' real spread and let a tail falling by
    several kJ/mol pass as bulk. The tilt is the stronger test of a tail that
    is still falling (windows stopped inside the interaction), and the
    departures of one that is not smooth.

    Without resamples, or with fewer than `BULK_RESAMPLES_NEEDED` whose
    tail is whole, every point is judged against 0.6 kJ/mol alone and the
    tilt is not judged, exactly as before the resamples were taken; the
    record says why, in `why_fixed`.
    """
    count = int(radius.size)
    flattened = energy + 2.0 * kt * np.log(radius)
    departure = np.abs(flattened - np.mean(flattened))
    floor = np.full(count, BULK_RESIDUAL_KJMOL)
    whole = None
    if curves is not None:
        shaped = curves + 2.0 * kt * np.log(radius)
        whole = shaped[np.all(np.isfinite(shaped), axis=1)]
    usable = whole is not None and whole.shape[0] >= BULK_RESAMPLES_NEEDED
    tilt = float(_tilt(radius, flattened))
    each_test = BULK_FAMILY_CHANCE / 2.0
    if usable:
        used = int(whole.shape[0])
        multiplier = _sidak_multiplier(count, each_test, used)
        spread = np.std(whole - whole.mean(axis=1, keepdims=True),
                        axis=0, ddof=1)
        allowed = np.maximum(floor, multiplier * spread)
        tilt_multiplier = _two_sided(each_test, used)
        tilt_error = float(np.std(_tilt(radius, whole), ddof=1))
        tilt_allowed = max(BULK_RESIDUAL_KJMOL, tilt_multiplier * tilt_error)
        judged = (
            f"each of the outer {count} points' departure from their mean "
            f"against the larger of {BULK_RESIDUAL_KJMOL} kJ/mol and "
            f"{multiplier:.2f} times that departure's spread over "
            f"{whole.shape[0]} of the bootstrap's resampled curves, and the "
            f"tail's tilt against the larger of {BULK_RESIDUAL_KJMOL} kJ/mol "
            f"and {tilt_multiplier:.2f} times its spread over the same "
            f"resamples (a {each_test:.1%} chance each, so at most "
            f"{BULK_FAMILY_CHANCE:.0%} together, of refusing a tail that is "
            "bulk)")
    else:
        multiplier = tilt_multiplier = tilt_error = tilt_allowed = None
        allowed = floor
        if curves is not None:
            why_fixed = (
                f", since only {0 if whole is None else whole.shape[0]} of "
                f"the resampled curves held a free energy in every bin of the "
                f"tail, against {BULK_RESAMPLES_NEEDED} needed")
        judged = (
            f"each of the outer {count} points' departure from their mean "
            f"against {BULK_RESIDUAL_KJMOL} kJ/mol, the tilt not judged"
            + why_fixed)
    worst = int(np.argmax(departure - allowed))
    tilted = tilt_allowed is not None and abs(tilt) > tilt_allowed
    return {
        "residual": float(np.max(departure)),
        "passed": bool(np.all(departure <= allowed)) and not tilted,
        "departs": not bool(np.all(departure <= allowed)),
        "tilted": tilted,
        "worst_at_nm": float(radius[worst]),
        "worst_kjmol": float(departure[worst]),
        "allowed_kjmol": float(allowed[worst]),
        "tilt_kjmol": tilt,
        "tilt_error_kjmol": tilt_error,
        "tilt_allowed_kjmol": tilt_allowed,
        "by": ("the bootstrap's resampled curves" if usable
               else f"{BULK_RESIDUAL_KJMOL} kJ/mol alone"),
        "resamples_used": None if whole is None else int(whole.shape[0]),
        "multiplier": multiplier,
        "tilt_multiplier": tilt_multiplier,
        "judged": judged,
    }


def _what_the_resamples_said(resampled: "dict[str, Any] | None",
                             correction: float) -> dict[str, Any]:
    """The bootstrap's interval on this number, shifted by the corrections.

    The resampled quantity is the uncorrected binding free energy, because
    that is the part that varies: the cone correction and the standard-state
    term are constants, and adding a constant moves an interval without
    widening it. So the bounds shift and the standard error does not.

    `correlation_resolved` travels with it. An interval whose block length is
    a lower bound is optimistic by an unknown factor, and a number carrying
    one should say so where it is read rather than where it was computed.
    """
    if not resampled or resampled.get("value") is None:
        return {
            "delta_g_standard_error_kjmol": None,
            "delta_g_confidence_kjmol": None,
            "delta_g_uncertainty_is_a_floor": None,
        }
    error = resampled.get("standard_error")
    low = resampled.get("confidence_low")
    high = resampled.get("confidence_high")
    resolved = resampled.get("correlation_resolved")
    return {
        "delta_g_standard_error_kjmol": (
            None if error is None else float(error)),
        "delta_g_confidence_kjmol": (
            None if low is None or high is None
            else [float(low) + correction, float(high) + correction]),
        # Said positively rather than left to be inferred from a flag called
        # `correlation_resolved` two files away.
        "delta_g_uncertainty_is_a_floor": (
            None if resolved is None else not bool(resolved)),
        "delta_g_uncertainty_note": resampled.get("note") or None,
        # Only the resamples that gave a number make the interval, so only
        # they are counted in it; the rest are said beside it.
        "delta_g_resamples": (
            resampled.get("resamples_with_a_number")
            if resampled.get("resamples_with_a_number") is not None
            else resampled.get("resamples")),
        "delta_g_resamples_without_a_number": resampled.get(
            "resamples_without_a_number"),
    }


def _number(value: Any, what: str, window: Any) -> float:
    """`value` as a float, or a StudyError naming the window and the field."""
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise StudyError(
            f"Window {window!r} of `wall_bias_kjmol` has `{what}` "
            f"{value!r:.40}, which is not a number.",
            code="config.option.wrong_type") from exc


def _the_wall_up_to(wall_bias_kjmol: Any, cutoff: float) -> dict[str, Any]:
    """The wall's bias where the bound state is, judged up to the cutoff the
    integral uses.

    Per window, as `umbrella.wall_bias_by_window` gives it. Where a row has
    its frames (``coordinate_nm`` and ``frame_bias_kjmol``), the window is
    judged on its frames at or inside the cutoff alone, and only where it has
    any: the mean of their bias, frames whose bias is NaN left out and
    counted. A window held just outside a steep well is pulled into it, and a
    window that touched the bound range once is mostly elsewhere, so neither
    where it was held nor its mean over the whole run is the wall over the
    bound state. A window with frames inside is judged however few they
    are, and their count is recorded (``frames_inside``): one frame with the
    wall against it is population the integral is over. Where a row has no
    frames (or none were read), it is judged where ``nearest_nm`` (else its
    centre) is at or inside the cutoff, on ``bias_kjmol``, its mean over the
    whole run.

    The largest of the judged means is the number; +inf is a wall that bit.
    A judged window with no bias read (every frame inside NaN, or a NaN or
    missing ``bias_kjmol``) is named in ``not_read`` and the number is
    refused rather than resting on the windows that were read; so it is
    where no window sampled the bound range at all. One number is the
    caller's own reading and is taken as it stands, with the range it covers
    unknown; a NaN one is refused too.
    """
    empty = {"bias_kjmol": None, "up_to_nm": None, "windows": None,
             "judged": None, "bit": None, "not_read": None,
             "unreadable": False, "none_inside": False, "scalar": False}
    if wall_bias_kjmol is None:
        return empty
    if isinstance(wall_bias_kjmol, (str, bytes, dict)) or (
            isinstance(wall_bias_kjmol, np.ndarray)
            and wall_bias_kjmol.ndim > 0):
        raise StudyError(
            "`wall_bias_kjmol` is one number or one row per window "
            "(`umbrella.wall_bias_by_window`); a "
            f"{type(wall_bias_kjmol).__name__} was given.",
            code="config.option.wrong_type")
    if np.ndim(wall_bias_kjmol) == 0:
        value = float(wall_bias_kjmol)
        if np.isnan(value):
            return {**empty, "unreadable": True, "scalar": True}
        return {**empty, "bias_kjmol": value, "scalar": True}
    edge = cutoff * (1.0 + 1e-9)
    judged = []
    for number, row in enumerate(wall_bias_kjmol):
        if not isinstance(row, dict) or not {"window", "centre"} <= set(row):
            raise StudyError(
                f"Row {number} of `wall_bias_kjmol` is not a window's "
                "{\"window\", \"centre\", \"bias_kjmol\"}: "
                f"{row!r:.80} was given.", code="config.option.wrong_type")
        window = row["window"]
        if isinstance(window, bool) or not float(
                _number(window, "window", window)).is_integer():
            raise StudyError(
                f"Row {number} of `wall_bias_kjmol` has `window` "
                f"{window!r:.40}, which is not a window's number.",
                code="config.option.wrong_type")
        window = int(_number(window, "window", window))
        centre = _number(row["centre"], "centre", window)
        if not np.isfinite(centre):
            raise StudyError(
                f"Window {window} of `wall_bias_kjmol` has no centre "
                f"({row['centre']!r}), so whether it holds the bound state "
                "cannot be told.", code="config.option.wrong_type")
        where = row.get("coordinate_nm")
        frames = row.get("frame_bias_kjmol")
        if where is not None and frames is not None:
            where = np.asarray(where, dtype=float).ravel()
            frames = np.asarray(frames, dtype=float).ravel()
        if where is not None and frames is not None and where.size:
            if where.shape != frames.shape:
                raise StudyError(
                    f"Window {window} of `wall_bias_kjmol` has "
                    f"{where.size} coordinates and {frames.size} biases; "
                    "they are one per frame.",
                    code="simulation.bias.dimension_mismatch")
            bound = where <= edge
            if not bound.any():
                continue
            read = bound & ~np.isnan(frames)
            judged.append({
                "window": window, "centre": centre,
                "bias_kjmol": (float(np.mean(frames[read])) if read.any()
                               else float("nan")),
                "frames_inside": int(bound.sum()),
                "frames_unread": int((bound & ~read).sum())})
            continue
        bias = row.get("bias_kjmol")
        bias = float("nan") if bias is None else _number(
            bias, "bias_kjmol", window)
        nearest = row.get("nearest_nm")
        nearest = None if nearest is None else _number(
            nearest, "nearest_nm", window)
        reach = nearest if nearest is not None and np.isfinite(nearest) \
            else centre
        if reach <= edge:
            judged.append({"window": window, "centre": centre,
                           "bias_kjmol": bias, "frames_inside": None,
                           "frames_unread": None})
    read = [row for row in judged if not np.isnan(row["bias_kjmol"])]
    not_read = [row["window"] for row in judged if row not in read]
    return {
        "bias_kjmol": max((row["bias_kjmol"] for row in read), default=None),
        "up_to_nm": float(cutoff),
        "windows": [row["window"] for row in read],
        "judged": judged,
        "bit": [row for row in read if row["bias_kjmol"] > WALL_QUIET_KJMOL],
        "not_read": not_read,
        "unreadable": bool(not_read),
        "none_inside": not judged,
        "scalar": False,
    }


def _saved_bias(value: "float | None") -> "float | None":
    """A wall's bias as `pmf.json` can hold it: None for NaN or infinity,
    which strict JSON readers refuse; the record says which beside it."""
    return None if value is None or not np.isfinite(value) else float(value)


def _saved_row(row: dict[str, Any]) -> dict[str, Any]:
    """One judged window as saved, with ``read`` and ``infinite`` said."""
    bias = row["bias_kjmol"]
    return {**row, "bias_kjmol": _saved_bias(bias),
            "read": not np.isnan(bias),
            "infinite": bool(np.isinf(bias))}


def _the_wall_as_saved(wall: dict[str, Any]) -> dict[str, Any]:
    """`_the_wall_up_to`'s verdict as record fields with no NaN or
    infinity in them; an infinite wall is ``cone_wall_infinite``."""
    bias = wall["bias_kjmol"]
    return {
        "cone_wall_bias_kjmol": _saved_bias(bias),
        "cone_wall_infinite": (None if bias is None
                               else bool(np.isinf(bias))),
        "cone_wall_checked_up_to_nm": wall["up_to_nm"],
        "cone_wall_windows": wall["windows"],
        "cone_wall_bit_in": (None if wall["bit"] is None
                             else [_saved_row(row) for row in wall["bit"]]),
        "cone_wall_not_read_in": wall["not_read"],
        "cone_wall_judged": (None if wall["judged"] is None
                             else [_saved_row(row) for row in wall["judged"]]),
    }


def _array_of_numbers(values: Any, what: str, ndim: int) -> np.ndarray:
    """`values` as a float array of `ndim` dimensions, None read as NaN,
    or a StudyError saying what it was."""
    shape = ("one number per point" if ndim == 1
             else "one curve per resample, one number per point")
    try:
        held = (None if isinstance(values, (str, bytes))
                else np.asarray(values, dtype=object))
    except ValueError:
        held = None
    if held is None or held.ndim != ndim:
        raise StudyError(
            f"{what} must be {shape}; {type(values).__name__} "
            f"{values!r:.60} was given.",
            code="simulation.bias.dimension_mismatch")
    try:
        return np.frompyfunc(lambda v: np.nan if v is None else v, 1, 1)(
            held).astype(float)
    except (TypeError, ValueError) as exc:
        raise StudyError(f"{what} must be numbers: {exc}.",
                         code="config.option.wrong_type") from exc


def binding_free_energy(
    coordinate: Any,
    free_energy_kjmol: Any,
    *,
    temperature_K: float = 300.0,
    bound_cutoff_nm: float | None = None,
    bulk_fraction: float = 0.25,
    cone: Any = None,
    wall_bias_kjmol: Any = None,
    resampled: "dict[str, Any] | None" = None,
    errors_kjmol: Any = None,
    resample_curves_kjmol: Any = None,
    judge_the_bulk: bool = True,
    bulk_fixed_because: "str | None" = None,
) -> dict[str, Any]:
    """A standard-state binding free energy, or a refusal saying why not.

    ``coordinate`` is the ligand-site distance in nm and
    ``free_energy_kjmol`` the recombined free energy along it, as
    ``compute_pmf`` returns them.

    ``bound_cutoff_nm`` is where the bound state is taken to end. Left out,
    it is placed at the first point beyond the minimum where the curve has
    come within kT of its bulk value, which is a defensible reading of
    "no longer interacting" and is reported alongside the answer.

    ``cone`` is the angular wall the windows ran under, where they ran under
    one: either an `umbrella.Cone` or the record one writes. A cone confines
    the ligand to a cap of solid angle Omega, which is what makes the bulk
    reference measurable at all -- the sphere at these radii is neither open
    nor covered, and a cap is both. It costs a term. The bulk state under a
    cone is ``4 pi / Omega`` smaller than a free ligand's, while a bound pose
    that fits inside the cone loses nothing, so the free energy comes out too
    negative by ``kT ln(4 pi / Omega)`` and that is added back here.

    ``wall_bias_kjmol`` is the bias the wall applied where the bound state
    is. The correction above rests on the cone not cutting the bound state,
    which is read off the runs rather than assumed: a wall that bit there
    has removed part of the bound population and no analytic term puts it
    back. Best given per window, as `umbrella.wall_bias_by_window` returns
    it, frame by frame: then every window with frames at or inside the
    cutoff the integral uses is judged on those frames alone, wherever it
    was held, and those that bit are named (`_the_wall_up_to`). A window
    with no bias read on its frames there refuses the number, as does no
    window in the bound range at all. A single number is still accepted, as
    the largest mean in windows the caller chose, and is judged as it
    stands. Given nothing, it says the check was not made rather than
    implying it passed.

    ``resample_curves_kjmol`` is the bootstrap's resampled curves, one row
    per resample on the same points (``pmf["uncertainty"]
    ["resample_curves_kjmol"]`` from `umbrella.compute_pmf`). Given, the
    tail's departures and its tilt are judged against their own spread over
    them, with 0.6 kJ/mol as the floor (`_judge_the_bulk`). ``errors_kjmol``,
    the per-bin standard errors, is accepted and checked but not judged
    against: each holds the noise of the bin the curve is zeroed at, which
    cancels in a departure. Without resamples, or with fewer than
    `BULK_RESAMPLES_NEEDED` whose tail is whole, 0.6 kJ/mol is the whole
    allowance and the tilt is not judged. The record says which was judged.

    ``bulk_fixed_because`` says, in the caller's words, why no resamples
    were given (a study run with ``bootstrap_resamples: 0``, say), where the
    tail falls back to the fixed check for want of them.

    ``judge_the_bulk=False`` reads the reference off the tail without
    judging its shape: for each of the bootstrap's resamples, whose curve
    the real curve's verdict stands for. A resample judged on its own would
    be refused by its own noise, and the interval would be made of the
    resamples whose tails happened to be flattest.
    """
    radius = np.asarray(coordinate, dtype=float)
    energy = np.asarray(free_energy_kjmol, dtype=float)
    if radius.ndim != 1 or radius.shape != energy.shape:
        raise StudyError(
            "The coordinate and the free energy must be one-dimensional and "
            f"the same length; got {radius.shape} and {energy.shape}."
        , code="simulation.bias.dimension_mismatch")
    order = np.argsort(radius)
    radius, energy = radius[order], energy[order]
    if errors_kjmol is not None:
        errors = _array_of_numbers(errors_kjmol, "`errors_kjmol`", 1)
        if errors.shape != order.shape:
            raise StudyError(
                "The free energy's errors must be one per point of the "
                f"curve; got {errors.shape} for {order.shape}.",
                code="simulation.bias.dimension_mismatch")
        if np.any(errors[np.isfinite(errors)] < 0):
            raise StudyError(
                "A standard error is not negative; `errors_kjmol` holds "
                f"{float(np.nanmin(errors)):g}.",
                code="config.option.wrong_type")
    curves = None
    if resample_curves_kjmol is not None:
        curves = _array_of_numbers(
            resample_curves_kjmol, "`resample_curves_kjmol`", 2)
        if curves.shape[1:] != order.shape:
            raise StudyError(
                "Each resampled curve must have one free energy per point of "
                f"the curve; got {curves.shape[1:]} for {order.shape}.",
                code="simulation.bias.dimension_mismatch")
        curves = curves[:, order]

    if radius.size < 8:
        return {"delta_g_kjmol": None, "refused": (
            f"A binding free energy is an integral over the bound range, and "
            f"{radius.size} points is too few to take one."
        )}
    if np.any(radius <= 0):
        return {"delta_g_kjmol": None, "refused": (
            "The coordinate reaches zero or below. This treats it as a "
            "centre-to-centre distance, whose volume element is 4 pi r^2, "
            "and that is not defined at the origin."
        )}

    kt = KB_KJMOL * float(temperature_K)

    # Has the run reached bulk? Judged on the outer part of the range.
    n_bulk = max(4, int(radius.size * bulk_fraction))

    # A bin no sample reached is unknown, not a number. Read as NaN it made
    # the bulk check pass (NaN is not above any limit), put the minimum on
    # the hole, and ended in "no population above the bulk reference" for a
    # run whose only fault was an empty bin -- or one that stopped short of
    # bulk. Where it matters it is said; elsewhere it is left out.
    known = np.isfinite(energy)
    if not known.any():
        return {"delta_g_kjmol": None, "refused": (
            "No bin of the curve holds a free energy, so there is nothing to "
            "integrate.")}
    if not known[-n_bulk:].all():
        empty = radius[-n_bulk:][~known[-n_bulk:]]
        return {
            "delta_g_kjmol": None,
            "refused": (
                f"{empty.size} of the outer {n_bulk} bins, the range the bulk "
                f"reference is read from (beyond {radius[-n_bulk]:.3g} nm), "
                "hold no samples (at "
                + ", ".join(f"{r:.3g}" for r in empty) + " nm). Whether the "
                "ligand is free there cannot be checked across a gap, and the "
                "reference cannot be read through one. More sampling in the "
                "windows that cover it, or a window placed in it, closes it."
            ),
        }
    if judge_the_bulk:
        bulk = _judge_the_bulk(
            radius[-n_bulk:], energy[-n_bulk:],
            None if curves is None else curves[:, -n_bulk:], kt,
            why_fixed=(
                f", since {bulk_fixed_because}"
                if bulk_fixed_because and curves is None else
                ", since the bootstrap's per-bin errors were passed without "
                "its resampled curves, and each per-bin error holds the "
                "noise of the bin the curve is zeroed at"
                if errors_kjmol is not None else
                ", since the bootstrap's resampled curves were not passed"))
        judged_record = {
            "bulk_residual_kjmol": bulk["residual"],
            "bulk_worst_at_nm": bulk["worst_at_nm"],
            "bulk_allowed_kjmol": bulk["allowed_kjmol"],
            "bulk_tilt_kjmol": bulk["tilt_kjmol"],
            "bulk_tilt_error_kjmol": bulk["tilt_error_kjmol"],
            "bulk_tilt_allowed_kjmol": bulk["tilt_allowed_kjmol"],
            "bulk_judged_by": bulk["by"],
            "bulk_resamples_used": bulk["resamples_used"],
            "bulk_error_multiplier": bulk["multiplier"],
            "bulk_tilt_multiplier": bulk["tilt_multiplier"],
            "bulk_judged": bulk["judged"],
        }
    else:
        bulk = {"passed": True}
        judged_record = {
            "bulk_judged_by": None,
            "bulk_judged": ("not judged here: the curve this resample is of "
                            "was judged once, and its verdict stands"),
        }
    if not bulk["passed"]:
        found = []
        if bulk["departs"]:
            found.append(
                f"at {bulk['worst_at_nm']:.3g} nm by "
                f"{bulk['worst_kjmol']:.2f} kJ/mol, against "
                f"{bulk['allowed_kjmol']:.2f} allowed there")
        if bulk["tilted"]:
            found.append(
                f"across the tail by a tilt of {bulk['tilt_kjmol']:.2f} "
                f"kJ/mol, against {bulk['tilt_allowed_kjmol']:.2f} allowed")
        return {
            "delta_g_kjmol": None,
            **judged_record,
            "bulk_from_nm": float(radius[-n_bulk]),
            "refused": (
                f"The outer {n_bulk} points depart from a free ligand's "
                f"shape: {'; and '.join(found)}. Judged: "
                f"{bulk['judged']}. Beyond the interaction the "
                "curve should fall as -2kT ln r, because the only thing "
                "changing there is how much room the shell holds. Where it "
                "does not, the windows stopped while the ligand was still "
                "being held, and there is no reference state to define binding "
                "against: the run would still give a smooth curve and a "
                "plausible number, wrong by however much of the well was "
                "left outside. Extend the range until the tail has that "
                "shape"
                + (", or, since the allowance here was set by the noise and "
                   f"is above {BULK_RESIDUAL_KJMOL} kJ/mol, sample the outer "
                   "windows longer: a tail in bulk is refused by its noise "
                   f"alone about {BULK_FAMILY_CHANCE:.0%} of the time."
                   if max(bulk["allowed_kjmol"],
                          bulk["tilt_allowed_kjmol"] or 0.0)
                   > BULK_RESIDUAL_KJMOL else ".")
            ),
        }

    # The bulk reference, taken as the mean of the shape-corrected tail so
    # it does not rest on the single outermost point.
    tail = energy[-n_bulk:] + 2.0 * kt * np.log(radius[-n_bulk:])
    reference_at = float(np.mean(tail))

    def integrate(cutoff: float) -> float:
        inside = (radius <= cutoff) & known
        if inside.sum() < 3:
            return float("nan")
        # Referenced to the bulk constant, not to the bulk curve. The shell's
        # r^2 cancels against the r^-2 already carried inside A, which is why
        # this integrand has no explicit Jacobian and why writing one in
        # would count the translational entropy twice. Doing exactly that
        # put a square well 10.5 kJ/mol away from its closed form.
        shifted = energy[inside] - reference_at
        return float(np.trapezoid(np.exp(-shifted / kt), radius[inside]))

    minimum_at = float(radius[int(np.nanargmin(energy))])
    bulk_level = reference_at - 2.0 * kt * np.log(radius)
    if bound_cutoff_nm is None:
        beyond = known & (radius > minimum_at) & (energy >= bulk_level - kt)
        chosen = float(radius[np.argmax(beyond)]) if beyond.any() else float(
            radius[-n_bulk])
    else:
        chosen = float(bound_cutoff_nm)

    # An empty bin where the curve on both sides of it stands well above its
    # minimum -- at the wall the ligand cannot enter -- holds no population
    # the integral would notice, and is taken as holding none. One in the
    # well is population nobody measured.
    lowest = float(np.nanmin(energy))
    holes = []
    for index in np.flatnonzero((radius <= chosen) & ~known):
        below = energy[:index][known[:index]]
        above = energy[index + 1:][known[index + 1:]]
        sides = [part[-1] for part in (below,) if part.size] + \
            [part[0] for part in (above,) if part.size]
        if not sides or min(sides) - lowest < UNPOPULATED_ABOVE_KT * kt:
            holes.append(float(radius[index]))
    holes = np.asarray(holes)
    if holes.size:
        return {
            "delta_g_kjmol": None,
            **judged_record,
            "bulk_from_nm": float(radius[-n_bulk]),
            "minimum_at_nm": minimum_at,
            "bound_cutoff_nm": chosen,
            "refused": (
                f"{holes.size} bin(s) inside the bound range (up to "
                f"{chosen:.3g} nm) hold no samples (at "
                + ", ".join(f"{r:.3g}" for r in holes) + " nm). The binding "
                "constant is an integral over that range, and a gap in it is "
                "population nobody measured. More sampling in the windows "
                "that cover it, or a window placed in it, closes it."
            ),
        }

    reference_shell = 4.0 * np.pi
    integral = integrate(chosen)
    if not np.isfinite(integral) or integral <= 0:
        return {"delta_g_kjmol": None, **judged_record,
                "bulk_from_nm": float(radius[-n_bulk]),
                "minimum_at_nm": minimum_at, "bound_cutoff_nm": chosen,
                "refused": (
            f"The bound range up to {chosen:.3g} nm holds no population "
            "above the bulk reference, so there is no bound state to report "
            "a free energy for."
        )}

    constant_nm3 = reference_shell * integral
    delta_g = -kt * float(np.log(constant_nm3 / STANDARD_VOLUME_NM3))

    # The cap the windows ran in, and what it costs.
    cone_record, correction = _what_the_cone_costs(cone, temperature_K)
    wall = _the_wall_up_to(wall_bias_kjmol, chosen)
    if cone_record is None:
        # Nothing was judged without a cone, so nothing is said about a wall.
        wall = {key: None for key in wall}
    else:
        wall_record = {
            "cone": cone_record, "cone_correction_kjmol": correction,
            **judged_record,
            "bulk_from_nm": float(radius[-n_bulk]),
            "minimum_at_nm": minimum_at, "bound_cutoff_nm": chosen,
            "standard_volume_nm3": STANDARD_VOLUME_NM3,
            **_the_wall_as_saved(wall),
        }
        widen = (
            "Widen `half_angle_deg` (or `margin`, for a cone worked out from "
            "the pull) until the wall is quiet there, or point "
            "the axis along the direction the ligand actually leaves by. "
            "Every window then runs again under the new cone, not only "
            "those named, since the windows of one study share one cone: "
            "`fastmdx explore -c <config> --force-overwrite` with the new "
            "setting in the config. `--rerun-window` cannot do it.")
        def _windows(numbers: "list[int]") -> str:
            return ("window" + ("s " if len(numbers) > 1 else " ")
                    + ", ".join(str(n) for n in numbers))

        if wall["unreadable"]:
            return {"delta_g_kjmol": None, **wall_record, "refused": (
                "The cone's wall bias given is not a number, so whether the "
                "wall bit where the bound state is cannot be told."
                if wall["scalar"] else
                f"The cone's wall could not be read in {_windows(wall['not_read'])}, "
                f"which sampled the bound range (up to {chosen:.3g} nm): no "
                "bias was read on its frames there. The correction for running inside "
                "a cone assumes the bound pose fits within it, and a wall "
                "nobody could read may have bitten. Run those windows again, "
                "or check the `cone.bias` column of their COLVAR files."
            )}
        if wall["none_inside"]:
            return {"delta_g_kjmol": None, **wall_record, "refused": (
                "No window sampled the bound range (up to "
                f"{chosen:.3g} nm), so whether the cone's wall bit there "
                "cannot be told: the bound state was reached only by the "
                "tails of windows held outside it. Place windows in the well."
            )}
        if wall["bias_kjmol"] is not None and wall["bias_kjmol"] > WALL_QUIET_KJMOL:
            bit = wall["bit"]
            where = (
                ", in " + _windows([b["window"] for b in bit]) + " ("
                + ", ".join(f"at {b['centre']:.3g} nm" for b in bit) + ")"
                + f"; every window that sampled the bound range (up to "
                f"{chosen:.3g} nm) was judged"
            ) if bit else ""
            return {"delta_g_kjmol": None, **wall_record, "refused": (
                ("The cone's wall gave an infinite bias (a frame it gave no "
                 "finite energy)" if np.isinf(wall["bias_kjmol"]) else
                 f"The cone's wall carried {wall['bias_kjmol']:.2f} kJ/mol")
                + " where the bound state is"
                + (" (over the frames at or inside the cutoff)"
                   if any(b["frames_inside"] for b in bit or ()) else "")
                + f", against {WALL_QUIET_KJMOL} allowed{where}. The "
                "correction for running inside a cone assumes the bound pose "
                "fits within it: the bulk state loses "
                f"{correction:.2f} kJ/mol of room and the bound state loses "
                "none. A wall that bites in the bound state has taken part of "
                "the population this integral is over, and no analytic term "
                "puts it back. " + widen
            )}
        delta_g += correction

    # What the cutoff is doing. A deep narrow well barely moves; a shallow
    # one moves a lot, and the reader is owed that difference.
    span = [c for c in (chosen * 0.8, chosen, chosen * 1.25)
            if c < radius[-1]]
    spread = []
    for cutoff in span:
        value = integrate(cutoff)
        if np.isfinite(value) and value > 0:
            spread.append(
                -kt * float(np.log(reference_shell * value
                                   / STANDARD_VOLUME_NM3)))

    return {
        "delta_g_kjmol": delta_g,
        "delta_g_kcalmol": delta_g / 4.184,
        # The statistical uncertainty, where the caller measured one. It comes
        # from resampling the windows and recomputing this whole quantity, so
        # it is the spread of the measurement rather than a propagated
        # derivative. `cutoff_sensitivity_kjmol` below is a different thing
        # entirely -- how much the answer moves across defensible choices of
        # where the bound state ends -- and reporting one in place of the
        # other would be reporting a systematic as though it were noise.
        **_what_the_resamples_said(resampled, correction),
        # Both, always, where a cone was used. The uncorrected number is what
        # the curve says and the corrected one is what it means, and a reader
        # checking the arithmetic needs to see the step rather than take it.
        "delta_g_before_the_cone_kjmol": (
            delta_g - correction if cone_record is not None else None),
        "cone": cone_record,
        "cone_correction_kjmol": correction if cone_record is not None else None,
        **{key: value for key, value in _the_wall_as_saved(wall).items()
           if key not in ("cone_wall_checked_up_to_nm", "cone_wall_windows",
                          "cone_wall_bit_in", "cone_wall_not_read_in",
                          "cone_wall_judged")},
        "cone_wall_checked": (
            None if cone_record is None else wall["bias_kjmol"] is not None),
        # Where the wall was judged: in every window that sampled the range
        # up to the cutoff the integral used when it came per window, unknown
        # (None) when it came as one number.
        "cone_wall_checked_up_to_nm": wall["up_to_nm"],
        "cone_wall_windows": wall["windows"],
        "cone_wall_bit_in": _the_wall_as_saved(wall)["cone_wall_bit_in"],
        # Windows that sampled the bound range whose bias was not a number.
        # Never read as quiet: a number is refused while any is listed.
        "cone_wall_not_read_in": wall["not_read"],
        # Each judged window: its mean bias over its frames in the bound
        # range, how many frames that was and how many had no bias read.
        "cone_wall_judged": _the_wall_as_saved(wall)["cone_wall_judged"],
        "binding_constant_nm3": constant_nm3,
        "bound_cutoff_nm": chosen,
        "bound_cutoff_chosen_by": (
            "given" if bound_cutoff_nm is not None else
            "the first point beyond the minimum within kT of bulk"),
        "cutoff_sensitivity_kjmol": (
            float(np.max(spread) - np.min(spread)) if len(spread) > 1
            else None),
        **judged_record,
        # Where the range taken as bulk begins: the reference is read beyond
        # it, and what is checked about the reference is checked there.
        "bulk_from_nm": float(radius[-n_bulk]),
        "minimum_at_nm": minimum_at,
        "standard_volume_nm3": STANDARD_VOLUME_NM3,
        "convention": (
            "The free energy is treated as that of the radial distribution, "
            "in which the 4 pi r^2 volume element is already present; the "
            "integrand therefore carries no explicit Jacobian. Applying this "
            "expression to a PMF with the Jacobian removed counts the "
            "translational entropy twice."
        ),
        "refused": None,
    }
