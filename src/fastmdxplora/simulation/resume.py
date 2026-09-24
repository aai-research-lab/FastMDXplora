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
from pathlib import Path
from typing import Any, Callable

from fastmdxplora.refusals import Refusal, StudyError
from fastmdxplora.simulation.ensembles import NPT, resolve_ensemble

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
    # periodic box. A constant-volume run resumed from a checkpoint at the
    # same thread count reproduces the run it continued to within 1e-9 nm
    # over fifty steps. At another thread count the thermostat draws a
    # different noise sequence, so it continues the ensemble rather than the
    # trajectory -- which needs no qualification, because Langevin noise
    # carries no memory and the joined run is a sample of the same dynamics.
    # The same run at
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
    #
    # Whether production has a barostat is the ensemble it runs in, asked
    # of the resolver the runner uses. A pressure in the config does not
    # say: a default study writes none and runs at constant pressure, and
    # every resolved config carries one, NVT production included.
    barostat = resolve_ensemble(block) == NPT
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
            # State the ensemble rather than leave it to be inferred from
            # the stage lengths. Zeroing `npt_steps` used to mean both "do
            # not equilibrate" and "no barostat", so a resumed segment
            # silently produced at constant volume while the first segment
            # produced at constant pressure -- two ensembles in one
            # trajectory with nothing able to tell.
            #
            # Now the two are separate questions and this answers both:
            # whatever the study runs in, and no equilibration.
            simulation["ensemble"] = resolve_ensemble(block)
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


# ---------------------------------------------------------------------------
# Continuing a study that stopped.
#
# plan_segments splits a run up front. This is the other direction: one
# new segment from a study that already ran and stopped, for however much
# more is wanted, with the arithmetic done from the record rather than
# asked of the person. The Agent used to write resume_from by hand and ask
# for the equilibration lengths; the parent's resolved config has them,
# the checkpoint's sidecar has the step, and the rest is subtraction.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Continuation:
    """What continuing a study would be, and what it would carry."""

    parent: str
    checkpoint: str
    production_done_ns: float
    production_planned_ns: float
    config: dict[str, Any]
    #: Why it cannot be continued, or None.
    refusal: str | None = None

    @property
    def possible(self) -> bool:
        return self.refusal is None

    def as_text(self) -> str:
        if self.refusal:
            return f"cannot be continued: {self.refusal}"
        return (f"production done {self.production_done_ns:.3f} ns of "
                f"{self.production_planned_ns:.3f} ns planned; a continuation "
                f"resumes from {Path(self.checkpoint).name} in the same solvated system, "
                f"with no minimisation and no equilibration")


def continuation_of(parent: str | Path, *, total_ns: float | None = None,
                    more_ns: float | None = None,
                    from_segment: str | Path | None = None) -> Continuation:
    """The config that continues ``parent``, and the facts it rests on.

    ``total_ns`` asks for that much production in all, counting what
    already ran; ``more_ns`` asks for that much more. Given neither, the
    config carries the remainder of what the parent planned. The config
    is a whole study: it reuses the parent's prepared system, resumes
    from its checkpoint, and neither minimises nor equilibrates -- the
    three things a hand-written resume gets wrong.

    ``from_segment`` is the folder whose checkpoint is resumed, where that
    is not the study's own run: a study already extended goes on from its
    last segment, not from where its first run stopped, and what it has
    done is then counted across all of its segments.
    """
    import yaml

    from fastmdxplora.simulation.runner import read_checkpoint_sidecar

    root = Path(parent).expanduser().resolve()
    source = Path(from_segment).expanduser().resolve() if from_segment else root
    empty = dict(parent=str(root), checkpoint="", production_done_ns=0.0,
                 production_planned_ns=0.0, config={})
    resolved = root / "resolved_config.yml"
    if not resolved.is_file():
        return Continuation(**empty, refusal="the study has no resolved_config.yml")
    try:
        config = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        return Continuation(**empty, refusal=f"the study's config could not be read: {exc}")
    verdict = segmentability(config)
    if not verdict.allowed:
        return Continuation(**empty, refusal=verdict.reason)

    checkpoint = source / "simulation" / "checkpoint.chk"
    empty["checkpoint"] = str(checkpoint)
    if not checkpoint.is_file():
        where = "the study" if source == root else source.name
        return Continuation(**empty,
                            refusal=f"{where} has no checkpoint; checkpoints are written "
                                    "during production, so it may not have reached it")
    side = read_checkpoint_sidecar(checkpoint) or {}
    if str(side.get("stage") or "") != "production":
        return Continuation(**empty,
                            refusal="the checkpoint does not say it was written during "
                                    "production, so continuing from it is not safe")

    sim = dict(config.get("simulation") or {})
    dt_fs = float(sim.get("timestep_fs") or 2.0)
    dt_ns = dt_fs * 1e-6
    nvt = int(sim.get("nvt_steps") if sim.get("nvt_steps") is not None
              else round(float(sim.get("nvt_duration_ns") or 0.5) / dt_ns))
    npt = int(sim.get("npt_steps") if sim.get("npt_steps") is not None
              else round(float(sim.get("npt_duration_ns") or 1.0) / dt_ns))
    planned_ns = float(sim.get("duration_ns") or
                       (int(sim.get("production_steps") or 0) * dt_ns))
    # The checkpoint's step is a production step. The runner resets the
    # counter to zero before production so that every statistic computed
    # from the energy log measures production alone; the checkpoints are
    # written after that reset. Subtracting the equilibration here
    # subtracted it a second time: a 0.5 ns production read as 0.3 ns,
    # and "extend to 0.6 ns in all" asked for 0.3 ns more instead of 0.1.
    # A sidecar written before this was understood may carry a whole-run
    # step; one larger than the plan is recognised and converted.
    # Only the study's own run equilibrated, so only its step can be a
    # whole-run one; a segment's step counts its own production from zero,
    # and the study's plan says nothing about how long a segment ran.
    step = int(side.get("step") or 0)
    planned_steps = int(round(planned_ns / dt_ns)) if dt_ns else 0
    if source == root and planned_steps and step > planned_steps + nvt + npt - 1:
        step = max(0, step - nvt - npt)
    done_steps = max(0, step)
    done_ns = done_steps * dt_ns if source == root else production_done_ns(root)

    # The continuation's frames start from its own zero, at the checkpoint.
    # Off the frame grid, the gap across the join is the interval plus the
    # remainder, and the joined trajectory changes its spacing there.
    interval = trajectory_interval_of(source)
    if interval and done_steps % interval:
        return Continuation(**empty, refusal=(
            f"the checkpoint is at production step {done_steps:,}, which is not a "
            f"multiple of the {interval:,} steps between frames, so a continuation's "
            f"frames would fall {done_steps % interval:,} steps off the ones before "
            "it and the joined trajectory would change its spacing at the join. "
            "Runs now place their checkpoints on frames; this one has to be rerun "
            "to be continued."))

    if total_ns is not None:
        remaining = float(total_ns) - done_ns
    elif more_ns is not None:
        remaining = float(more_ns)
    else:
        remaining = planned_ns - done_ns
    if remaining <= 0:
        # A finished plan is not a study that cannot be continued -- it is
        # the case extending a study exists for. Saying "cannot be
        # continued" here sent the Agent to offer a fresh run of the same
        # molecule instead of the hundred picoseconds that were asked for.
        asked = total_ns is not None or more_ns is not None
        why = (f"{done_ns:.3f} ns of production is already written, which is "
               f"at or past the {float(total_ns):.3f} ns asked for"
               if total_ns is not None else
               f"production already reached {done_ns:.3f} ns, which is the "
               f"{planned_ns:.3f} ns this study planned. Nothing remains of "
               f"the plan, but it can still be extended past it: "
               f"`fastmdx explore --simulate-resume-from {root} "
               f"--simulate-duration-ns <total>`, or `--simulate-extra-ns "
               f"<more>` in place of the total")
        if asked and total_ns is None:
            why = f"{done_ns:.3f} ns is already written and no further length was asked for"
        return Continuation(parent=str(root), checkpoint=str(checkpoint),
                            production_done_ns=done_ns, production_planned_ns=planned_ns,
                            config={}, refusal=why)

    new = {k: v for k, v in config.items() if k not in ("output", "include_phase", "exclude_phase")}
    # The parent's analysis block names the parent's own trajectory by
    # absolute path. Carried across, the continuation would simulate its
    # segment into a new folder and then analyse the PARENT's file,
    # reporting the parent's numbers as the new study's. Those paths go;
    # the continuation analyses what it wrote. Analysing the two together
    # is the explicit join, which refuses gaps and unsealed segments and
    # is not something a resume should do silently.
    analysis = {k: v for k, v in (config.get("analysis") or {}).items()
                if k not in ("trajectory", "topology")}
    if analysis:
        new["analysis"] = analysis
    else:
        new.pop("analysis", None)
    new_sim = {k: v for k, v in sim.items()
               if k not in ("nvt_steps", "npt_steps", "nvt_duration_ns", "npt_duration_ns",
                            "production_steps", "resume_from", "prepared_from", "setup_from")}
    new_sim.update({
        "duration_ns": round(remaining, 6),
        "resume_from": str(checkpoint),
        "setup_from": str(root),
        "minimize": False,
        "nvt_steps": 0,
        "npt_steps": 0,
        "ensemble": str(side.get("ensemble") or ("npt" if npt > 0 else "nvt")),
    })
    # The parent's spacing, not a new one. Left unset, the interval is
    # chosen from the run's own length, so a 1 ns remainder of a 2 ns run
    # wrote frames twice as often and the joined trajectory changed its
    # spacing at the join with nothing to say so.
    interval = trajectory_interval_of(root)
    if interval is not None:
        new_sim["trajectory_interval_steps"] = interval
    new["simulation"] = new_sim
    new["exclude_phase"] = ["setup"]
    return Continuation(parent=str(root), checkpoint=str(checkpoint),
                        production_done_ns=done_ns, production_planned_ns=planned_ns,
                        config=new)


# ---------------------------------------------------------------------------
# Extending a study, without leaving the joining to the person.
#
# A continuation is more of the same study, not a new one: the same
# system, the same water, the same velocities carried through a
# checkpoint. So it belongs inside the study, as its next segment, and
# the pieces are put together and re-analysed by the software rather
# than by hand. What the join refuses -- a gap, an unsealed segment,
# segments from two studies -- it still refuses; automatic does not mean
# unchecked.
# ---------------------------------------------------------------------------


def next_segment_index(study: str | Path) -> int:
    """The next free segment number. A study that ran in one piece is its
    own segment zero, the index a campaign's first segment has, so its
    first extension is one."""
    root = Path(study)
    used = {0} if (root / "simulation").is_dir() else set()
    for directory in root.glob("segment-*"):
        try:
            used.add(int(directory.name.split("-", 1)[1]))
        except (IndexError, ValueError):
            continue
    return (max(used) + 1) if used else 0


def segments_so_far(study: str | Path) -> list[int]:
    from fastmdxplora.analysis.joining import survey_segments

    return sorted(piece.index for piece in survey_segments(study))


def last_segment(study: str | Path) -> Path:
    """The folder of the study's highest-numbered segment, which is where a
    continuation resumes from: the study itself until it has been extended.
    Chosen by number, because the segment a run last wrote is the one the
    join puts last, whatever order the folders list or were touched in."""
    from fastmdxplora.analysis.joining import survey_segments

    pieces = survey_segments(study)
    return max(pieces, key=lambda piece: piece.index).directory if pieces else Path(study)


def production_done_ns(study: str | Path) -> float:
    """Production across every finished segment of this study."""
    import yaml

    root = Path(study)
    total = 0.0
    for index in segments_so_far(root):
        folder = root if index == 0 else root / f"segment-{index:03d}"
        resolved = folder / "resolved_config.yml"
        try:
            config = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        piece = continuation_of(folder)
        total += piece.production_done_ns if piece.production_done_ns else float(
            (config.get("simulation") or {}).get("duration_ns") or 0.0)
    return round(total, 9)


def extension_of(study: str | Path, *, total_ns: float | None = None,
                 more_ns: float | None = None) -> Continuation:
    """The next segment of ``study``, written inside it.

    The same continuation as before -- resumed from the checkpoint, no
    minimisation, no equilibration -- but its output is the study's next
    segment folder and it runs the simulation only. Analysis and the
    report come after the join, over the whole trajectory.
    """
    done = production_done_ns(study)
    if total_ns is not None:
        wanted = float(total_ns) - done
    elif more_ns is not None:
        wanted = float(more_ns)
    else:
        wanted = None

    # From the last segment's checkpoint. The study's own is where its first
    # run stopped; resuming there once it has been extended would run the
    # extensions' span again, and the join would hold that span twice.
    last = last_segment(study)
    plan = continuation_of(study, more_ns=wanted, from_segment=last) if wanted is not None \
        else continuation_of(study, from_segment=last)
    if not plan.possible:
        return plan
    index = next_segment_index(study)
    config = dict(plan.config)
    config["output"] = str(Path(study) / f"segment-{index:03d}")
    # The segment simulates. The study's analysis and report are rerun
    # over the joined trajectory once it exists.
    config["include_phase"] = ["simulation"]
    config.pop("exclude_phase", None)
    return Continuation(parent=plan.parent, checkpoint=plan.checkpoint,
                        production_done_ns=done,
                        production_planned_ns=plan.production_planned_ns,
                        config=config)


def extend_study(study: str | Path, *, total_ns: float | None = None,
                 more_ns: float | None = None,
                 analyse: bool = True) -> dict[str, Any]:
    """Run the next segment, join the study, and analyse the whole.

    Three steps, so that asking for more sampling is one instruction
    rather than three: simulate the segment into the study; join every
    finished segment into one trajectory; rerun the analyses and the
    report over that trajectory. The join refuses a gap, an unsealed
    segment or segments from two studies, and a refusal stops the step
    rather than being worked around -- an automatic join that papers over
    a discontinuity is worse than one that never ran.
    """
    import yaml

    from fastmdxplora.analysis.joining import join_segments

    from fastmdxplora.analysis.joining import survey_segments

    root = Path(study).expanduser().resolve()
    plan = extension_of(root, total_ns=total_ns, more_ns=more_ns)
    if not plan.possible:
        return {"ok": False, "error": plan.refusal, "stage": "planning"}

    # Before anything runs: can what is here be joined to what will be?
    # A segment that did not finish was killed, and its trajectory holds frames
    # written after its last checkpoint -- the frames a resume would run
    # again. Joining the two would put that overlap in the middle of the
    # trajectory with nothing to mark it, and every analysis downstream
    # would read straight through it. Said now, before a segment is
    # simulated, rather than after.
    # A piece that was killed holds frames written after its last
    # checkpoint -- the frames this resume runs again. They are left out
    # of the join, so the pieces meet at the checkpoint rather than
    # overlapping. Where the frames to keep cannot be counted -- no
    # recorded step, no recorded interval -- the join is refused rather
    # than guessed at, because a guess here puts an overlap in a
    # trajectory and calls it whole.
    keep_frames: dict[int, int] = {}
    uncountable: list[int] = []
    for piece in survey_segments(root):
        if piece.finished:
            continue
        folder = root if piece.index == 0 else root / f"segment-{piece.index:03d}"
        keep = frames_before_checkpoint(folder)
        if keep is None:
            uncountable.append(piece.index)
        else:
            keep_frames[piece.index] = keep
    if uncountable:
        where = ", ".join(("the study's own run" if i == 0 else f"segment-{i:03d}")
                          for i in uncountable)
        return {"ok": False, "stage": "planning", "unsealed": uncountable,
                "error": f"{where} did not finish cleanly, and how much of its "
                         "trajectory precedes its last checkpoint cannot be worked "
                         "out: the step or the frame interval is not recorded. "
                         "Joining it would leave frames the resume runs again in "
                         "the middle of the trajectory."}
    if keep_frames:
        # The resume starts from a checkpoint the run did not seal, which
        # the runner refuses unless it is told. OpenMM still refuses one it
        # cannot read, which is what a torn write leaves.
        plan.config.setdefault("simulation", {})["resume_unsealed"] = True

    segment = Path(plan.config["output"])
    config_path = segment.with_name(segment.name + ".yml")
    segment.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.safe_dump(plan.config, sort_keys=False),
                           encoding="utf-8")

    from fastmdxplora import FastMDXplora

    study_run = FastMDXplora(config_data=plan.config, output_dir=str(segment))
    study_run.explore()

    # Every finished segment, in one trajectory, in the study's own folder.
    joined_dir = root / "joined"
    joined_dir.mkdir(parents=True, exist_ok=True)
    # The trajectory's own topology, not the system's. A run that saved a
    # selection -- "not water", almost always -- writes frames of those
    # atoms and a matching trajectory_topology.pdb beside them; joining
    # against the full solvated system asks MDTraj to read 37 atoms into
    # 1369 and it refuses, rightly.
    topology = root / "simulation" / "trajectory_topology.pdb"
    try:
        record = join_segments(root, joined_dir / "production.dcd",
                               topology=topology if topology.is_file() else None,
                               keep_frames=keep_frames or None)
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        from fastmdxplora.refusals import refusal_of

        return {"ok": False, "stage": "joining", "segment": str(segment),
                "error": str(exc), "refusal": refusal_of(exc).as_dict()}
    (joined_dir / "joined.json").write_text(
        json_dumps(record), encoding="utf-8")

    if not analyse:
        return {"ok": True, "segment": str(segment), "joined": record,
                "analysed": False}

    # The study's analyses and report, over the whole trajectory.
    whole = dict(plan.config)
    whole["output"] = str(root)
    whole["include_phase"] = ["analysis", "report"]
    analysis = dict(whole.get("analysis") or {})
    analysis["trajectory"] = str(joined_dir / "production.dcd")
    topology = root / "simulation" / "trajectory_topology.pdb"
    if topology.is_file():
        analysis["topology"] = str(topology)
    whole["analysis"] = analysis
    # The study's own analysis and report are replaced on purpose: they
    # described a shorter trajectory than the study now has, and leaving
    # them would leave a report whose numbers are for a run that is no
    # longer the whole of it. The segments themselves are never touched.
    FastMDXplora(config_data=whole, output_dir=str(root)).explore(force=True)
    return {"ok": True, "segment": str(segment), "joined": record,
            "analysed": True, "trajectory": str(joined_dir / "production.dcd")}


def json_dumps(value: Any) -> str:
    import json

    return json.dumps(value, indent=1, default=str)


def trajectory_interval_of(segment: str | Path) -> int | None:
    """Steps between the frames a run wrote, from what the run recorded.

    Left unset, the interval is chosen from the run's own length, so a
    killed run's config says nothing, and the record that would say it is
    written only at the end of the phase. Read, in order, from the
    checkpoint's sidecar, which records it with every checkpoint; the
    finished run's record; the config, where it was set; and the run's own
    telemetry, which pairs each production step with the frames written by
    then and is accepted only where every sample agrees with one interval.
    None where none of these says, so a caller refuses rather than guesses.
    """
    import json

    import yaml

    from fastmdxplora.simulation.runner import read_checkpoint_sidecar

    folder = Path(segment)
    simulation = folder / "simulation"

    def whole(value) -> int | None:
        return int(value) if isinstance(value, (int, float)) and value > 0 else None

    side = read_checkpoint_sidecar(simulation / "checkpoint.chk") or {}
    if whole(side.get("trajectory_interval_steps")):
        return whole(side["trajectory_interval_steps"])
    try:
        record = json.loads((simulation / "simulation_parameters.json").read_text(encoding="utf-8"))
        found = whole((record.get("resolved") or {}).get("trajectory_interval_steps"))
        if found:
            return found
    except (OSError, ValueError, AttributeError):
        pass
    try:
        config = yaml.safe_load((folder / "resolved_config.yml").read_text(encoding="utf-8")) or {}
        found = whole((config.get("simulation") or {}).get("trajectory_interval_steps"))
        if found:
            return found
    except (OSError, yaml.YAMLError, AttributeError):
        pass
    return _interval_from_telemetry(simulation)


def _interval_from_telemetry(simulation: Path) -> int | None:
    """The interval every production sample of the run's telemetry agrees on.

    Each sample says how many frames production had written by a step, as
    (step - start) // interval, and the status says how many were planned
    in all. One interval from the first and last samples, then kept only if
    some production start makes every sample, and the plan, come out
    exactly: a disagreement anywhere means the samples do not say, and
    None is returned.
    """
    import csv
    import json

    try:
        with (simulation / "live_metrics.csv").open(encoding="utf-8") as handle:
            rows = [(int(float(r["step"])), int(float(r["current_frame_count"])))
                    for r in csv.DictReader(handle)
                    if str(r.get("stage", "")).lower().startswith("production")
                    and r.get("step") and r.get("current_frame_count")]
        status = json.loads((simulation / "live_status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError):
        return None
    if len(rows) < 2 or rows[-1][1] <= rows[0][1]:
        return None
    (first_step, first_frames), (last_step, last_frames) = rows[0], rows[-1]
    interval = round((last_step - first_step) / (last_frames - first_frames))
    if interval <= 0:
        return None
    # The production start P0 satisfies f*I <= step - P0 < (f+1)*I for
    # every sample: step-(f+1)*I < P0 <= step-f*I.
    low = max(step - (frames + 1) * interval for step, frames in rows)
    high = min(step - frames * interval for step, frames in rows)
    total, planned = status.get("total_planned_steps"), status.get("planned_frame_count")
    if isinstance(total, int) and isinstance(planned, int) and planned > 0:
        low = max(low, total - (planned + 1) * interval)
        high = min(high, total - planned * interval)
    return interval if low < high else None


def frames_before_checkpoint(segment: str | Path) -> int | None:
    """How many written frames precede this segment's last checkpoint.

    A killed run's trajectory holds frames past its last checkpoint --
    the frames a resume runs again. Those are the frames to leave out of
    a join, and this counts the ones to keep. A reporter writes a frame
    every ``trajectory_interval_steps``, so frame k is at step
    k*interval and the frames at or before the checkpoint are
    ``step // interval``. None where the interval or the step is not
    recorded, because a guess here would put an overlap in a trajectory
    and call it whole.
    """
    from fastmdxplora.simulation.runner import read_checkpoint_sidecar

    folder = Path(segment)
    side = read_checkpoint_sidecar(folder / "simulation" / "checkpoint.chk") or {}
    step = side.get("step")
    if not isinstance(step, (int, float)) or step < 0:
        return None
    interval = trajectory_interval_of(folder)
    if interval is None:
        return None
    return int(step) // int(interval)
