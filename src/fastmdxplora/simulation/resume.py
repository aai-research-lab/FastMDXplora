"""Whether a run may be stopped and picked up again, and where it may not.

A hundred nanoseconds submitted as ten ten-nanosecond pieces is the
difference, on one card, between finishing three candidates in a week and
finishing one. It is also, for some studies, a way of producing a
confidently wrong answer.

The reasoning is already written in ``_attach_checkpoint_reporter``, which
explains why this software has never had a ``--resume``. A checkpoint
brings back positions and velocities. It does not bring back the biasing
state, and for two methods that is the whole of the calculation:

**Metadynamics.** PLUMED does not re-read ``HILLS`` unless its script says
``RESTART``. A metadynamics run resumed without it begins again from zero
bias with the system sitting in a well it has already filled, and produces
a free energy surface that is wrong without saying so. That last clause is
what makes this worth a refusal rather than a warning: nothing downstream
looks different. The surface is smooth, the plot is a plot, and the depth
is wrong.

**Steered dynamics.** The moving restraint is placed by absolute step
number. Resuming mid-pull puts the anchor somewhere the protein is not,
and the work integral -- which is the measurement -- is taken along a path
that was never walked.

Two are safe. An unbiased run carries no state beyond positions and
velocities. An umbrella window's restraint is a function of the coordinate
and not of time, so a window that stops at 4 ns and continues is doing the
same thing it was doing before.

So this says which a study is, and refuses rather than guessing. A caller
that wants segments for a metadynamics run is not told to try harder; it
is told the run has to go through in one piece, which is a fact about the
method and not a limitation of this software.

And where a run does resume, that is recorded. A trajectory assembled from
two pieces is not the same object as one that ran through -- the join is a
place where the thermostat was re-seeded and the reporters restarted -- and
the analyses that read equilibration and correlation should be able to see
it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastmdxplora.refusals import Refusal, StudyError

__all__ = [
    "Segmentability",
    "segmentability",
    "require_segmentable",
    "resume_provenance",
]


@dataclass(frozen=True)
class Segmentability:
    """Whether a study may be run in pieces, and why.

    ``reason`` is filled whether or not the answer is yes, because "an
    unbiased run carries no state beyond positions and velocities" is
    worth reading too. A verdict that only explains itself when it says no
    trains a reader to skip it.
    """

    allowed: bool
    method: str
    reason: str
    code: str = ""

    def as_record(self) -> dict[str, Any]:
        record = {"segmentable": self.allowed, "method": self.method,
                  "reason": self.reason}
        if self.code:
            record["code"] = self.code
        return record

    def __str__(self) -> str:
        return self.reason


def segmentability(config: dict[str, Any] | None) -> Segmentability:
    """Read a study and say whether it may be split.

    Reads the config rather than the running simulation, so a caller can
    ask before committing anything -- which is the point. Discovering at
    the join that a run should not have been split means the first
    segment's hours are already spent.
    """
    block = (config or {}).get("simulation") or {}

    if block.get("metadynamics"):
        return Segmentability(
            False, "metadynamics",
            "A metadynamics run cannot be split. The deposited bias lives "
            "in PLUMED's state, and a checkpoint does not carry it: the "
            "second piece would begin from zero bias with the system in a "
            "well it had already filled, and the free energy surface would "
            "be wrong without looking wrong. Run it through in one piece.",
            code="simulation.resume.bias_not_carried")

    if block.get("steered"):
        return Segmentability(
            False, "steered",
            "A steered run cannot be split. The moving restraint is placed "
            "by absolute step number, so the second piece would pull from "
            "an anchor the protein is not at, and the work integral -- "
            "which is the measurement -- would be taken along a path "
            "nothing walked. Run it through in one piece.",
            code="simulation.resume.time_dependent_bias")

    if block.get("plumed"):
        # A hand-written PLUMED script may do anything, including deposit
        # bias. The software cannot read the script's intent, and guessing
        # in the permissive direction is the expensive way to be wrong.
        return Segmentability(
            False, "plumed",
            "A run driven by a hand-written PLUMED script cannot be split, "
            "because what state that script keeps is not something this "
            "software can read. Where the script is genuinely stateless, "
            "say so by splitting the study yourself into runs that each "
            "stand alone.",
            code="simulation.resume.bias_not_carried")

    if block.get("umbrella"):
        return Segmentability(
            True, "umbrella",
            "An umbrella window may be split. Its restraint is a function "
            "of the collective variable and not of time, so a window that "
            "stops and continues is doing what it was doing before.")

    return Segmentability(
        True, "unbiased",
        "An unbiased run may be split. It carries no state beyond "
        "positions and velocities, which a checkpoint restores.")


def require_segmentable(config: dict[str, Any] | None, *,
                        segments: int) -> Segmentability:
    """The same question, raising where the answer is no.

    For a caller that has already decided to split and wants the refusal
    rather than the verdict. ``segments <= 1`` always passes: a study run
    in one piece is not being split, whatever method it uses.
    """
    verdict = segmentability(config)
    if segments <= 1 or verdict.allowed:
        return verdict
    raise StudyError(verdict.reason, code=verdict.code,
                     method=verdict.method, segments=segments)


def resume_provenance(previous: dict[str, Any] | None, *, segment: int,
                      of_segments: int, from_step: int,
                      checkpoint: str = "") -> dict[str, Any]:
    """What the manifest should say about a run that was picked up.

    A trajectory assembled from pieces is not the same object as one that
    ran through. The join is a place where reporters restarted and, on
    some integrators, where the thermostat was re-seeded. An analysis
    reading equilibration or correlation across that point is reading
    across a discontinuity, and it should be able to know that rather
    than have to infer it from a step count that looks continuous.

    So the record accumulates: each segment appends where it started, and
    the finished run carries the whole list of joins.
    """
    joins = list((previous or {}).get("joins") or [])
    if segment > 0:
        joins.append({"segment": segment, "from_step": from_step,
                      "checkpoint": checkpoint})
    return {
        "segments": of_segments,
        "segment": segment,
        "joins": joins,
        # Stated rather than left to be worked out from len(joins), because
        # the thing an analysis wants to test is "did this run through",
        # and that should not require arithmetic.
        "ran_through": not joins,
    }


def refusal_for(verdict: Segmentability) -> Refusal | None:
    """The verdict as a refusal, for a caller recording rather than raising."""
    if verdict.allowed:
        return None
    return Refusal(code=verdict.code, message=verdict.reason,
                   details={"method": verdict.method})
