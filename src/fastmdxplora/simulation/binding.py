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
#: kind resolves. Where the curve comes with its bootstrap errors this is
#: the floor of the allowance, not the whole of it.
BULK_RESIDUAL_KJMOL = 0.6

#: The chance, over all the points of the tail together, that a curve truly
#: in bulk is refused because its noise put one point past its allowance.
BULK_FAMILY_CHANCE = 0.05


def _sidak_multiplier(count: int, chance: float = BULK_FAMILY_CHANCE) -> float:
    """How many standard errors each of `count` points may depart by.

    Sidak's bound: each point is allowed z standard errors, with z the
    two-sided normal quantile at 1 - (1 - chance)^(1/count), so that the
    chance of any of the `count` departing that far by noise alone is
    `chance` where the departures are independent and normal. Bins of a
    recombined curve are correlated, so the bound is approximate here; for
    two-sided tests on correlated normal variables it errs towards allowing
    more (Sidak 1967, J. Am. Stat. Assoc. 62, 626).
    """
    from statistics import NormalDist

    each = 1.0 - (1.0 - chance) ** (1.0 / max(1, int(count)))
    return float(NormalDist().inv_cdf(1.0 - each / 2.0))


def _judge_the_bulk(radius: np.ndarray, energy: np.ndarray,
                    errors: "np.ndarray | None", kt: float) -> dict[str, Any]:
    """Whether the tail is flat in A(r) + 2kT ln r, judged against its errors.

    In bulk f(r) = A(r) + 2kT ln r is a constant c (Gilson et al. 1997,
    Biophys. J. 72, 1047: the standard-state integral with W(r) flat). Each
    tail point's departure d_i = f_i - mean(f) is judged against

        allowed_i = max(0.6 kJ/mol, z_n * sqrt(e_i^2 + e_mean^2)),
        e_mean = sqrt(sum_j e_j^2) / n,

    with e_i the bootstrap's standard error of A at that bin (2kT ln r is
    exact and adds none), e_mean the tail mean's error, both taken as if the
    bins were independent, and z_n Sidak's multiplier for n points
    (`_sidak_multiplier`). Two things make this allowance larger than the
    departure's own error rather than smaller. The covariance of f_i with the
    mean it is a part of is left out, which would take 2 e_i^2 / n off the
    variance. And the curve is zeroed at its lowest bin, so every e_i holds
    that bin's noise, which is common to the whole tail and cancels in d_i.
    A tail is therefore refused only where it departs past what its noise
    could make, and a smooth tail within its errors is taken as bulk because
    nothing in the run can tell it from bulk.

    Without errors, or with any tail bin's error missing, every point is
    judged against 0.6 kJ/mol alone, as before the errors were taken.
    """
    flattened = energy + 2.0 * kt * np.log(radius)
    departure = np.abs(flattened - np.mean(flattened))
    count = int(radius.size)
    floor = np.full(count, BULK_RESIDUAL_KJMOL)
    usable = (errors is not None and errors.shape == radius.shape
              and bool(np.all(np.isfinite(errors))))
    if usable:
        multiplier = _sidak_multiplier(count)
        of_the_mean = float(np.sqrt(np.sum(errors ** 2))) / count
        own = np.sqrt(errors ** 2 + of_the_mean ** 2)
        allowed = np.maximum(floor, multiplier * own)
        judged = (
            f"each of the outer {count} points' departure from their mean "
            f"against the larger of {BULK_RESIDUAL_KJMOL} kJ/mol and "
            f"{multiplier:.2f} standard errors of that departure, from the "
            f"bootstrap's error at each bin (a {BULK_FAMILY_CHANCE:.0%} "
            f"chance, over the {count} points, of refusing a tail that is "
            "bulk)")
    else:
        multiplier = None
        allowed = floor
        judged = (
            f"each of the outer {count} points' departure from their mean "
            f"against {BULK_RESIDUAL_KJMOL} kJ/mol"
            + (", since the bootstrap's errors were not given for every "
               "point of it" if errors is not None else
               ", since the curve came without the bootstrap's errors"))
    worst = int(np.argmax(departure - allowed))
    return {
        "residual": float(np.max(departure)),
        "passed": bool(np.all(departure <= allowed)),
        "worst_at_nm": float(radius[worst]),
        "worst_kjmol": float(departure[worst]),
        "allowed_kjmol": float(allowed[worst]),
        "against_errors": usable,
        "multiplier": multiplier,
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
        "delta_g_resamples": resampled.get("resamples"),
    }


def _the_wall_up_to(wall_bias_kjmol: Any, cutoff: float) -> dict[str, Any]:
    """The wall's bias in the windows the bound state spans, judged up to
    the cutoff the integral uses.

    Per window (``[{"window", "centre", "bias_kjmol"}, ...]``), every window
    whose centre is at or inside ``cutoff`` is judged and its largest mean is
    the number. A window beyond it is left out: its centre is outside the
    range the integral is taken over. One number is the caller's own reading
    and is taken as it stands, with the range it covers unknown.
    """
    if wall_bias_kjmol is None:
        return {"bias_kjmol": None, "up_to_nm": None, "windows": None,
                "bit": None}
    if isinstance(wall_bias_kjmol, (int, float, np.floating)):
        return {"bias_kjmol": float(wall_bias_kjmol), "up_to_nm": None,
                "windows": None, "bit": None}
    rows = [{"window": int(row["window"]), "centre": float(row["centre"]),
             "bias_kjmol": float(row["bias_kjmol"])}
            for row in wall_bias_kjmol]
    inside = [row for row in rows
              if row["centre"] <= cutoff * (1.0 + 1e-9)]
    if not inside:
        return {"bias_kjmol": None, "up_to_nm": float(cutoff), "windows": [],
                "bit": []}
    return {
        "bias_kjmol": max(row["bias_kjmol"] for row in inside),
        "up_to_nm": float(cutoff),
        "windows": [row["window"] for row in inside],
        "bit": [row for row in inside if row["bias_kjmol"] > WALL_QUIET_KJMOL],
    }


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
    it (a list of ``{"window", "centre", "bias_kjmol"}``): then every window
    whose centre is at or inside the cutoff the integral uses is judged, and
    those that bit are named. A single number is still accepted, as the
    largest mean in windows the caller chose, and is judged as it stands.
    Given nothing, it says the check was not made rather than implying it
    passed.

    ``errors_kjmol`` is the bootstrap's standard error of the free energy at
    each point (``pmf["uncertainty"]["standard_error"]``). Given, the bulk
    check allows each point of the tail what its noise could make, with
    0.6 kJ/mol as the floor (`_judge_the_bulk`); not given, 0.6 kJ/mol is
    the whole allowance. The record says which was judged.
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
    errors = None
    if errors_kjmol is not None:
        errors = np.asarray(
            [np.nan if e is None else e for e in errors_kjmol], dtype=float)
        if errors.shape != order.shape:
            raise StudyError(
                "The free energy's errors must be one per point of the "
                f"curve; got {errors.shape} for {order.shape}.",
                code="simulation.bias.dimension_mismatch")
        errors = errors[order]

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
    bulk = _judge_the_bulk(radius[-n_bulk:], energy[-n_bulk:],
                           None if errors is None else errors[-n_bulk:], kt)
    residual = bulk["residual"]
    judged_record = {
        "bulk_residual_kjmol": residual,
        "bulk_worst_at_nm": bulk["worst_at_nm"],
        "bulk_allowed_kjmol": bulk["allowed_kjmol"],
        "bulk_judged_against_errors": bulk["against_errors"],
        "bulk_error_multiplier": bulk["multiplier"],
        "bulk_judged": bulk["judged"],
    }
    if not bulk["passed"]:
        return {
            "delta_g_kjmol": None,
            **judged_record,
            "bulk_from_nm": float(radius[-n_bulk]),
            "refused": (
                f"The outer {n_bulk} points depart from a free ligand's "
                f"shape: at {bulk['worst_at_nm']:.3g} nm by "
                f"{bulk['worst_kjmol']:.2f} kJ/mol, against "
                f"{bulk['allowed_kjmol']:.2f} allowed there. Judged: "
                f"{bulk['judged']}. Beyond the interaction the "
                "curve should fall as -2kT ln r, because the only thing "
                "changing there is how much room the shell holds. Where it "
                "does not, the windows stopped while the ligand was still "
                "being held, and there is no reference state to define binding "
                "against: the run would still give a smooth curve and a "
                "plausible number, wrong by however much of the well was "
                "left outside. Extend the range until the tail has that "
                "shape."
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
        return {"delta_g_kjmol": None, "refused": (
            f"The bound range up to {chosen:.3g} nm holds no population "
            "above the bulk reference, so there is no bound state to report "
            "a free energy for."
        )}

    constant_nm3 = reference_shell * integral
    delta_g = -kt * float(np.log(constant_nm3 / STANDARD_VOLUME_NM3))

    # The cap the windows ran in, and what it costs.
    cone_record, correction = _what_the_cone_costs(cone, temperature_K)
    wall = _the_wall_up_to(wall_bias_kjmol, chosen)
    if cone_record is not None:
        if wall["bias_kjmol"] is not None and wall["bias_kjmol"] > WALL_QUIET_KJMOL:
            bit = wall["bit"]
            where = (
                " in window" + ("s " if len(bit) > 1 else " ")
                + ", ".join(f"{b['window']} (at {b['centre']:.3g} nm, "
                            f"{b['bias_kjmol']:.2f} kJ/mol)" for b in bit)
                + f", every window up to the cutoff of {chosen:.3g} nm having "
                "been judged"
            ) if bit else ""
            return {"delta_g_kjmol": None, "cone": cone_record,
                    "bound_cutoff_nm": chosen,
                    "cone_wall_bias_kjmol": wall["bias_kjmol"],
                    "cone_wall_checked_up_to_nm": wall["up_to_nm"],
                    "cone_wall_windows": wall["windows"],
                    "cone_wall_bit_in": bit,
                    "refused": (
                f"The cone's wall carried {wall['bias_kjmol']:.2f} kJ/mol where "
                f"the bound state is{where}, against {WALL_QUIET_KJMOL} "
                "allowed. The "
                "correction for running inside a cone assumes the bound pose "
                "fits within it: the bulk state loses "
                f"{correction:.2f} kJ/mol of room and the bound state loses "
                "none. A wall that bites in the bound state has taken part of "
                "the population this integral is over, and no analytic term "
                "puts it back. Widen `half_angle_deg` until the wall is quiet "
                "there, or point the axis along the direction the ligand "
                "actually leaves by."
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
        "cone_wall_bias_kjmol": wall["bias_kjmol"],
        "cone_wall_checked": (
            None if cone_record is None else wall["bias_kjmol"] is not None),
        # Where the wall was judged: up to the cutoff the integral used when
        # it came per window, unknown (None) when it came as one number.
        "cone_wall_checked_up_to_nm": wall["up_to_nm"],
        "cone_wall_windows": wall["windows"],
        "cone_wall_bit_in": wall["bit"],
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
