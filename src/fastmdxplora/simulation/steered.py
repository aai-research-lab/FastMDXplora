"""Pulling a system along a coordinate, and what that does and does not give.

Some things do not happen on their own within reach of a simulation. A ligand
may take milliseconds to leave a pocket where a run lasts microseconds. Steered
molecular dynamics attaches a spring to a collective variable and moves the
spring's anchor, dragging the system along whether or not it wants to go.

**What this gives you is a pathway, not a free energy.** The work done pulling
depends on how fast you pull: drag a ligand out in a nanosecond and most of the
work goes into pushing water aside and straining the protein, not into
breaking the interactions you meant to measure. That dissipated work does not
cancel, and a single fast pull overestimates the barrier, sometimes by a lot.

Jarzynski's equality recovers a free energy from an ensemble of pulls, and the
average is dominated by the rare low-work trajectories -- so it needs many
repeats and converges badly when the pulling is fast. FastMDXplora does not
claim a free energy from a steered run, and reports the work so the claim can
be made deliberately by somebody who has done the repeats.

**What steered MD is genuinely good for is generating starting structures.**
Pull once, take frames along the way, and each becomes a window for umbrella
sampling -- which does give a free energy, from equilibrium sampling at each
position rather than from work done in a hurry. That is the standard use, and
it is why this exists before umbrella sampling does.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import logging

import numpy as np

from fastmdxplora.simulation.metadynamics import (
    COLLECTIVE_VARIABLES,
    cv_lines,
    MetadynamicsPlan,
    plan_from_config,
)

logger = logging.getLogger(__name__)

__all__ = ["SteeredPlan", "plan_steered", "build_steered_script"]

#: A spring stiff enough to drag the system without the anchor running away
#: from it, in kJ/mol/nm². Softer and the coordinate lags behind the anchor;
#: much stiffer and the pull becomes a shove.
DEFAULT_FORCE = 2000.0


@dataclass(frozen=True)
class SteeredPlan:
    """A pull: from where, to where, how hard, and over how long."""

    #: The coordinate, resolved to atoms, reusing the metadynamics layer.
    cv: MetadynamicsPlan
    to_value: float
    from_value: float | None = None
    force_constant: float = DEFAULT_FORCE
    #: Steps over which the anchor travels. Everything about how much this
    #: run means depends on this being large.
    steps: int = 500000

    def rate_per_ns(self, timestep_fs: float) -> float | None:
        """How fast the anchor moves, in units of the variable per ns.

        The number that decides whether the work means anything. Reported
        rather than checked against a threshold, because what counts as slow
        depends on the coordinate and the system -- but an order of magnitude
        is usually obvious.
        """
        if self.from_value is None or not self.steps or not timestep_fs:
            return None
        duration_ns = self.steps * timestep_fs / 1e6
        if duration_ns <= 0:
            return None
        return abs(self.to_value - self.from_value) / duration_ns

    def as_record(self) -> dict[str, Any]:
        return {
            "collective_variable": self.cv.collective_variable,
            "what_it_separates": COLLECTIVE_VARIABLES.get(
                self.cv.collective_variable, ""),
            "from": self.from_value,
            "to": self.to_value,
            "force_constant": self.force_constant,
            "steps": self.steps,
            "gives": (
                "a pathway and the work done along it, not a free energy: "
                "the work depends on how fast the anchor moved, and a single "
                "pull overestimates a barrier"
            ),
        }


def plan_steered(
    spec: dict[str, Any],
    topology: Any,
    *,
    temperature_K: float = 300.0,
    ligand_resname: str | None = None,
    structure: Any = None,
) -> SteeredPlan:
    """Read a steered-MD block, reusing the collective-variable machinery."""
    if "to" not in spec:
        raise ValueError(
            "Steered MD needs a `to`: the value of the collective variable "
            "to pull towards. Without it there is no destination and nothing "
            "to steer."
        )

    # The same eight variables metadynamics offers, resolved the same way,
    # with the same refusals. A steered run does not need a hill width, so
    # sigma is supplied and ignored rather than demanded of the user.
    cv_spec = {k: v for k, v in spec.items()
               if k not in ("to", "from", "force_constant", "steps")}
    cv_spec.setdefault("sigma", 0.05)
    cv_spec.setdefault("unbounded", True)
    cv = plan_from_config(cv_spec, topology, temperature_K=temperature_K,
                          ligand_resname=ligand_resname)

    steps = int(spec.get("steps", 500000))
    if steps <= 0:
        raise ValueError("Steered MD needs a positive number of `steps`.")

    # Where the pull starts. Zero is not a default: it is a real position
    # for most coordinates, and anchoring there drags the system towards it
    # before the pull begins. But the value in the structure about to be
    # simulated is a default, and it is the one the old error message told
    # the user to go and measure by hand. Measuring is something this can
    # do, and a framework that asks for a number it is holding is asking
    # for the wrong reason.
    #
    # Refused here rather than where the script is written, because the
    # script is written after equilibration has already run: a missing
    # starting anchor should cost a config error, not four minutes of NVT.
    from_value = spec.get("from")
    if from_value is None and structure is not None:
        from_value = measure_in(cv, structure)
        if from_value is not None:
            # Named, because it is a measurement rather than a setting and
            # the structure it comes from is the one that exists when the
            # script is written -- before equilibration, not after. The
            # difference is small for a pull that travels a nanometre and a
            # half, and it is not nothing: state `from` to anchor elsewhere.
            logger.info(
                "Pull anchored at %.4f nm, measured in the structure the "
                "run starts from (%s), before equilibration. Set `from` to "
                "anchor it elsewhere.", from_value,
                getattr(structure, "__class__", type(structure)).__name__
                if not isinstance(structure, str) else structure)
    if from_value is None:
        raise ValueError(
            "A steered pull needs `from`: the value of the collective "
            f"variable ({cv.collective_variable}) where the run starts. "
            "It would ordinarily be read from the structure being "
            "simulated, and could not be here -- either no structure was "
            "available yet, or this variable is not one that can be "
            f"measured from coordinates alone ({cv.collective_variable} is "
            "not). Measure it and set `from`.")

    return SteeredPlan(
        cv=cv,
        to_value=float(spec["to"]),
        from_value=float(from_value),
        force_constant=float(spec.get("force_constant", DEFAULT_FORCE)),
        steps=steps,
    )


#: Variables whose value can be read straight from one set of coordinates.
#: A pull is along one of these in practice; the rest need a reference
#: structure, a contact map or a history, and are left to the user to state.
MEASURABLE = ("distance", "ligand_distance", "radius_of_gyration", "torsion")


def measure_in(cv: Any, structure: Any) -> float | None:
    """The variable's value in a structure, where it can be read from one.

    `structure` is anything MDTraj loads, or an already-loaded trajectory;
    the first frame is used. Returns ``None`` where the variable is not one
    of :data:`MEASURABLE`, so the caller can say so rather than guess.

    Centres are mass-weighted, which is what PLUMED's ``COM`` is. An
    unweighted centroid differs by a few hundredths of a nanometre on a
    ligand with a heavy ring -- small, and the whole point of this number
    is that the pull starts where the system already is.
    """
    if cv.collective_variable not in MEASURABLE:
        return None

    import mdtraj as md

    frame = structure if hasattr(structure, "xyz") else md.load(str(structure))
    frame = frame[0]
    groups = list(cv.atoms.values())

    if cv.collective_variable == "radius_of_gyration":
        selection = groups[0] if groups else None
        subset = frame.atom_slice(selection) if selection else frame
        return float(md.compute_rg(subset)[0])

    if cv.collective_variable == "torsion":
        indices = [i for group in groups for i in group]
        if len(indices) != 4:
            return None
        return float(md.compute_dihedrals(frame, [indices])[0][0])

    if len(groups) != 2:
        return None
    masses = np.array([a.element.mass for a in frame.topology.atoms],
                      dtype=float)

    def centre(selection):
        weight = masses[selection]
        return (frame.xyz[0][selection] * weight[:, None]).sum(axis=0) \
            / weight.sum()

    first, second = (np.asarray(g, dtype=int) for g in groups)
    delta = centre(first) - centre(second)
    # Reduced to the nearest image where the structure has a box. A prepared
    # structure usually has its molecules whole and in one image, so this
    # rarely changes the answer -- but "rarely" is not a reason to report a
    # distance across the box as the place a pull should start.
    if frame.unitcell_vectors is not None:
        from fastmdxplora.simulation.seeding import shortest_vector

        delta = shortest_vector(delta[None, :],
                                frame.unitcell_vectors[:1])[0]
    return float(np.linalg.norm(delta))


def build_steered_script(plan: SteeredPlan,
                         reference_pdb: str | None = None) -> str:
    """The PLUMED input for a pull.

    Written out rather than hidden, because the pulling rate is the thing
    that decides whether the result means anything and somebody checking a
    number should be able to read it.
    """
    variable = plan.cv.collective_variable
    lines = [
        "# Generated by FastMDXplora from a named collective variable.",
        f"# Pulling: {variable} -- {COLLECTIVE_VARIABLES[variable]}",
        "#",
        "# The work reported below depends on how fast the anchor moves. A",
        "# fast pull overestimates a barrier, because the work goes into",
        "# pushing solvent aside as well as into the interactions of",
        "# interest.",
        "",
    ]
    # The same translation metadynamics uses. This was a copy of it, so a
    # variable added there did not arrive here.
    lines.extend(cv_lines(plan.cv, reference_pdb))
    lines.append("")
    # A moving restraint travels from AT0 to AT1, and PLUMED has no way to
    # say "start wherever the system is" -- AT0 has to be a number. This
    # wrote a literal zero when `from` was not given, which is only harmless
    # for a coordinate whose natural value is zero and is nothing like
    # harmless otherwise: a real pull on the radius of gyration of a folded
    # protein began with the anchor at 0 nm against a system at 0.71 nm, so
    # a 2000 kJ/mol/nm^2 restraint spent the first half of the run hauling
    # the protein towards a collapsed state and the work came out negative.
    # Refused rather than guessed, because the guess was silent.
    if plan.from_value is None:
        raise ValueError(
            "A steered pull needs `from`: the value of the collective "
            "variable where the run starts. PLUMED's moving restraint has to "
            "be given a starting anchor, and there is no sensible default -- "
            "zero is a real position for most coordinates, and anchoring "
            "there drags the system towards it before the pull begins. "
            "Measure the variable in the structure being simulated and set "
            "`from` to it.")
    lines.append(
        "pull: MOVINGRESTRAINT ARG=cv "
        f"AT0={plan.from_value:g} STEP0=0 KAPPA0={plan.force_constant:g} "
        + f"AT1={plan.to_value:g} STEP1={plan.steps:d} "
        f"KAPPA1={plan.force_constant:g}"
    )
    lines.append("")
    # The work is the point of recording anything: without it a steered run
    # has produced a trajectory and no number.
    lines.append("PRINT ARG=cv,pull.work,pull.bias STRIDE=100 FILE=COLVAR")
    # Flushed as it goes. PLUMED buffers its output and writes on teardown,
    # and the work record is built at the end of the simulation phase --
    # before the force is finalised. COLVAR existed, held no rows, and the
    # record was silently not written; run by hand afterwards on the same
    # file it worked. A race rather than a logic error, which is why reading
    # the code found nothing.
    #
    # The cost is a flush every hundred steps, which is nothing beside the
    # dynamics, and it also means a pull can be watched while it runs rather
    # than only after it ends.
    lines.append("FLUSH STRIDE=100")
    return "\n".join(lines) + "\n"
