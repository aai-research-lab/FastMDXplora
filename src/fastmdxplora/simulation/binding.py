"""A binding free energy from a potential of mean force along a distance.

The well depth of a PMF is not a binding free energy. Turning one into the
other needs the standard state, because a dissociation constant is defined
against a reference concentration and a curve is not: at 1 M the ligand has
1661 cubic Angstroms to itself, and the free energy of binding is the cost
of giving that up. \\cite{gilson1997}

    K = 4 pi * integral over the bound range of exp(-beta [A(r) - c]) dr
    dG = -kT ln(K / V0),   V0 = 1661 A^3

with c the constant that A(r) + 2kT ln r settles to in bulk.

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

__all__ = ["STANDARD_VOLUME_NM3", "binding_free_energy"]

#: The volume one molecule has to itself at 1 mol/L, in nm^3. 1661 cubic
#: Angstroms, the standard state a dissociation constant is quoted against.
STANDARD_VOLUME_NM3 = 1.660539e0

#: Boltzmann's constant in kJ/(mol K).
KB_KJMOL = 0.008314462618

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
            raise ValueError(
                "A cone record needs `correction_kjmol`: what the bulk state "
                f"gave up by being confined to it. {sorted(cone)} was given.")
        return dict(cone), float(cone["correction_kjmol"])
    raise ValueError(
        "`cone` is the angular wall the windows ran under -- an "
        f"umbrella.Cone or the record one writes. {cone!r} was given.")


#: How well the outer range must follow the bulk form before it counts as
#: bulk. The residual of a fit of A(r) to -2kT ln r + c, in kJ/mol; a
#: quarter of RT at 300 K, which is smaller than any feature a PMF of this
#: kind resolves.
BULK_RESIDUAL_KJMOL = 0.6


def _bulk_residual(radius: np.ndarray, energy: np.ndarray,
                   kt: float) -> float:
    """How far the outer range departs from a free ligand's shape.

    In bulk the only thing varying with r is the size of the shell, so
    A(r) + 2kT ln r is constant. The residual of that combination is the
    evidence, and it needs no fitted slope: the slope is known.
    """
    flattened = energy + 2.0 * kt * np.log(radius)
    return float(np.max(np.abs(flattened - np.mean(flattened))))


def binding_free_energy(
    coordinate: Any,
    free_energy_kjmol: Any,
    *,
    temperature_K: float = 300.0,
    bound_cutoff_nm: float | None = None,
    bulk_fraction: float = 0.25,
    cone: Any = None,
    wall_bias_kjmol: float | None = None,
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

    ``wall_bias_kjmol`` is the mean bias the wall applied where the bound
    state is. The correction above rests on the cone not cutting the bound
    state, which is a measurement rather than an assumption: a wall that bit
    there has removed part of the bound population and no analytic term puts
    it back. Given the number, this checks it; given nothing, it says the
    check was not made rather than implying it passed.
    """
    radius = np.asarray(coordinate, dtype=float)
    energy = np.asarray(free_energy_kjmol, dtype=float)
    if radius.ndim != 1 or radius.shape != energy.shape:
        raise ValueError(
            "The coordinate and the free energy must be one-dimensional and "
            f"the same length; got {radius.shape} and {energy.shape}."
        )
    order = np.argsort(radius)
    radius, energy = radius[order], energy[order]

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
    residual = _bulk_residual(radius[-n_bulk:], energy[-n_bulk:], kt)
    if residual > BULK_RESIDUAL_KJMOL:
        return {
            "delta_g_kjmol": None,
            "bulk_residual_kjmol": residual,
            "refused": (
                f"The outer {n_bulk} points depart from a free ligand's "
                f"shape by {residual:.2f} kJ/mol, against "
                f"{BULK_RESIDUAL_KJMOL} allowed. Beyond the interaction the "
                "curve should fall as -2kT ln r, because the only thing "
                "changing there is how much room the shell holds. Where it "
                "does not, the windows stopped while the ligand was still "
                "being held, and there is no reference to measure binding "
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
        inside = radius <= cutoff
        if inside.sum() < 3:
            return float("nan")
        # Referenced to the bulk constant, not to the bulk curve. The shell's
        # r^2 cancels against the r^-2 already carried inside A, which is why
        # this integrand has no explicit Jacobian and why writing one in
        # would count the translational entropy twice. Doing exactly that
        # put a square well 10.5 kJ/mol away from its closed form.
        shifted = energy[inside] - reference_at
        return float(np.trapezoid(np.exp(-shifted / kt), radius[inside]))

    minimum_at = float(radius[int(np.argmin(energy))])
    bulk_level = reference_at - 2.0 * kt * np.log(radius)
    if bound_cutoff_nm is None:
        beyond = (radius > minimum_at) & (energy >= bulk_level - kt)
        chosen = float(radius[np.argmax(beyond)]) if beyond.any() else float(
            radius[-n_bulk])
    else:
        chosen = float(bound_cutoff_nm)

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
    if cone_record is not None:
        if wall_bias_kjmol is not None and wall_bias_kjmol > WALL_QUIET_KJMOL:
            return {"delta_g_kjmol": None, "cone": cone_record, "refused": (
                f"The cone's wall carried {wall_bias_kjmol:.2f} kJ/mol where "
                f"the bound state is, against {WALL_QUIET_KJMOL} allowed. The "
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
        # Both, always, where a cone was used. The uncorrected number is what
        # the curve says and the corrected one is what it means, and a reader
        # checking the arithmetic needs to see the step rather than take it.
        "delta_g_before_the_cone_kjmol": (
            delta_g - correction if cone_record is not None else None),
        "cone": cone_record,
        "cone_correction_kjmol": correction if cone_record is not None else None,
        "cone_wall_bias_kjmol": wall_bias_kjmol,
        "cone_wall_checked": (
            None if cone_record is None else wall_bias_kjmol is not None),
        "binding_constant_nm3": constant_nm3,
        "bound_cutoff_nm": chosen,
        "bound_cutoff_chosen_by": (
            "given" if bound_cutoff_nm is not None else
            "the first point beyond the minimum within kT of bulk"),
        "cutoff_sensitivity_kjmol": (
            float(np.max(spread) - np.min(spread)) if len(spread) > 1
            else None),
        "bulk_residual_kjmol": residual,
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
