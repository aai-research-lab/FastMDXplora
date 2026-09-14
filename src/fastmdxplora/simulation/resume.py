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
from typing import Any, Callable

from fastmdxplora.refusals import Refusal, StudyError

__all__ = [
    "Segment",
    "Segmentability",
    "plan_segments",
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
    qualification: str = ""
    """What is true of a split run that is not true of one that ran through.

    Distinct from ``reason``, and distinct from refusing. A qualification
    says the split is sound and something about the result is different
    anyway -- which is a third answer the corpus already has a name for,
    and the right one for a constant-pressure run.
    """

    def as_record(self) -> dict[str, Any]:
        record = {"segmentable": self.allowed, "method": self.method,
                  "reason": self.reason}
        if self.code:
            record["code"] = self.code
        if self.qualification:
            record["qualification"] = self.qualification
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

    # Measured rather than assumed, on the CPU platform with argon in a
    # periodic box. A constant-volume run resumed from a checkpoint
    # reproduces the run it continued to within 8e-8 nm. The same run at
    # constant pressure does not, and seeding the barostat does not fix
    # it: positions, velocities and box vectors all come back exactly, but
    # the Monte Carlo barostat's adaptive volume-move size is not in the
    # checkpoint and is not a Context parameter, so it restarts at its
    # default and re-adapts over the moves after the join.
    #
    # That is a qualification and not a refusal. The state the second
    # piece starts from is physically right, and the trajectory it
    # produces is a valid sample of the same ensemble -- it is simply not
    # the trajectory the unsplit run would have produced, and the
    # barostat's acceptance rate is off for a while at each join. Refusing
    # would refuse constant pressure, which is most work anybody does.
    # Saying nothing would leave a volume artefact for somebody to find.
    barostat = (block.get("pressure_bar") is not None
                or block.get("pressure_atm") is not None)
    qualification = (
        "The barostat's adaptive move size is not carried by a checkpoint, "
        "so it restarts at its default and re-adapts after each join. The "
        "state is right and the ensemble is right; the acceptance rate is "
        "off for a while, and volume or density averaged across a join "
        "carries that transient. Analyses that care should discard a "
        "window after each join, which `provenance['joins']` records."
        if barostat else "")

    if block.get("umbrella"):
        return Segmentability(
            True, "umbrella",
            "An umbrella window may be split. Its restraint is a function "
            "of the collective variable and not of time, so a window that "
            "stops and continues is doing what it was doing before.",
            qualification=qualification)

    return Segmentability(
        True, "unbiased",
        "An unbiased run may be split. It carries no state beyond "
        "positions and velocities, which a checkpoint restores.",
        qualification=qualification)


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
                      checkpoint: str = "",
                      qualification: str = "") -> dict[str, Any]:
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
        "qualification": qualification,
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


@dataclass(frozen=True)
class Segment:
    """One piece of a run, as a config and where it continues from.

    ``config`` is a whole study config, not a fragment: it goes through
    ``validate_config`` like any other and runs through the same
    orchestrator. A segment is an ordinary study that happens to start
    somewhere.
    """

    index: int
    of_segments: int
    config: dict[str, Any]
    resume_from: str | None
    steps: int

    @property
    def is_first(self) -> bool:
        return self.index == 0

    def as_record(self) -> dict[str, Any]:
        return {"segment": self.index, "of_segments": self.of_segments,
                "steps": self.steps, "resume_from": self.resume_from}


def _production_steps(block: dict[str, Any]) -> int:
    """How many production steps a config asks for, defaults included."""
    if block.get("production_steps") is not None:
        return int(block["production_steps"])
    if block.get("duration_ns") is not None:
        timestep = float(block.get("timestep_fs") or 2.0)
        return int(float(block["duration_ns"]) * 1e6 / timestep)
    return 1_000_000


def plan_segments(
    config: dict[str, Any],
    *,
    segments: int,
    checkpoint_name: str = "checkpoint.chk",
    output_dir_for: "Callable[[int], str] | None" = None,
) -> list[Segment]:
    """Split a study into pieces that add up to the study.

    Three things are decided here rather than left to a caller, because
    getting any of them wrong produces a run that looks finished and is
    not.

    **Equilibration happens once.** Only the first segment minimises and
    equilibrates. The rest set ``simulation.minimize: false`` and zero the
    NVT and NPT counts, because a segment that re-equilibrated would throw away
    the production it was supposed to continue, and the joined trajectory
    would hold a settling transient in the middle of what is meant to be
    a production run.

    **The production steps add up.** Integer division leaves a remainder,
    and dropping it would quietly shorten the study: ten segments of a
    million and one steps is not ten lots of a hundred thousand. The
    remainder goes on the last segment.

    **Every segment after the first names its predecessor's checkpoint.**
    Without that the pieces are not segments at all -- they are ten
    independent runs of a tenth the length, which is a different and much
    worse experiment that no output would distinguish from the intended
    one.

    Raises
    ------
    StudyError
        Where the study is one that may not be split at all. See
        :func:`segmentability`.
    """
    require_segmentable(config, segments=segments)
    if segments < 1:
        raise StudyError(
            f"A study runs in at least one segment; got {segments}.",
            code="config.option.wrong_type",
            option="segments", found_type="below one")

    block = dict((config.get("simulation") or {}))
    total = _production_steps(block)
    base, remainder = divmod(total, segments)

    planned: list[Segment] = []
    for index in range(segments):
        steps = base + (remainder if index == segments - 1 else 0)
        simulation = dict(block)
        simulation["production_steps"] = steps
        # An explicit step count and a duration in the same block would
        # leave which one wins to the reader. The count is what this
        # decided, so the duration goes.
        simulation.pop("duration_ns", None)

        piece = dict(config)
        if index > 0:
            simulation["minimize"] = False
            simulation["nvt_steps"] = 0
            simulation["npt_steps"] = 0
            simulation.pop("nvt_duration_ns", None)
            simulation.pop("npt_duration_ns", None)
        piece["simulation"] = simulation

        previous = (None if index == 0 else
                    f"{output_dir_for(index - 1)}/{checkpoint_name}"
                    if output_dir_for else checkpoint_name)
        if previous:
            # Into the config, not beside it. A segment's config has to
            # describe the segment completely, or the resolved config of a
            # run that resumed would not say where it resumed from, and
            # rerunning it from that file would silently start over.
            simulation["resume_from"] = previous
        planned.append(Segment(index=index, of_segments=segments,
                               config=piece, resume_from=previous,
                               steps=steps))
    return planned
