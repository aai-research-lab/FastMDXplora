"""Whether a run may be stopped and picked up again, and where it may not.

A hundred nanoseconds submitted as ten ten-nanosecond pieces is the
difference, on one card, between finishing three candidates in a week and
finishing one. It is also, for some studies, a way of producing a
confidently wrong answer.

The same reasoning sits beside ``_attach_checkpoint_reporter``, where the
checkpoint is written, and ``fastmdx resume`` follows it. A checkpoint
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

import contextlib
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from fastmdxplora import record_access as _records
from fastmdxplora.refusals import CodedError, Refusal, StudyError
from fastmdxplora.simulation.ensembles import NPT, resolve_ensemble

__all__ = [
    "Segment",
    "extended_production",
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

    if block.get("replica_exchange"):
        return Segmentability(
            False, "replica_exchange",
            "A replica exchange run cannot be split. Its replicas and which "
            "conditions each holds live in the sampler's own storage, not in a "
            "checkpoint of one simulation. Run it through in one piece.",
            code="simulation.resume.bias_not_carried")

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
            "which is the result -- would be taken along a path "
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
    would hold an equilibration transient in the middle of what is meant to be
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
    from fastmdxplora.simulation.runner import read_checkpoint_sidecar

    root = Path(parent).expanduser().resolve()
    source = Path(from_segment).expanduser().resolve() if from_segment else root
    empty = dict(parent=str(root), checkpoint="", production_done_ns=0.0,
                 production_planned_ns=0.0, config={})
    resolved = root / "resolved_config.yml"
    # Read through `record_access`. In a read held to the study (the study
    # pages), a config or checkpoint the read refuses raises: not read is
    # not a study with none.
    _refuse(resolved)
    if not _records.is_file(resolved):
        return Continuation(**empty, refusal="the study has no resolved_config.yml")
    config, why = _read_config(resolved)
    if config is None:
        return Continuation(**empty, refusal=f"the study's config could not be read: {why}")
    verdict = segmentability(config)
    if not verdict.allowed:
        return Continuation(**empty, refusal=verdict.reason)

    checkpoint = source / "simulation" / "checkpoint.chk"
    empty["checkpoint"] = str(checkpoint)
    _refuse(checkpoint, _sidecar_of(checkpoint))
    if not _records.is_file(checkpoint):
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
    # Checked by `_read_config`, as the numbers below are.
    # A stated zero is zero, as the runner reads it.
    nvt_ns = sim.get("nvt_duration_ns")
    npt_ns = sim.get("npt_duration_ns")
    from fastmdxplora.simulation.lengths import DEFAULT_NPT_NS, DEFAULT_NVT_NS, steps_in

    nvt = int(sim.get("nvt_steps") if sim.get("nvt_steps") is not None
              else steps_in(DEFAULT_NVT_NS if nvt_ns is None else nvt_ns, dt_fs))
    npt = int(sim.get("npt_steps") if sim.get("npt_steps") is not None
              else steps_in(DEFAULT_NPT_NS if npt_ns is None else npt_ns, dt_fs))
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
    # Only a sidecar that does not record its trajectory's interval can be
    # one of those: the interval was recorded from after the counter was
    # understood. Read from any later sidecar, the test misfired once a
    # study had been extended, because the join rewrites the study's own
    # config with its last piece's length: 300 production steps were read
    # against a last piece of 50 and 200 steps of equilibration, and
    # counted as 100, so the study's production went down as it grew.
    step = int(side.get("step") or 0)
    planned_steps = int(round(planned_ns / dt_ns)) if dt_ns else 0
    if (source == root and planned_steps and "trajectory_interval_steps" not in side
            and step > planned_steps + nvt + npt - 1):
        step = max(0, step - nvt - npt)
    done_steps = max(0, step)
    done_ns = done_steps * dt_ns if source == root else production_done_ns(root)

    # The continuation's frames start from its own zero, at the checkpoint.
    # Off the frame grid, the gap across the join is the interval plus the
    # remainder, and the joined trajectory changes its spacing there.
    interval = trajectory_interval_of(source)
    if interval and done_steps % interval:
        # What ran is said with the refusal: read as nothing done, a study
        # of one piece was run again from its start over its frames.
        return Continuation(**{**empty, "production_done_ns": done_ns,
                               "production_planned_ns": planned_ns}, refusal=(
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
                            "production_steps", "resume_from", "prepared_from", "setup_from",
                            # A piece is one length; the rule that chose it
                            # is the study's, and applied there.
                            "stop_when")}
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


def extended_production(study: str | Path) -> tuple[float, int] | None:
    """Production across an extended study's pieces, and how many pieces
    ran; None for a study that ran in one, and for one where a piece's
    ``resolved_config.yml`` does not read as a config, so what it ran is
    not known (:func:`production_unreadable` says why).

    An extension writes its own folder inside the study (``segment-001``
    onward) and leaves the study's own ``simulation_parameters.json`` as
    the first piece wrote it. Read from there alone, a study extended from
    10 ns to 40 ns said "Production dynamics were run for 10 ns" in its
    methods while its analyses averaged 40.
    """
    from fastmdxplora.analysis.joining import survey_segments
    from fastmdxplora.refusals import OutsideWorkspace

    try:
        # A piece a held read refuses is not a piece that is not there.
        _refuse_pieces(Path(study))
        # No piece but the study's own: not extended, and its pieces are
        # not surveyed (each one's Config read again for its digest).
        if not any(folder.name[8:].isdigit()
                   for folder in _records.glob(Path(study), "segment-*")):
            return None
        # A piece's config that does not read: what it ran is not known, so
        # nothing is summed (`production_unreadable` says why).
        if production_unreadable(study) is not None:
            return None
        ran = [piece for piece in survey_segments(study) if piece.trajectory is not None]
    except OutsideWorkspace:
        raise
    except OSError:
        return None
    if len(ran) < 2:
        return None
    return production_done_ns(study), len(ran)


def _read_config(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    """A piece's ``resolved_config.yml`` as a config, or None and why: not
    there or not read, not UTF-8, not YAML, not a mapping of settings, or a
    simulation block whose timestep or lengths are not numbers. Each of
    these raised where the production was counted, and the study's stage
    lengths with it."""
    import yaml

    from fastmdxplora.refusals import OutsideWorkspace

    try:
        text = _records.read_text(path, encoding="utf-8")
    except OutsideWorkspace:
        raise
    except UnicodeDecodeError:
        return None, "it is not UTF-8 text"
    except OSError as exc:
        return None, f"it could not be read ({exc.strerror or exc})"
    try:
        config = yaml.load(text, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader)) or {}
    except yaml.YAMLError:
        return None, "it is not YAML"
    if not isinstance(config, dict):
        return None, "it is not a mapping of settings"
    simulation = config.get("simulation")
    if simulation is None:
        return config, None
    if not isinstance(simulation, dict):
        return None, "its `simulation` is not a mapping of settings"
    for key in ("timestep_fs", "duration_ns", "nvt_duration_ns", "npt_duration_ns",
                "production_steps", "nvt_steps", "npt_steps"):
        value = simulation.get(key)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0 \
                or value != value or (key == "timestep_fs" and value == 0):
            return None, f"its `simulation.{key}` ({value!r}) is not a length"
    return config, None


def production_unreadable(study: str | Path) -> str | None:
    """Why the production of a study carried on in pieces is not known: the
    first piece whose ``resolved_config.yml`` is there and does not read
    as a config (:func:`_read_config`), named by its folder; None where
    every one that is there reads."""
    import re

    root = Path(study)
    try:
        folders = [root] + sorted(
            (folder for folder in _records.glob(root, "segment-*")
             if re.fullmatch(r"segment-\d+", folder.name)),
            key=lambda folder: int(folder.name[8:]))
    except OSError:
        return None
    for folder in folders:
        resolved = folder / "resolved_config.yml"
        if not _records.is_file(resolved):
            continue
        _config, why = _read_config(resolved)
        if why is not None:
            where = "the study's" if folder == root else f"{folder.name}'s"
            return f"{where} resolved_config.yml does not read: {why}"
    return None


def _refuse(*paths: Path) -> None:
    """`pipeline._refuse`: raise `OutsideWorkspace` for the first of
    ``paths`` a held read refuses; nothing outside one."""
    from fastmdxplora.simulation.pipeline import _refuse as refuse

    refuse(*paths)


def _sidecar_of(checkpoint: Path) -> Path:
    from fastmdxplora.simulation.runner import CHECKPOINT_SIDECAR_SUFFIX

    return checkpoint.with_suffix(checkpoint.suffix + CHECKPOINT_SIDECAR_SUFFIX)


def _refuse_pieces(root: Path) -> None:
    """In a read held to the study (`record_access`), raise
    `OutsideWorkspace` for a piece's folder or trajectory the read refuses:
    the pieces are found by these (`survey_segments`), and one refused was
    passed over as not there. Nothing outside a held read."""
    import os
    import re

    if _records.can_read(Path(os.path.abspath(os.sep))):
        return
    folders = [root] + sorted(folder for folder in _records.glob(root, "segment-*")
                              if re.fullmatch(r"segment-\d+", folder.name))
    for folder in folders:
        _refuse(folder, folder / "simulation", folder / "simulation" / "production.dcd")


def production_done_ns(study: str | Path) -> float:
    """Production across the pieces of this study: each finished piece at
    its length, each stopped one as far as its checkpoint, and a piece
    stopped before its first checkpoint not at all."""
    from fastmdxplora.analysis.joining import survey_segments
    from fastmdxplora.simulation.runner import read_checkpoint_sidecar

    root = Path(study)
    total = 0.0
    # In a read held to the study, a piece, or a record of one, that the
    # read refuses raises rather than being passed over: a piece left out
    # understates the production.
    _refuse_pieces(root)
    # Each piece's own folder, as found. Rebuilt from its number as
    # segment-NNN, a folder named segment-1 was never read.
    for found in survey_segments(root):
        folder = found.directory
        checkpoint = folder / "simulation" / "checkpoint.chk"
        resolved = folder / "resolved_config.yml"
        _refuse(checkpoint, _sidecar_of(checkpoint), resolved)
        # The final state says a piece finished only where its checkpoint's
        # record does not (`joining._finished`): refused only then.
        if not isinstance((read_checkpoint_sidecar(checkpoint) or {}).get("finished"), bool):
            _refuse(folder / "simulation" / "state_final.xml")
        # Stopped before its first checkpoint, a piece kept nothing: what it
        # ran is run again from the checkpoint before it. It was counted at
        # the length it was asked for.
        if not found.finished and not _records.is_file(checkpoint):
            continue
        # Killed between writing the checkpoint and its record of itself:
        # nothing says how far it got, so it counts nothing, as one killed
        # just before the checkpoint does.
        if not found.finished and str((read_checkpoint_sidecar(checkpoint) or {})
                                      .get("stage") or "") != "production":
            continue
        config, _why = _read_config(resolved)
        if config is None:
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


def study_to_continue(config: dict[str, Any] | None) -> Path | None:
    """The study a config continues, where ``simulation.resume_from`` names a
    study directory; None where it names a checkpoint file, which is the raw
    mechanism and an ordinary run, or nothing. One reading for the command
    line, the Python API and the GUI, which launches through the first."""
    simulation = (config or {}).get("simulation") or {}
    named = simulation.get("resume_from") if isinstance(simulation, dict) else None
    if not named:
        return None
    study = Path(str(named)).expanduser()
    return study if study.is_dir() else None


def extend_study(study: str | Path, *, total_ns: float | None = None,
                 more_ns: float | None = None,
                 analyse: bool = True,
                 device_index: str | int | None = None,
                 checkpoint_interval_steps: int | None = None) -> dict[str, Any]:
    """Run the next segment, join the study, and analyse the whole.

    A study being carried on by another process is said, not carried on
    twice. ``checkpoint_interval_steps`` is how often the new piece writes
    its checkpoint, where the extension's config sets it; otherwise the
    study's.

    Three steps, so that asking for more sampling is one instruction
    rather than three: simulate the segment into the study; join every
    finished segment into one trajectory; rerun the analyses and the
    report over that trajectory. The join refuses a gap, an unsealed
    segment or segments from two studies, and a refusal stops the step
    rather than being worked around -- an automatic join that papers over
    a discontinuity is worse than one that never ran.
    """
    root = Path(study).expanduser().resolve()
    # A study, or a run of one, going elsewhere or run or held by another
    # process here (its lock, its study's or another run's), is refused
    # before anything is written: an extension run into a live run joined
    # a trajectory that is not one run.
    held = _held_elsewhere(root)
    if held:
        return {"ok": False, "stage": "planning",
                "error": held.replace(_STILL_GOING, _STILL_GOING_TO_EXTEND)}
    try:
        with _carrying_on(root):
            return _extend_study(root, total_ns=total_ns, more_ns=more_ns, analyse=analyse,
                                 device_index=device_index,
                                 checkpoint_interval_steps=checkpoint_interval_steps)
    except _CarriedOnElsewhere:
        return {"ok": False, "stage": "planning", "error": _STILL_GOING_TO_EXTEND}


def _extend_study(study: str | Path, *, total_ns: float | None = None,
                  more_ns: float | None = None,
                  analyse: bool = True,
                  device_index: str | int | None = None,
                  checkpoint_interval_steps: int | None = None) -> dict[str, Any]:
    """The work of :func:`extend_study`, under the study's lock."""
    import yaml

    root = Path(study).expanduser().resolve()
    # An extension stopped before its first checkpoint has nothing to go on
    # from: set aside, as a resume does, so the next is carried on from the
    # piece before it rather than refused for it.
    aside = _set_aside_a_segment_never_checkpointed(root)
    said = {"set_aside": str(aside)} if aside is not None else {}
    plan = extension_of(root, total_ns=total_ns, more_ns=more_ns)
    if not plan.possible:
        return {"ok": False, "error": plan.refusal, "stage": "planning", **said}

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
    keep_frames, refusal = _frames_to_keep(root)
    if refusal is not None:
        return {"ok": False, "stage": "planning", **refusal, **said}
    if keep_frames:
        # The resume starts from a checkpoint the run did not seal, which
        # the runner refuses unless it is told. OpenMM still refuses one it
        # cannot read, which is what a torn write leaves.
        plan.config.setdefault("simulation", {})["resume_unsealed"] = True

    if device_index is not None:
        # The device this run was given now, where a campaign shares out
        # its cards, rather than the one it had before it stopped.
        plan.config.setdefault("simulation", {})["device_index"] = device_index
    if checkpoint_interval_steps is not None:
        # Asked of this piece, as a run on a machine with a wall time asks
        # for checkpoints often enough to lose little at the stop. The
        # study's own was used whatever the extension said, so a piece of
        # 6,000 steps with the study's 10,000 wrote none, and a stop lost
        # all of it.
        plan.config.setdefault("simulation", {})[
            "checkpoint_interval_steps"] = int(checkpoint_interval_steps)

    segment = Path(plan.config["output"])
    config_path = segment.with_name(segment.name + ".yml")
    segment.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.safe_dump(plan.config, sort_keys=False),
                           encoding="utf-8")

    from fastmdxplora import FastMDXplora

    study_run = FastMDXplora(config_data=plan.config, output_dir=str(segment))
    ran = study_run.explore()
    failed = [r for r in ran if r.status != "ok"]
    if failed:
        from fastmdxplora.simulation.runner import STOPPED_CODE

        # Said as what it is. Left to the join, a segment that failed read
        # as one that could not be joined. A segment asked to stop says so,
        # so a study of several runs knows to start no more.
        stopped = any((phase.refusal or {}).get("code") == STOPPED_CODE
                      for result in failed for phase in result.phases)
        return {"ok": False, "stage": "simulation", "segment": str(segment),
                "stopped": stopped, **said,
                "error": failed[0].message or f"{segment.name} did not finish."}

    # Where the piece left behind went is said whatever the join says.
    return {**_join_and_analyse(root, plan.config, segment=segment,
                                keep_frames=keep_frames or None, analyse=analyse),
            **said}


def _frames_to_keep(root: Path, *, last_too: bool = True
                    ) -> tuple[dict[int, int], dict[str, Any] | None]:
    """How many frames of each piece that did not finish go into the join.

    A piece that was killed holds frames written after its last checkpoint,
    the frames the next piece ran again; the join keeps those before it.
    Read the same way wherever a study's pieces are joined, so a study
    stopped again after its pieces were joined (in its analysis, say) is
    joined as before when it is carried on. Where the frames to keep
    cannot be counted, the second item says why the join is refused.

    ``last_too`` False leaves the last piece alone: nothing ran after it,
    so its frames past its checkpoint overlap nothing, and a last piece
    that did not finish is said by the join as one that did not, never
    cut back to its checkpoint and called whole.
    """
    from fastmdxplora.analysis.joining import survey_segments

    keep_frames: dict[int, int] = {}
    uncountable: list[int] = []
    folders: dict[int, Path] = {}
    pieces = survey_segments(root)
    last = max((piece.index for piece in pieces), default=-1)
    for piece in pieces:
        if piece.finished or (not last_too and piece.index == last):
            continue
        folders[piece.index] = piece.directory
        keep = frames_before_checkpoint(piece.directory)
        if keep is None:
            uncountable.append(piece.index)
        else:
            keep_frames[piece.index] = keep
    if not uncountable:
        return keep_frames, None
    where = ", ".join(("the study's own run" if i == 0 else folders[i].name)
                      for i in uncountable)
    return keep_frames, {
        "unsealed": uncountable,
        "error": f"{where} did not finish cleanly, and how much of its "
                 "trajectory precedes its last checkpoint cannot be worked "
                 "out: the step or the frame interval is not recorded. "
                 "Joining it would leave frames the resume runs again in "
                 "the middle of the trajectory."}


def _save_what_is_replaced(root: Path) -> list[str]:
    """Save the study's analysis and report in `previous/` before the
    join's own run over them, as `--rerun` does: the analysis there may be
    one the person chose (`fastmdx analyze --rerun --trajectory`), and the
    join's replaced it with nothing left of it. The phases saved.

    Only an analysis that finished (its manifest written) is saved, and an
    analysis of the join is never saved over one saved before: a resume
    stopped in its own analyses left an unfinished one, or one of the join
    without its report, and the next resume saved it in place of the
    person's. What is not saved is replaced by the join's analyses."""
    from fastmdxplora.replaced import PREVIOUS, analysis_finished, make_way, outputs_of
    from fastmdxplora.utils.logging import get_logger

    if not outputs_of(root, "analysis"):
        return []
    # Finished as `--rerun` reads it (`make_way` saves no other).
    record = analysis_finished(root / "analysis")
    if record is None:
        return []
    given = record.get("trajectory_input")
    of_the_join = (isinstance(given, str) and Path(given).parts[-2:] == ("joined", "production.dcd"))
    if of_the_join and _records.exists(root / PREVIOUS / "analysis"):
        return []
    return make_way(root, ["analysis"], keep=True, say=get_logger(__name__).info)


def _join_and_analyse(root: Path, config: dict[str, Any], *, segment: Path | None,
                      keep_frames: dict[int, int] | None,
                      analyse: bool = True) -> dict[str, Any]:
    """Join every segment of a study into one trajectory, then rerun the
    study's analyses and report over it. Shared by extending a study and by
    finishing one whose production was complete when it stopped."""
    from fastmdxplora import FastMDXplora
    from fastmdxplora.analysis.joining import join_segments

    segment_text = str(segment) if segment is not None else None

    # Every finished segment, in one trajectory, in the study's own folder.
    joined_dir = root / "joined"
    joined_dir.mkdir(parents=True, exist_ok=True)
    owed = joined_dir / ANALYSIS_OWED
    if analyse:
        # Said before the join is written, taken away only once the study's
        # analyses and report of it have run: a study stopped anywhere
        # between read as finished, its report of the first piece alone.
        _write_atomically(owed, json_dumps({"segment": segment_text}))
    # The trajectory's own topology, not the system's. A run that saved a
    # selection -- "not water", almost always -- writes frames of those
    # atoms and a matching trajectory_topology.pdb beside them; joining
    # against the full solvated system asks MDTraj to read 37 atoms into
    # 1369 and it refuses, rightly.
    topology = root / "simulation" / "trajectory_topology.pdb"
    try:
        record = join_segments(root, joined_dir / "production.dcd",
                               topology=topology if topology.is_file() else None,
                               keep_frames=keep_frames)
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        from fastmdxplora.refusals import refusal_of

        return {"ok": False, "stage": "joining", "segment": segment_text,
                "error": str(exc), "refusal": refusal_of(exc).as_dict()}
    # When, in the record: a copy that does not carry the files' times
    # carries this (an analysis chosen after the join is told by it).
    from datetime import datetime, timezone

    record = {**record, "joined_at": datetime.now(timezone.utc).isoformat()}
    (joined_dir / "joined.json").write_text(
        json_dumps(record), encoding="utf-8")

    if not analyse:
        return {"ok": True, "segment": segment_text, "joined": record,
                "analysed": False}

    # The study's analyses and report, over the whole trajectory, run from
    # the study's own config at its whole length, which is in its place
    # before they start and after they end. Run from the last piece's, the
    # analysis wrote the piece's over the study's `resolved_config.yml`
    # until it ended: a study stopped then was read as its last piece (no
    # minimisation, no equilibration, its own length), and with the first
    # piece's step count beside that length it was refused by `fastmdx
    # resume`. A copy beside the joined trajectory says, to a resume, what
    # the study's config was where the analysis was stopped before it was
    # put back. The segments themselves are never touched; the study's own
    # analysis and report are replaced, since they described a shorter
    # trajectory than the study now has.
    resolved = root / "resolved_config.yml"
    kept = _the_study_s_config_text(root)
    whole_text = _keep_the_study_s_config(root, kept)
    import yaml

    try:
        own = yaml.safe_load(whole_text) if whole_text is not None else None
    except yaml.YAMLError:
        own = None
    if not isinstance(own, dict):
        # Run from the last piece's config instead, the analysis wrote it
        # over the study's, and nothing put the study's back. Said, and the
        # analysis left owed.
        return {"ok": False, "stage": "analysis", "segment": segment_text, "joined": record,
                "error": "The study's resolved_config.yml does not read as a config, so "
                         "the joined trajectory is not analysed: correct it and resume."}
    _write_atomically(joined_dir / STUDY_CONFIG_COPY, whole_text)
    whole = dict(own)
    whole["output"] = str(root)
    whole["include_phase"] = ["analysis", "report"]
    whole.pop("exclude_phase", None)
    analysis = dict(whole.get("analysis") or {})
    analysis["trajectory"] = str(joined_dir / "production.dcd")
    topology = root / "simulation" / "trajectory_topology.pdb"
    if topology.is_file():
        analysis["topology"] = str(topology)
    else:
        analysis.pop("topology", None)
    whole["analysis"] = analysis
    ran = FastMDXplora(config_data=whole, output_dir=str(root)).explore(force=True)
    try:
        _write_atomically(resolved, whole_text)
        (joined_dir / STUDY_CONFIG_COPY).unlink(missing_ok=True)
    except OSError:
        from fastmdxplora.utils.logging import get_logger

        get_logger(__name__).warning(
            "The study's resolved config could not be written back.")
    failed = [result for result in ran if result.status != "ok"]
    if failed:
        from fastmdxplora.simulation.runner import STOPPED_CODE

        # Its answer is in the study's Manifest; the analysis stays owed.
        # Said in the phase it ended in (the report's, or the analyses'),
        # and as a stop where it was asked to stop, as a piece's is.
        ended = [phase for result in failed for phase in getattr(result, "phases", None) or ()
                 if getattr(phase, "status", None) == "error"]
        return {"ok": False, "stage": ended[0].name if ended else "analysis",
                "stopped": any((getattr(phase, "refusal", None) or {}).get("code")
                               == STOPPED_CODE for phase in ended),
                "segment": segment_text, "joined": record,
                "error": failed[0].message or "The analyses of the joined trajectory "
                                              "did not finish."}
    _production_carried_on(root, record)
    owed.unlink(missing_ok=True)
    _phases_after_ended_well(root)
    return {"ok": True, "segment": segment_text, "joined": record,
            "analysed": True, "trajectory": str(joined_dir / "production.dcd")}


def _phases_after_ended_well(root: Path) -> None:
    """The study's record said as its analyses and report ending well leave
    it (`telemetry.phases_after_ended_well`), where a stop in them is the
    last thing it says; a record, never the outcome."""
    try:
        from fastmdxplora.gui.telemetry import phases_after_ended_well

        phases_after_ended_well(root)
    except Exception:  # noqa: BLE001 - a record, never the outcome
        return


#: Beside the joined trajectory from before the pieces are joined until the
#: study's analyses and report of the join have run: a study stopped between
#: is owed them, and `fastmdx resume` runs them (``did: analysed``).
ANALYSIS_OWED = "analysis_owed.json"


def analysis_owed(root: str | Path) -> bool:
    """Whether the study's analyses and report of its joined pieces are
    owed: the one reading `fastmdx resume` and the GUI take.

    Owed where a join of its pieces was begun and they have not run since
    (:data:`ANALYSIS_OWED`), and where a study stopped after its join by a
    version that wrote no such marker left an analysis that is not of the
    join: its `analysis/analysis_manifest.json` names a first piece's
    trajectory (`simulation/production.dcd`), or the join read whole (no
    stride, first or last frame) at a count of frames other than
    `joined/joined.json`'s. An analysis the person chose is not owed
    (`_chosen_by_the_person`). A study with no join, or no analysis
    recorded, owes none by this reading.

    Owed is not going: the GUI asks `being_carried_on` before it says so."""
    root = Path(root)
    try:
        if _records.is_file(root / "joined" / ANALYSIS_OWED):
            return True
        return _analysis_not_of_the_join(root)
    except OSError:
        return False


#: What `_analysis_not_of_the_join` read, by the two records' times and
#: sizes: the GUI asks at every poll of a page.
_NOT_OF_THE_JOIN: dict[tuple[Any, ...], bool] = {}


def _analysis_not_of_the_join(root: Path) -> bool:
    import json

    joined = root / "joined" / "joined.json"
    manifest = root / "analysis" / "analysis_manifest.json"
    if not _records.is_file(joined) or not _records.is_file(manifest):
        return False
    try:
        key = (str(root), *((s.st_mtime_ns, s.st_size) for s in
                            (_records.stat(joined), _records.stat(manifest))),
               _config_stamp(root), _config_stamp(root, "manifest.json"))
    except OSError:
        return False
    # Read once: a clear by another of the GUI's threads between a test of
    # the key and its read raised KeyError in the status route.
    cached = _NOT_OF_THE_JOIN.get(key)
    if cached is not None:
        return cached
    owed = False
    try:
        frames = json.loads(_records.read_text(joined, encoding="utf-8")).get("frames")
        record = json.loads(_records.read_text(manifest, encoding="utf-8"))
    except (OSError, ValueError, AttributeError):
        record = None
    if isinstance(record, dict):
        given = record.get("trajectory_input")
        parts = Path(given).parts if isinstance(given, str) and given else ()
        # A first piece's frames, the study's own or, in a copy made by an
        # older version, the original's; a piece carried on is never
        # analysed alone by a study.
        first = (parts[-2:] == ("simulation", "production.dcd")
                 and not (len(parts) > 2 and parts[-3].startswith("segment-")))
        loaded = record.get("load_kwargs") if isinstance(record.get("load_kwargs"), dict) else {}
        whole = all(loaded.get(key) is None for key in ("stride", "first", "last"))
        counted = record.get("n_frames")
        short = (parts[-2:] == ("joined", "production.dcd") and whole
                 and isinstance(frames, int) and not isinstance(frames, bool)
                 and isinstance(counted, int) and not isinstance(counted, bool)
                 and counted != frames)
        owed = (first or short) and not _chosen_by_the_person(root, given, joined)
    if len(_NOT_OF_THE_JOIN) > 512:
        _NOT_OF_THE_JOIN.clear()
    _NOT_OF_THE_JOIN[key] = owed
    return owed


def _config_stamp(root: Path, name: str = "resolved_config.yml") -> tuple[int, int] | None:
    """The study's resolved config's time and size, which name the
    trajectory a person chose (or its Manifest's, which says when its
    analysis began): None where it is not there."""
    try:
        stat = _records.stat(root / name)
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def _chosen_by_the_person(root: Path, given: Any, joined: Path) -> bool:
    """Whether the analysis on record is of a trajectory the person named
    (`fastmdx analyze --rerun --trajectory`), not one a join owes: the
    study's resolved config names it as `analysis.trajectory`, and it was
    begun after the join (`_analysed_after_the_join`).

    A study's own first run names its first piece there too, and a version
    that wrote no marker left it so when stopped after its join; that
    analysis was written before the join, and is owed. The join's own
    analysis names the join, and the study's config is put back without
    it, so a config that names no trajectory, or the join, chose none."""
    import os

    import yaml

    if not isinstance(given, str) or not given:
        return False
    try:
        config = yaml.safe_load(_records.read_text(root / "resolved_config.yml",
                                                   encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError):
        return False
    analysis = config.get("analysis") if isinstance(config, dict) else None
    named = analysis.get("trajectory") if isinstance(analysis, dict) else None
    if not isinstance(named, str) or not named:
        return False

    def where(path: str) -> str:
        return os.path.normpath(os.path.join(str(root), os.path.expanduser(path)))

    if where(named) != where(given) or where(named) == where(str(root / "joined" / "production.dcd")):
        return False
    return _analysed_after_the_join(root, joined)


def _recorded_time(path: Path, phase: str, key: str) -> float | None:
    """``key`` (`started_at`, `finished_at`) of ``phase`` in the Manifest
    at ``path``, as a time; None where it is not recorded."""
    import json

    try:
        record = json.loads(_records.read_text(path, encoding="utf-8"))
    except (OSError, ValueError):
        return None
    phases = record.get("phases") if isinstance(record, dict) else None
    for entry in phases if isinstance(phases, list) else []:
        if isinstance(entry, dict) and (entry.get("name") or entry.get("phase")) == phase:
            return _a_time(entry.get(key))
    return None


def _a_time(value: Any) -> float | None:
    from datetime import datetime, timezone

    if not isinstance(value, str) or not value:
        return None
    try:
        when = datetime.fromisoformat(value)
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.timestamp()


def _analysed_after_the_join(root: Path, joined: Path) -> bool:
    """Whether the study's analysis on record was begun after its pieces
    were joined, by the times the records hold, which a copy carries: its
    Manifest's analysis `started_at` against the join's `joined_at`, or,
    from a version that wrote none, the last piece's simulation
    `finished_at`. The files' own times are not read: `cp -r`, `scp -r`
    or a download without them writes the files in their copy order.
    Not after where either time is not recorded."""
    import json

    started = _recorded_time(root / "manifest.json", "analysis", "started_at")
    if started is None:
        return False
    try:
        record = json.loads(_records.read_text(joined, encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(record, dict):
        return False
    joined_at = _a_time(record.get("joined_at"))
    if joined_at is None:
        pieces = [index for index in record.get("segments") or []
                  if isinstance(index, int) and not isinstance(index, bool) and index > 0]
        if not pieces:
            return False
        joined_at = _recorded_time(root / f"segment-{max(pieces):03d}" / "manifest.json",
                                   "simulation", "finished_at")
    return joined_at is not None and started > joined_at


def _the_analysis_chosen(root: Path) -> bool:
    """Whether the analysis on record is one the person chose after the
    join, and no join's analyses are owed by its marker: the reading
    `analysis_owed` takes, so resume and the GUI agree."""
    import json

    if _records.is_file(root / "joined" / ANALYSIS_OWED):
        return False
    joined = root / "joined" / "joined.json"
    try:
        record = json.loads(_records.read_text(root / "analysis" / "analysis_manifest.json",
                                               encoding="utf-8"))
    except (OSError, ValueError):
        return False
    given = record.get("trajectory_input") if isinstance(record, dict) else None
    return _records.is_file(joined) and _chosen_by_the_person(root, given, joined)


def _report_of_the_chosen(root: Path) -> dict[str, Any]:
    """The study's report written over the analysis the person chose, as
    `fastmdx report --output` writes it, from the study's config as the
    join's analyses run from it (`_keep_the_study_s_config`): its whole
    length, never a step count beside a duration it disagrees with, which
    an analyze of an extended study by an earlier version wrote and the
    config's check refuses. A refusal is the answer, never a traceback."""
    from fastmdxplora import FastMDXplora
    from fastmdxplora.refusals import refusal_of

    resolved = root / "resolved_config.yml"
    whole_text = _keep_the_study_s_config(root, _the_study_s_config_text(root), chosen=True)
    import yaml

    try:
        own = yaml.safe_load(whole_text) if whole_text is not None else None
    except yaml.YAMLError:
        own = None
    if not isinstance(own, dict):
        return {"ok": False, "stage": "report",
                "error": "The study's resolved_config.yml does not read as a config, so "
                         "its report is not written: correct it and resume."}
    joined_dir = root / "joined"
    _write_atomically(joined_dir / STUDY_CONFIG_COPY, whole_text)
    whole = dict(own)
    whole["output"] = str(root)
    whole["include_phase"] = ["report"]
    whole.pop("exclude_phase", None)
    try:
        ran = FastMDXplora(config_data=whole, output_dir=str(root)).explore(force=True)
    except Exception as exc:  # noqa: BLE001 - said as the answer, as every stage's
        said = refusal_of(exc)
        ran = None
    try:
        _write_atomically(resolved, whole_text)
        (joined_dir / STUDY_CONFIG_COPY).unlink(missing_ok=True)
    except OSError:
        pass
    if ran is None:
        return {"ok": False, "stage": "report", "error": said.message,
                "refusal": said.as_dict()}
    failed = [result for result in ran if result.status != "ok"]
    if failed:
        return {"ok": False, "stage": "report",
                "error": failed[0].message or "The report did not finish."}
    return {"ok": True, "reported_only": True,
            "detail": "Production was complete and its analysis is the one chosen "
                      "after the join (fastmdx analyze --rerun --trajectory); its report "
                      "was written over it."}


#: The study's config at its whole length, beside the joined trajectory
#: while its analyses run again: where they are stopped before it is put
#: back in `resolved_config.yml`, the next join reads it from here.
STUDY_CONFIG_COPY = "study_resolved_config.yml"


def _the_study_s_config_text(root: Path) -> str | None:
    """The study's own `resolved_config.yml` as text: the copy a join left
    beside the joined trajectory where its analysis was stopped before the
    study's was put back, else the file; None where neither reads."""
    for path in (root / "joined" / STUDY_CONFIG_COPY, root / "resolved_config.yml"):
        try:
            return _records.read_text(path, encoding="utf-8")
        except (OSError, ValueError):
            continue
    return None


def _write_atomically(path: Path, text: str) -> None:
    """``text`` into ``path`` through a file beside it, so a reader finds
    the old or the new, never half of one."""
    partial = path.with_name(path.name + ".new")
    partial.write_text(text, encoding="utf-8")
    partial.replace(path)


def _keep_the_study_s_config(root: Path, kept: str | None, *,
                             chosen: bool = False) -> str | None:
    """The study's resolved config as the study, not as its last piece,
    put in its place; the text written, None where it is not a config.

    Analysing the joined trajectory ran the study's analysis and report
    from the last piece's config, and that run wrote it over the study's
    `resolved_config.yml`: its `resume_from`, its own length, no
    minimisation or equilibration, and the joined trajectory's path. The
    file kept so the study can be run again then described the last
    extension. Put back as the study wrote it, with `duration_ns` the
    production of every piece, it runs the study to its whole length; an
    extended study analysed again in place finds its joined trajectory
    itself (`analysis.analyze.study_trajectory`). Each write is whole, so a
    reader never finds half a file. ``chosen`` leaves its `analysis` as
    it is: the trajectory the person chose is how that analysis is known
    as theirs (`_chosen_by_the_person`).
    """
    import yaml

    if not kept:
        return None
    resolved = root / "resolved_config.yml"
    header = "".join(line for line in kept.splitlines(keepends=True)
                     if line.startswith("#"))
    try:
        config = yaml.safe_load(kept)
    except yaml.YAMLError:
        config = None
    try:
        # Put back first: what the pieces add up to is counted against the
        # study's own plan, its equilibration included.
        _write_atomically(resolved, kept)
        if not isinstance(config, dict):
            return None
        simulation = config.get("simulation")
        simulation = dict(simulation) if isinstance(simulation, dict) else {}
        simulation.pop("production_steps", None)
        simulation["duration_ns"] = round(production_done_ns(root), 9)
        config["simulation"] = simulation
        if isinstance(config.get("analysis"), dict) and not chosen:
            from fastmdxplora.config.recorded import leave_the_study_s_own_files

            # Its own frames, found where it is (a copy named the original's).
            config["analysis"] = dict(config["analysis"])
            leave_the_study_s_own_files(config["analysis"], config.get("output"), root)
        text = header + yaml.safe_dump(config, sort_keys=False, default_flow_style=False)
        _write_atomically(resolved, text)
        return text
    except OSError:
        from fastmdxplora.utils.logging import get_logger

        get_logger(__name__).warning(
            "The study's resolved config could not be written back.")
        return None


def _production_carried_on(root: Path, joined: dict[str, Any]) -> None:
    """Record a production that stopped and was carried on as done.

    The study's Manifest kept the simulation phase as it stopped, an
    interruption, beside the analyses of the joined trajectory. So a second
    `fastmdx resume` read a study still to carry on, and tried to join the
    stopped piece again, which the join refuses. The phase now says it was
    carried on and joined, and keeps the stop it came back from.
    """
    import json

    manifest = root / "manifest.json"
    try:
        record = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    changed = False
    for phase in record.get("phases") or []:
        refusal = phase.get("refusal") if isinstance(phase, dict) else None
        if (phase.get("name") == "simulation" and phase.get("status") == "error"
                and isinstance(refusal, dict) and refusal.get("retryable") is True):
            phase["status"] = "ok"
            phase["carried_on_from"] = {"message": phase.get("message"),
                                        "refusal": refusal}
            phase["refusal"] = None
            phase["message"] = (
                "Production stopped and was carried on from its checkpoint; "
                f"segments {joined.get('segments')} are joined in "
                "joined/production.dcd "
                f"({joined.get('frames')} frames).")
            changed = True
    if changed:
        partial = manifest.with_name(manifest.name + ".new")
        partial.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
        partial.replace(manifest)


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
        record = json.loads(_records.read_text(
            simulation / "simulation_parameters.json", encoding="utf-8"))
        found = whole((record.get("resolved") or {}).get("trajectory_interval_steps"))
        if found:
            return found
    except (OSError, ValueError, AttributeError):
        pass
    try:
        config = yaml.safe_load(_records.read_text(
            folder / "resolved_config.yml", encoding="utf-8")) or {}
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
        with _records.open_text(simulation / "live_metrics.csv", encoding="utf-8") as handle:
            rows = [(int(float(r["step"])), int(float(r["current_frame_count"])))
                    for r in csv.DictReader(handle)
                    if str(r.get("stage", "")).lower().startswith("production")
                    and r.get("step") and r.get("current_frame_count")]
        status = json.loads(_records.read_text(simulation / "live_status.json", encoding="utf-8"))
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


# ---------------------------------------------------------------------------
# Carrying on a study that was stopped.
#
# A rented GPU can be taken back part-way through a run, a machine can
# restart, a job can reach its time limit. What is on disk then says how
# far the study got, and that decides what carrying it on means: nothing,
# if it finished; the analyses and report, if production did; the rest of
# production from the last sealed checkpoint, if it had started; the whole
# study again, if it had not. One command reads which, so a service that
# restarts an interrupted job runs the same thing every time.
# ---------------------------------------------------------------------------

#: What `resume_study` did, as a program reads it.
RESUMED_NOTHING = "nothing"
RESUMED_ANALYSIS = "analysed"
#: Only the report, over an analysis the person chose after the join.
RESUMED_REPORT = "reported"
RESUMED_PRODUCTION = "continued"
#: Its stopping rule applied to its runs as they stand, nothing simulated.
RESUMED_JUDGED = "judged"
RESUMED_FROM_START = "restarted"


#: The name a last segment that never reached its first checkpoint is kept
#: under, set aside: neither the join nor a carry-on reads it as a segment
#: (its number is not the whole of what follows ``segment-``), and its
#: frames are kept.
NEVER_CHECKPOINTED = "-stopped-before-its-checkpoint"


def _set_aside_a_segment_never_checkpointed(root: Path) -> Path | None:
    """The study's last extension, set aside where it stopped before its
    first checkpoint; None where there is none such.

    Such a segment carries nothing a run can go on from, and the piece
    before it still has the checkpoint it began from. Left in place, it
    was read as the place to carry on from, refused for want of a
    checkpoint, and the study was run again from its start, its
    production overwritten. Set aside, the study is carried on from the
    piece before it, and what that segment wrote is kept beside it.
    """
    from fastmdxplora.analysis.joining import survey_segments

    pieces = survey_segments(root)
    if len(pieces) < 2:
        return None
    last = max(pieces, key=lambda piece: piece.index)
    if last.index < 1 or last.directory == root:
        return None
    from fastmdxplora.simulation.runner import read_checkpoint_sidecar

    simulation = last.directory / "simulation"
    checkpoint = simulation / "checkpoint.chk"
    # A checkpoint to go on from, or the state of a segment that ran to
    # its end with its last checkpoint not written: neither is set aside.
    # A checkpoint whose record of itself was never written (the piece
    # killed as it wrote it) is none to go on from, as it counts nothing.
    said = str((read_checkpoint_sidecar(checkpoint) or {}).get("stage") or "")
    if ((checkpoint.is_file() and said == "production")
            or (simulation / "state_final.xml").is_file()):
        return None
    # Asked again just before it is moved: a run begun since the first ask.
    if _still_running(last.directory):
        return None
    aside = last.directory.with_name(last.directory.name + NEVER_CHECKPOINTED)
    n = 2
    while aside.exists():
        aside = last.directory.with_name(f"{last.directory.name}{NEVER_CHECKPOINTED}-{n}")
        n += 1
    last.directory.rename(aside)
    return aside


def _length_asked(root: Path, checkpoint: str | None) -> float | None:
    """The length the newest segment set aside from ``checkpoint`` was
    asked to run; None where none was.

    Read from every segment set aside, not only one set aside now: a resume
    that stopped short of carrying it on leaves it set aside, and the next
    one still runs it as it was asked. The newest is the one set aside
    last, by the number its name was given, not by its folder's time,
    which a copy of the study changes."""
    import re

    if not checkpoint:
        return None
    named = re.compile(r"segment-(\d+)" + re.escape(NEVER_CHECKPOINTED) + r"(?:-(\d+))?")
    asides = []
    for path in root.glob(f"segment-*{NEVER_CHECKPOINTED}*"):
        found = named.fullmatch(path.name)
        if found and path.is_dir():
            asides.append(((int(found.group(1)), int(found.group(2) or 1)), path))
    for _, aside in sorted(asides, reverse=True):
        asked, began, ran_in = _length_asked_of(aside)
        if asked is not None and _the_same_checkpoint(began, ran_in, checkpoint, root):
            return asked
    return None


def _the_same_checkpoint(began: str | None, ran_in: str | None, checkpoint: str,
                         root: Path) -> bool:
    """Whether a segment's recorded checkpoint is ``checkpoint``: as written,
    or, the study moved since, the same file within the study. ``ran_in`` is
    where the study was when the segment ran (the folder above the
    segment's own); the checkpoint's place within it must be the same."""
    if not began:
        return False
    if began == checkpoint:
        return True
    if not ran_in:
        return False
    try:
        then = Path(began).relative_to(Path(ran_in))
        now = Path(checkpoint).resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return then == now


def _length_asked_of(aside: Path) -> tuple[float | None, str | None, str | None]:
    """The length a set-aside segment was asked to run, the checkpoint it
    ran from, and where the study was as it ran, as its config recorded
    them; Nones where unreadable."""
    import yaml

    try:
        config = yaml.safe_load((aside / "resolved_config.yml").read_text(encoding="utf-8"))
        simulation = config.get("simulation") or {}
        output = str(config.get("output") or "")
        return (float(simulation["duration_ns"]), str(simulation.get("resume_from") or ""),
                str(Path(output).parent) if output else None)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, yaml.YAMLError):
        return None, None, None


def _checkpoint_interval_asked(root: Path) -> int | None:
    """The checkpoint interval the study's newest extension was asked to run
    at (its ``segment-NNN.yml``, which stays where its folder was set
    aside), so the piece that carries it on writes its checkpoints as
    often; None where none was asked or the config does not read."""
    import re

    import yaml

    newest = None
    for path in _records.glob(root, "segment-*.yml"):
        found = re.fullmatch(r"segment-(\d+)\.yml", path.name)
        if found and (newest is None or int(found.group(1)) > newest[0]):
            newest = (int(found.group(1)), path)
    if newest is None:
        return None
    try:
        config = yaml.safe_load(_records.read_text(newest[1], encoding="utf-8"))
        value = config["simulation"]["checkpoint_interval_steps"]
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError):
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _owed_by_the_last_piece(root: Path) -> float | None:
    """What the study's last extension was asked to run and did not, where
    it stopped past a checkpoint; None for the study's own piece, a piece
    that finished, or one with no checkpoint."""
    import yaml

    from fastmdxplora.analysis.joining import survey_segments

    pieces = survey_segments(root)
    if not pieces:
        return None
    piece = max(pieces, key=lambda each: each.index)
    last = piece.directory
    if (last == root or piece.finished
            or not (last / "simulation" / "checkpoint.chk").is_file()):
        return None
    try:
        config = yaml.safe_load((last / "resolved_config.yml").read_text(encoding="utf-8"))
        asked = float((config.get("simulation") or {})["duration_ns"])
    except (OSError, ValueError, TypeError, KeyError, AttributeError, yaml.YAMLError):
        return None
    owed = round(asked - continuation_of(last).production_done_ns, 9)
    return owed if owed > 0 else None


def _the_last_piece_s_answer(root: Path, *, lengths: bool = True
                             ) -> tuple[str, str, float, float] | None:
    """(its folder's name, why it stopped, the length it was asked to run
    and how far past the study's plan that reaches, in ns) where the
    study's last numbered piece stopped
    with an answer, not an interruption: its own Manifest says an error the
    registry does not mark as worth retrying. A piece killed writes no
    Manifest, and reads as an interruption."""
    import yaml

    from fastmdxplora.analysis.joining import survey_segments

    pieces = [piece for piece in survey_segments(root) if piece.directory != root]
    if not pieces:
        return None
    found = max(pieces, key=lambda each: each.index)
    last = found.directory
    try:
        config = yaml.safe_load(_records.read_text(last / "resolved_config.yml",
                                                   encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        config = {}
    config = config if isinstance(config, dict) else {}
    _finished, stopped = _finished_record(last, config)
    if stopped is None:
        return None
    if not lengths:
        return last.name, stopped, 0.0, 0.0
    # Where the piece was asked to end, against the plan: the production
    # of every piece before it, and its own length.
    past = asked = 0.0
    try:
        asked = float((config.get("simulation") or {})["duration_ns"])
        plan = yaml.safe_load((root / "resolved_config.yml").read_text(encoding="utf-8")) or {}
        planned = float((plan.get("simulation") or {})["duration_ns"])
        from fastmdxplora.simulation.runner import read_checkpoint_sidecar

        before = production_done_ns(root)
        # Taken off as production_done_ns counted it: a piece not finished
        # counts only from a checkpoint written in production.
        checkpoint = last / "simulation" / "checkpoint.chk"
        if found.finished or (checkpoint.is_file() and str(
                (read_checkpoint_sidecar(checkpoint) or {}).get("stage") or "") == "production"):
            own = continuation_of(last).production_done_ns
            before -= own if own else asked
        past = round(max(0.0, before + asked - planned), 9)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, yaml.YAMLError):
        past = 0.0
    return last.name, stopped, asked, past


def _extended_since_it_finished(root: Path) -> bool:
    """Whether the study was extended after its record last said it was
    done: a piece the join has not taken in (the extension stopped before it
    finished), or a piece set aside that its carry-on has not yet run again.

    The study's Manifest is written by the join, so an extension killed
    before it is joined leaves the Manifest of the study as it was, every
    phase done: the study read as finished, its extension was never carried
    on, and extending it again was refused for the piece left behind."""
    import json

    from fastmdxplora.analysis.joining import survey_segments

    try:
        numbered = [piece.index for piece in survey_segments(root) if piece.index >= 1]
    except OSError:
        return False
    if numbered:
        try:
            joined = json.loads(_records.read_text(root / "joined" / "joined.json",
                                                   encoding="utf-8"))
            taken = {int(index) for index in joined.get("segments") or []}
        except (OSError, ValueError, TypeError, AttributeError):
            taken = set()
        if max(numbered) not in taken:
            return True
    if not any(_records.is_dir(path)
               for path in _records.glob(root, f"segment-*{NEVER_CHECKPOINTED}*")):
        return False
    try:
        checkpoint = extension_of(root).checkpoint
    except Exception:  # noqa: BLE001 - a study no plan reads is not extended here
        return False
    return _length_asked(root, checkpoint) is not None


def _extended(root: Path) -> bool:
    """Whether the study was carried on in a piece of its own, one set
    aside included: a resume refused once must be refused the next time,
    not take the study for one never extended and run it over its frames."""
    from fastmdxplora.analysis.joining import survey_segments

    if any(path.is_dir() for path in root.glob("segment-*")):
        return True
    try:
        return any(piece.index >= 1 for piece in survey_segments(root))
    except OSError:
        return True


def _production_written(root: Path) -> bool:
    """Whether any piece of the study wrote production frames."""
    from fastmdxplora.analysis.joining import survey_segments

    try:
        return any(piece.trajectory is not None for piece in survey_segments(root))
    except OSError:
        return True


def _still_running(root: Path) -> bool:
    """Whether a run of this study is alive, by the record it keeps while
    it runs and the process that record names.

    Not when the record was written on another machine or before this one
    booted, and never this resume or what started it: in a new container,
    where process numbers start again, the old record's number can be the
    resume's own or its shell's, and the study was refused as still
    running by the very command sent to carry it on."""
    import json

    from fastmdxplora.orchestrator import RUN_PROCESS_FILE, record_is_from_elsewhere

    try:
        record = json.loads(_held_alone(root / RUN_PROCESS_FILE))
    except (OSError, ValueError):
        return False
    pid = record.get("pid") if isinstance(record, dict) else None
    if not isinstance(pid, int) or pid <= 0:
        return False
    if record_is_from_elsewhere(record) or pid in _this_process_and_its_parents():
        return False
    from fastmdxplora.gui.exploration import _process_is_this_run

    return bool(_process_is_this_run(pid, root, record))


def _held_alone(path: Path) -> str:
    """A record's text, read where the read is held (`record_access`), or,
    where that hold does not admit it (a run's page asking of its study),
    in a hold of its own over that record only, never through a link: a
    reader held to a run has nothing refused for it."""
    from fastmdxplora import record_access

    scope = record_access._scope.get()
    try:
        inside = scope is None or path.absolute().is_relative_to(scope.root)
    except (OSError, ValueError):
        inside = True
    if inside:
        # Where the read is held: refused there as any record of it is.
        return record_access.read_text(path, encoding="utf-8")
    held = path.parent.resolve()
    with record_access.confined_records(
            path.parent, lambda place: place.parent == held and place.name == path.name):
        return record_access.read_text(path, encoding="utf-8")


def _the_resume_that_died(root: Path, started: dict[str, Any]) -> bool:
    """Whether a run's process record is that of a resume that carried its
    study on and died (`stopping.CARRIED_ON_BY`, its marker left): a record
    of that resume, not a run of it that never wrote its end, so the run
    reads as its Manifest says (its rule owed, `stopping.rule_owed`)."""
    import json

    from fastmdxplora.batch.explorer import campaign_of
    from fastmdxplora.simulation.stopping import CARRIED_ON_BY, RECORD, _a_resume_died

    study = campaign_of(root) or root
    try:
        record = json.loads(_held_alone(study / RECORD))
    except (OSError, ValueError):
        return False
    if not isinstance(record, dict) or not _a_resume_died(record):
        return False
    marked = record.get(CARRIED_ON_BY)
    return isinstance(marked, dict) and marked.get("pid") == started.get("pid")


def _this_process_and_its_parents() -> set[int]:
    """This process and every process above it, as far as can be read, but
    not process 1: a container's first process can be the run itself, still
    going, with this resume started beside it."""
    import os

    found = {os.getpid(), os.getppid()} - ({1} if os.getpid() != 1 else set())
    pid = os.getppid()
    for _ in range(64):
        try:
            # The parent is the fourth field; the name before it is in
            # parentheses and may hold spaces.
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
            pid = int(stat.rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            break
        if pid <= 1 or pid in found:
            break
        found.add(pid)
    return found


def _finished_record(root: Path, config: dict[str, Any]) -> tuple[bool, str | None]:
    """(finished, why it stopped) from the study's Manifest.

    The Manifest is written when a study ends: every phase of its plan
    recorded done, or one that stopped with a refusal or an error. That is
    an answer, not an interruption, and resuming would repeat it -- unless
    the refusal is one the registry marks as worth retrying, such as a GPU
    that went away, which is what an interruption looks like from inside a
    run. No Manifest, one missing a phase of the plan, or one older than the
    run that was stopped (whose record of itself is left behind when it is
    killed) is a run that never got to say.
    """
    import json
    from datetime import datetime

    from fastmdxplora.orchestrator import PHASES, RUN_PROCESS_FILE

    manifest = root / "manifest.json"
    try:
        record = json.loads(_records.read_text(manifest, encoding="utf-8"))
    except (OSError, ValueError):
        return False, None
    try:
        started = json.loads(_records.read_text(root / RUN_PROCESS_FILE, encoding="utf-8"))
        began = datetime.fromisoformat(str(started["started_at"])).timestamp()
        if _records.stat(manifest).st_mtime < began and not _the_resume_that_died(
                root, started):
            return False, None
    except (OSError, ValueError, KeyError, TypeError):
        pass
    phases = record.get("phases") if isinstance(record, dict) else None
    if not isinstance(phases, list):
        return False, None
    states: dict[str, str] = {}
    for phase in phases:
        if not isinstance(phase, dict):
            continue
        name = str(phase.get("name") or phase.get("phase") or "a phase")
        if stopped_at_its_end(root, phase):
            # Stopped at its last step: its production is whole.
            states[name] = "ok"
            continue
        if phase.get("status") == "error":
            if (phase.get("refusal") or {}).get("retryable") is True:
                return False, None
            return False, f"{name}: {phase.get('message') or 'it stopped with an error'}"
        states[name] = str(phase.get("status"))
    # Carried on in pieces after the run that began production was stopped:
    # that run never wrote its record, and the pieces wrote theirs in their
    # own folders, so the study's record holds only the analyses the join
    # ran. A joined trajectory (`study_trajectory`) is the setup and the
    # production done and put together. The study's config, put back
    # after the join, plans every phase, so they are read here and not from
    # a config that named only the analyses.
    if any(states.get(p) not in ("ok", "skipped") for p in ("setup", "simulation")):
        from fastmdxplora.analysis.analyze import study_trajectory

        if study_trajectory(root)[0] is not None:
            for made in ("setup", "simulation"):
                if states.get(made) not in ("ok", "skipped"):
                    states[made] = "ok"
    include = config.get("include_phase") or config.get("include")
    exclude = config.get("exclude_phase") or config.get("exclude") or []
    planned = [p for p in PHASES if (not include or p in include) and p not in exclude]
    return all(states.get(p) in ("ok", "skipped") for p in planned), None


def _its_stop_at_its_end_said_whole(root: Path) -> bool:
    """A run's Manifest, its simulation stopped when asked at the last step
    of a whole production (`stopped_at_its_end`), written as the run it is
    once a resume has run what came after it: its simulation "ok", the
    stop saved beside it (``stopped_at_its_end``). Every reader of the
    Manifest (the fixes, the Agent's remedies, the run's page, the
    Overview and the timeline) then reads the run as completed, as its
    state does, where they offered `fastmdx resume` for a run with nothing
    to carry on. Whether it was so written."""
    import json

    manifest = root / "manifest.json"
    try:
        # Its own record, never one linked out of the study (rewritten here).
        record = json.loads(_records.read_own_text(manifest, encoding="utf-8"))
    except (OSError, ValueError):
        return False
    phases = record.get("phases") if isinstance(record, dict) else None
    if not isinstance(phases, list):
        return False
    changed = False
    for phase in phases:
        if stopped_at_its_end(root, phase):
            phase["stopped_at_its_end"] = {"message": phase.get("message"),
                                          "refusal": phase.pop("refusal", None)}
            phase["status"] = "ok"
            phase["message"] = ("Production ran to its last step, where it was stopped "
                                "when asked: the run is whole.")
            if not phase.get("artifacts"):
                # What it wrote, as a simulation that ended ok lists it.
                phase["artifacts"] = _written_by_the_simulation(root / "simulation")
            changed = True
    if changed:
        _write_atomically(manifest, json.dumps(record, indent=2))
    return changed


def _written_by_the_simulation(simulation: Path) -> list[str]:
    """The files a simulation stopped at its last step wrote, as its record
    lists them (``artifacts_written``, written at the stop), else as they
    are there."""
    import json

    from fastmdxplora.simulation.pipeline import artifacts_of_a_whole_production

    try:
        said = json.loads(_records.read_own_text(simulation / "simulation_parameters.json",
                                                 encoding="utf-8"))
        written = [str(path) for path in said.get("artifacts_written") or []]
    except (OSError, ValueError, AttributeError, TypeError):
        written = []
    if not written:
        written = artifacts_of_a_whole_production(simulation)
    if "simulation_parameters.json" not in written and (
            simulation / "simulation_parameters.json").is_file():
        written.append("simulation_parameters.json")
    return written


def stopped_at_its_end(root: str | Path, phase: Any) -> bool:
    """Whether a run's Manifest records its simulation stopped when asked
    where its production had reached its end (`joining.piece_finished`, the
    one reading of a stretch of production's end): the stop is said, and
    the simulation is whole, so its state is read from its later phases,
    never stopped for good by it."""
    from fastmdxplora.analysis.joining import piece_finished
    from fastmdxplora.simulation.runner import STOPPED_CODE

    if not isinstance(phase, dict) or phase.get("status") != "error":
        return False
    if str(phase.get("name") or phase.get("phase") or "") != "simulation":
        return False
    if (phase.get("refusal") or {}).get("code") != STOPPED_CODE:
        return False
    try:
        return piece_finished(Path(root) / "simulation")
    except Exception:  # noqa: BLE001 - not known whole
        return False


# Held by the process carrying a study on, for as long as it does: a second
# resume or extension of the same study, from another process, is refused
# rather than running the same stretch into the same next piece.
CARRYING_ON_FILE = ".fastmdxplora-carrying-on"
# Held once per process, not per thread: a second thread of the process
# carrying the same study on is let in (none does today).
_CARRYING = threading.RLock()
_carried_here: dict[Path, int] = {}


#: How often, and how far apart, a resume tries the study's lock before it
#: says a run is still going: a probe holds it for far less than this.
_LOCK_TRIES = 10
_LOCK_WAIT_S = 0.01


class _CarriedOnElsewhere(CodedError, Exception):
    """Another process holds the study's lock: a run of it is still going."""

    default_code = "environment.workspace.run_going"


@contextlib.contextmanager
def _carrying_on(root: Path) -> Iterator[None]:
    """The study's own lock, held in this process (a resume that extends
    takes it again); one held by another process raises
    :class:`_CarriedOnElsewhere`. Where the folder holds no lock (not
    there, not writable, a file system without locks), nothing is held."""
    from fastmdxplora.runs_here import StartRefused, _lock, _unlock

    with _CARRYING:
        handle = None
        # Only a study is locked: a folder that is not one is said to be
        # so, with nothing left in it.
        if not _carried_here.get(root) and (root / "resolved_config.yml").is_file():
            # The GUI asks whether it is held by taking it shared for an
            # instant (`_lock_held`): a resume starting in that instant was
            # refused as still going. Tried again over some 100 ms.
            import time

            for attempt in range(_LOCK_TRIES):
                try:
                    handle = _lock(root / CARRYING_ON_FILE)
                    break
                except StartRefused:
                    if attempt == _LOCK_TRIES - 1:
                        raise _CarriedOnElsewhere() from None
                    time.sleep(_LOCK_WAIT_S)
        _carried_here[root] = _carried_here.get(root, 0) + 1
    try:
        yield
    finally:
        with _CARRYING:
            _carried_here[root] -= 1
            if not _carried_here[root]:
                _carried_here.pop(root)
            if handle is not None:
                _unlock(handle)


def _the_lock_s_text(path: Path) -> str | None:
    """The text of a study's lock, opened with no link followed, without
    blocking, and read only where it is a regular file; None otherwise (no
    lock). Raises OSError where it cannot be opened."""
    import os
    import stat as stat_module

    from fastmdxplora.record_access import _opened_without_links

    descriptor = _opened_without_links(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    if not stat_module.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        return None
    with os.fdopen(descriptor, encoding="utf-8") as stream:
        return stream.read()


def being_carried_on(root: str | Path) -> bool:
    """Whether a run of the study is going now: a resume or an extension
    holds its lock (:data:`CARRYING_ON_FILE`) in this process or another,
    joining its pieces, analysing them or writing their report, or its run
    record names a process that is alive (`_still_running`). Asked by the
    GUI before it says a study owes its analysis: the analysis a resume is
    running is not owed, and `fastmdx resume` would be refused.

    The lock is tried where the process it names is alive on this machine,
    and whatever machine it names otherwise: on a file system shared by
    several (a cluster's nodes, the GUI on another), a lock held anywhere
    is held, and the process there cannot be asked. Where the file system
    does not carry locks between machines it is not seen. A resume
    starting as it is tried tries again (`_carrying_on`). Going too while
    any of its runs' locks is held (a round's extension holds its run's),
    or while its process record is another machine's or container's and
    its records were written within a day (`going_elsewhere`)."""
    root = Path(root).expanduser()
    try:
        root = root.resolve()
    except OSError:
        pass
    with _CARRYING:
        if _carried_here.get(root):
            return True
    if _a_lock_held(root / CARRYING_ON_FILE):
        return True
    # A round's extension holds the lock of the run it extends: the study
    # is going while any of its runs' locks is held.
    if any(_a_lock_held(run / CARRYING_ON_FILE) for run in _its_runs(root)):
        return True
    if going_elsewhere(root):
        return True
    try:
        return _still_running(root)
    except Exception:  # noqa: BLE001 - a record, not a verdict
        return False


def _its_runs(root: Path) -> list[Path]:
    """The folders of a study's runs (``runs/<run>``), where it has any;
    none through a link (a ``runs`` linked out of the study is not walked)."""
    runs = root / "runs"
    try:
        if runs.is_symlink() or not runs.is_dir():
            return []
        return sorted(folder for folder in runs.iterdir()
                      if not folder.is_symlink() and folder.is_dir())
    except OSError:
        return []


def _a_lock_held(path: Path, *, but: set[int] | frozenset[int] = frozenset()) -> bool:
    """Whether another process holds the lock at ``path`` (a study's or a
    run's :data:`CARRYING_ON_FILE`): one on this machine, alive, holding
    it; one on another machine sharing the file system, holding it there.
    Not one of ``but`` (on this machine: this process's and those above
    it, where a worker asks of the study its parent carries on)."""
    import json

    from fastmdxplora.orchestrator import record_is_from_elsewhere

    try:
        if path.is_symlink():
            # A link at the lock's name is no lock of the study's: not
            # opened (a link to a FIFO hung a reader), and not a record it
            # refused either, so every page reads the study the same.
            record = None
        else:
            # The study's own lock, read for the process it names and never
            # shown: not one of its results, so not refused as one (that had
            # the study said "Not read" once its process died holding it),
            # but opened as `record_access` opens a record: no link, without
            # blocking, a regular file only (a FIFO at its name hung the
            # GUI's study list); anything else is no lock.
            record = json.loads(_the_lock_s_text(path) or "null")
    except (OSError, ValueError):
        record = None
    pid = record.get("pid") if isinstance(record, dict) else None
    if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0:
        if record_is_from_elsewhere(record) or _from_another_host(record):
            return _lock_taken(path)
        return pid not in but and _lock_held(path, pid)
    return False


def _the_lock_s_process(path: Path) -> int | None:
    """The process a lock names, as `_a_lock_held` reads it; None where it
    names none (no lock, a link, not a regular file)."""
    import json

    try:
        if path.is_symlink():
            return None
        record = json.loads(_the_lock_s_text(path) or "null")
    except (OSError, ValueError):
        return None
    pid = record.get("pid") if isinstance(record, dict) else None
    return pid if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0 else None


def _the_process_s(study: Path) -> int | None:
    """The process the study's own process record names, where it is this
    machine's; None otherwise."""
    import json

    from fastmdxplora.orchestrator import RUN_PROCESS_FILE, record_is_from_elsewhere

    try:
        record = json.loads(_the_lock_s_text(study / RUN_PROCESS_FILE) or "null")
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict) or record_is_from_elsewhere(record) or _from_another_host(
            record):
        return None
    pid = record.get("pid")
    return pid if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0 else None


def _from_another_host(record: dict[str, Any]) -> bool:
    """Whether a process record was written on another machine, or in a
    process namespace of this boot other than this process's (a container
    beside it): its process cannot be asked from here, so it is not known
    to have ended. A record of this machine before its last boot is not:
    every process of that boot has ended."""
    from fastmdxplora.orchestrator import this_machine

    here = this_machine()
    there = record.get("host")
    if isinstance(there, str) and there and here.get("host") and there != here["host"]:
        return True
    boot = record.get("boot")
    if isinstance(boot, str) and boot and here.get("boot") and boot != here["boot"]:
        return False
    space = record.get("pidns")
    return bool(isinstance(space, str) and space and here.get("pidns")
                and space != here["pidns"])


def _process_records(root: Path) -> list[Path]:
    """Where a study and its runs keep the record of the process running
    them: the study, each run, and each of their pieces."""
    from fastmdxplora.orchestrator import RUN_PROCESS_FILE

    import json

    folders = [root, *_its_runs(root)]
    found = []
    for folder in folders:
        found.append(folder / RUN_PROCESS_FILE)
        # A piece its run's join took in is part of the run, its process
        # long ended (a record a killed process left there is not read as
        # going once a resume finished the run).
        try:
            joined = json.loads(_the_lock_s_text(folder / "joined" / "joined.json") or "null")
            taken = {int(i) for i in (joined.get("segments") or [])} if isinstance(
                joined, dict) else set()
        except (OSError, ValueError, TypeError):
            taken = set()
        try:
            for piece in sorted(folder.glob("segment-*")):
                try:
                    index = int(piece.name.split("-", 1)[1])
                except ValueError:
                    # A piece set aside (``segment-001-stopped-before-its-
                    # checkpoint``) has ended: not read as going.
                    continue
                if index not in taken:
                    found.append(piece / RUN_PROCESS_FILE)
        except OSError:
            pass
    return found


def another_host_said(root: str | Path) -> str:
    """Where the process running the study or one of its runs was recorded,
    where that is another machine or container ("on node17", "in another
    container of this machine"); empty otherwise. Read without following a
    link, as the lock is."""
    import json

    from fastmdxplora.orchestrator import this_machine

    for path in _process_records(Path(root)):
        try:
            if path.is_symlink():
                continue
            record = json.loads(_the_lock_s_text(path) or "null")
        except (OSError, ValueError):
            continue
        if isinstance(record, dict) and _from_another_host(record):
            host = record.get("host")
            return (f"on {host}" if isinstance(host, str) and host
                    and host != this_machine()["host"]
                    else "in another container of this machine")
    return ""


#: How long a study whose process record is another machine's or
#: container's may go unwritten before it is taken to have ended there,
#: and read as its records say it ended (the user's decision of 11-05):
#: an hour. `fastmdx resume --taken-over` carries it on before that, for a
#: person who knows its process there has ended.
ANOTHER_HOST_QUIET_SECONDS = 3600


def _last_written(root: Path) -> float | None:
    """When the study's records were last written, on one clock with
    telemetry's (`telemetry.how_the_run_ended`): the newest of each process
    record's heartbeat (``alive_at``) and each live record's own
    `last_update_timestamp` (of the study, its runs and their pieces), the
    stamps the writing machine's clock gave, so a copy that does not keep
    file times reads the same; where none says one, when its process and
    round records were last written."""
    import json
    import os
    import time

    from fastmdxplora.gui.telemetry import STATUS_FILE, _timestamp_age_seconds

    folders = {path.parent for path in _process_records(root)}
    stamps = []
    # The heartbeat each live process stamps in its own record in every
    # phase (`orchestrator.stamp_alive`, ``alive_at``), on the clock that
    # wrote it: a long setup, analysis or join writes no live record.
    for path in _process_records(root):
        try:
            record = json.loads(_the_lock_s_text(path) or "null")
        except (OSError, ValueError):
            continue
        if isinstance(record, dict):
            for key in ("alive_at", "started_at"):
                age = _timestamp_age_seconds(str(record.get(key) or ""))
                if age is not None:
                    stamps.append(time.time() - age)
    for folder in folders:
        try:
            record = json.loads(_the_lock_s_text(folder / "simulation" / STATUS_FILE) or "null")
        except (OSError, ValueError):
            continue
        age = _timestamp_age_seconds(str(record.get("last_update_timestamp") or "")) if (
            isinstance(record, dict)) else None
        if age is not None:
            stamps.append(time.time() - age)
    if stamps:
        return max(stamps)
    written = []
    for place in [*_process_records(root), root / "stopping.json"]:
        try:
            written.append(os.lstat(place).st_mtime)
        except OSError:
            continue
    return max(written) if written else None


def _clock(when: float) -> str:
    """A time of day as this machine says it, its zone named (``14:05
    UTC+01:00``, as a page reached from another zone reads it), with its
    date where it is not today."""
    import time

    local = time.localtime(when)
    day = time.strftime("%Y-%m-%d", local)
    offset = time.strftime("%z", local)
    zone = f"UTC{offset[:3]}:{offset[3:]}" if len(offset) == 5 else "UTC"
    said = f"{time.strftime('%H:%M', local)} {zone}"
    return said if day == time.strftime("%Y-%m-%d") else f"{day} {said}"


def going_elsewhere(root: str | Path) -> str:
    """The sentence of a study going on another machine or container: its
    process record is another's (`another_host_said`), which cannot be asked
    from here, and its records were written within
    `ANOTHER_HOST_QUIET_SECONDS` (`_last_written`), with when it can be
    resumed from here and how before then. Empty otherwise: once quiet it
    reads as its records say it ended."""
    import shlex
    import time

    root = Path(root)
    where = another_host_said(root)
    last = _last_written(root) if where else None
    if last is None or time.time() - last > ANOTHER_HOST_QUIET_SECONDS:
        return ""
    return (f"It is going {where}, which cannot be asked from here: its records were last "
            f"written at {_clock(last)}. It can be resumed from here after "
            f"{_clock(last + ANOTHER_HOST_QUIET_SECONDS)}, or now with `fastmdx resume "
            f"--taken-over {shlex.quote(str(root))}` once it has ended there.")


#: Why `fastmdx resume --taken-over` is refused: it overrides only a
#: process on another machine or container.
_HELD_HERE = ("A process on this machine holds this study; --taken-over carries on only a "
              "study whose process was on another machine or container.")


def _held_elsewhere(root: Path, *, taken_over: bool = False) -> str:
    """Why a resume of ``root`` (a study, or a run of one) is refused before
    it writes anything: a process on this machine running it, the study's
    lock or one of its runs' held by another process, or the study going on
    another machine; empty where nothing else carries it on. This process's
    own holds are its own. ``taken_over``: the person knows the process on
    the other machine or container has ended, so only what is held on this
    machine refuses it."""
    from fastmdxplora.batch.explorer import campaign_of

    study = campaign_of(root) or root
    with _CARRYING:
        if _carried_here.get(study) or _carried_here.get(root):
            return ""
    # A run of the study resumed by a worker of the resume that holds the
    # study's lock: that resume's own, and so are its other runs' locks,
    # held by its other workers.
    above = _this_process_and_its_parents()
    if root != study and (_the_lock_s_process(study / CARRYING_ON_FILE) in above
                          or _the_process_s(study) in above):
        # Or of the study's run itself (its rule extending its runs in
        # workers, each holding its run's lock).
        return ""
    refused = _HELD_HERE if taken_over else _STILL_GOING
    try:
        if _still_running(study) or (root != study and _still_running(root)):
            return refused
    except Exception:  # noqa: BLE001 - its locks still say
        pass
    # A lock held now proves its process has not ended, wherever it runs:
    # refused, --taken-over too, naming the lock.
    for lock in [study / CARRYING_ON_FILE, *(run / CARRYING_ON_FILE for run in _its_runs(study))]:
        if _a_lock_held(lock, but=above):
            held = lock.relative_to(study).as_posix()
            return (f"{_STILL_GOING} Its lock {held} is held now." if not taken_over else
                    f"Its lock {held} is held now, so the process holding it has not ended; "
                    "--taken-over carries on only a study no process holds.")
    going = "" if taken_over else going_elsewhere(study)
    return f"{_STILL_GOING} {going}" if going else ""


def _lock_held(path: Path, pid: int) -> bool:
    """Whether the process ``pid``, alive, holds the lock on ``path``."""
    import os

    from fastmdxplora.gui.exploration import _process_alive

    if pid == os.getpid() or not _process_alive(pid):
        return False
    if os.name == "nt":
        # A lock there cannot be tried without taking it: the process the
        # record names, alive, is the answer.
        return True
    return _lock_taken(path)


def _lock_taken(path: Path) -> bool:
    """Whether a process, here or on another machine sharing the file
    system, holds the lock on ``path``: taken shared for an instant. Not
    on Windows, where it cannot be tried without taking it."""
    import os

    if os.name == "nt":
        return False
    import fcntl

    try:
        handle = os.open(path, os.O_RDONLY)
    except OSError:
        return False
    try:
        fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    except OSError:
        return False
    finally:
        os.close(handle)
    return False


_STILL_GOING = "A run of this study is still going. Resume it once it has stopped."
_STILL_GOING_TO_EXTEND = "A run of this study is still going. Extend it once it has stopped."


def resume_study(study: str | Path, *,
                 device_index: str | int | None = None,
                 taken_over: bool = False) -> dict[str, Any]:
    """Carry a study that stopped part-way on to the end of its plan.

    A study being carried on by another process is said, not carried on
    twice.

    Reads how far it got and does what is left, once:

    - finished: nothing (``did`` is ``"nothing"``);
    - production complete, analyses or report not: those, over the whole
      trajectory (``"analysed"``); only the report, over an analysis the
      person chose after the join (``"reported"``);
    - production begun: the rest of it, from the last sealed checkpoint,
      then the join and the analyses, as extending a study does
      (``"continued"``);
    - production not begun, or not as far as its first checkpoint: the
      study again from its start, since nothing it wrote can be continued
      from (``"restarted"``).

    An extension stopped before its first checkpoint is set aside, its
    frames kept (``set_aside`` names where), and the study carried on from
    the piece before it.

    Refused, with ``ok`` false: a folder that is not a study, a study still
    running, a study that stopped with a refusal or an error (that is its
    answer, and running it again would give the same one), and an extended
    study whose production cannot be continued (run again from its start,
    it would overwrite every piece).

    A study of several runs is carried on run by run
    (:func:`resume_batch`). ``device_index`` puts a run's simulation on the
    device given, where a campaign shares out its cards again.
    ``taken_over``: the person knows the process on the other machine or
    container that last ran it has ended (`fastmdx resume --taken-over`);
    what is held on this machine still refuses it.
    """
    root = Path(study).expanduser().resolve()
    # Refused before anything is written while another process carries the
    # study or one of its runs on (a resume rewrote a run's report, then
    # was refused at the run a round was extending).
    held = _held_elsewhere(root, taken_over=taken_over)
    if held:
        return {"study": str(root), "ok": False, "did": RESUMED_NOTHING, "error": held}
    try:
        with _carrying_on(root):
            return _resume_study(root, device_index=device_index)
    except _CarriedOnElsewhere:
        return {"study": str(root), "ok": False, "did": RESUMED_NOTHING, "error": _STILL_GOING}


def _resume_study(study: str | Path, *,
                  device_index: str | int | None = None) -> dict[str, Any]:
    """The work of :func:`resume_study`, under the study's lock.

    A study of one run whose rule's round failed, was stopped or was cut
    (`stopping.round_ended`, which says what this runs): its run carried on
    as any study's is, then its rule applied again from that round, as a
    study of several runs has it applied after its runs are carried on
    (`resume_batch`). Its process record is this resume's while it runs,
    so the study reads going, and a resume cut in turn reads cut. A run
    not carried on leaves the round said as it ended, never running."""
    import yaml

    from fastmdxplora.simulation.stopping import round_ended, round_not_carried_on

    root = Path(study).expanduser().resolve()
    base = {"study": str(root)}
    resolved = root / "resolved_config.yml"
    if not resolved.is_file():
        return {**base, "ok": False, "did": RESUMED_NOTHING,
                "error": f"{root} is not a study to resume: it has no "
                         "resolved_config.yml, which every study writes as it starts."}
    if (root / "batch_manifest.json").is_file():
        return resume_batch(root)
    if _still_running(root) or any(_still_running(piece) for piece in root.glob("segment-*")
                                   if piece.is_dir()):
        return {**base, "ok": False, "did": RESUMED_NOTHING,
                "error": _STILL_GOING}
    # Stopped while its pieces' analyses ran again: the study's own config,
    # at its whole length, is put back before anything reads it.
    # Put back once: left there, every later resume wrote it over an edit
    # made to the study's config since.
    copy = root / "joined" / STUDY_CONFIG_COPY
    try:
        _write_atomically(resolved, _records.read_text(copy, encoding="utf-8"))
        copy.unlink()
    except (OSError, ValueError):
        pass
    owed = None if root in _JUDGED_AGAIN_HERE else round_ended(root, resuming=True)
    if owed is None and (root in _JUDGED_AGAIN_HERE or not _its_rule_not_applied(root)):
        return _carry_the_run_on(root, base, device_index=device_index)
    from fastmdxplora.simulation.stopping import runs_kept_below_the_ceiling

    if owed is not None and runs_kept_below_the_ceiling(root):
        # A ceiling lowered below the length its cut extension runs to:
        # nothing is run, as the round's sentence says.
        return {**base, "ok": False, "did": RESUMED_NOTHING, "error": owed.as_text(),
                "round_ended": owed.as_record()}
    from fastmdxplora.orchestrator import _hold_run_process, _release_run_process

    # Read before the hold: a process record newer than its Manifest says
    # the run never got to say how it ended (`_finished_record`).
    try:
        standing = _where_it_stands(root)
    except (OSError, ValueError, StudyError, yaml.YAMLError):
        standing = None
    finished = standing is not None and not _carried_on_first(standing)
    _hold_run_process(root, rewrite=True)
    from fastmdxplora.simulation.stopping import carried_on_marked

    carried = True
    # Its record says this resume carries it on, before its work: one that
    # dies reads cut, never stopped when asked by an earlier stop's words.
    marked = carried_on_marked(root)
    marked.__enter__()
    try:
        if finished:
            answer = _judge_the_run_again(root, base)
        else:
            answer = _carry_the_run_on(root, base, device_index=device_index,
                                       standing=standing, said_whole=False)
            carried = bool(answer.get("ok"))
            if carried:
                judged = _judge_the_run_again(root, base)
                # What the carrying on did, where the rule then ran nothing.
                answer = {**judged, "did": (answer.get("did") if judged.get("did")
                                            == RESUMED_JUDGED else judged.get("did"))}
    except KeyboardInterrupt as stopped:
        # Ctrl+C outside a production and outside the rule's rounds (a
        # piece's join, the rule about to be applied): a stop asked, said
        # as the study then stands, where it ended in a traceback.
        answer = {**base, "ok": False, "did": RESUMED_NOTHING, "stopped": True,
                  "error": stopped_in_a_resume(stopped)}
        carried = False
    finally:
        marked.__exit__(None, None, None)
        _release_run_process(root)
    if not carried:
        # Said as the round's end where its record said it went on.
        round_not_carried_on(root, "the run", answer)
    # Read with the hold given back: a process record newer than its
    # Manifest read as a run that never said how it ended.
    ended = round_ended(root, resuming=True)
    if ended is not None:
        # What carries it on, as every page says it (its own refusal
        # advised an extension outside the rule, in ns).
        answer.update(ok=False, error=ended.as_text(), round_ended=ended.as_record())
    elif "round_ended" in answer:
        # Said under the hold, which read it so: it ended as the rule decides.
        answer.update(ok=True)
        answer.pop("error", None)
        answer.pop("round_ended", None)
    if ended is None and answer.get("ok"):
        # Its rule applied: the run stopped at its end said whole, last.
        _said_whole_last(root)
    return answer


def _said_whole_last(root: Path) -> bool:
    """What a resume says it finished, written last, after the work it
    stands for (its analyses and report run, its rule applied): the live
    record said as they leave it (`_phases_after_ended_well`), then the
    Manifest's simulation, stopped when asked at its last step, said whole
    (`_its_stop_at_its_end_said_whole`). A resume ended anywhere before the
    Manifest's leaves the run read stopped (`telemetry.status_as_it_stands`
    reads a stop at its end not yet said whole so), with `fastmdx resume`
    offered, and that resume finishes it; the live record is written
    again by it whatever it says. Whether either record was changed."""
    import json

    live = root / "simulation" / "live_status.json"

    def said() -> Any:
        try:
            return json.loads(_records.read_own_text(live, encoding="utf-8")).get("status")
        except (OSError, ValueError, AttributeError):
            return None

    before = said()
    _phases_after_ended_well(root)
    rewritten = _its_stop_at_its_end_said_whole(root)
    return rewritten or said() != before


#: What a resume of one run stopped by Ctrl+C outside its production says.
STOPPED_IN_A_RESUME = ("Stopped when asked (Ctrl+C) before the resume finished; "
                       "`fastmdx resume` carries it on from there.")


def stopped_in_a_resume(stopped: BaseException) -> str:
    """`STOPPED_IN_A_RESUME`, named by the stops it took where it was a stop
    now (`runner.StopNow.said`): a second SIGTERM was said Ctrl+C. A further
    stop once its study heard one (`runner.StoppedAgain`) is named by its
    signal."""
    from fastmdxplora.simulation.runner import StopNow, StoppedAgain

    if isinstance(stopped, StoppedAgain):
        return STOPPED_IN_A_RESUME.replace("(Ctrl+C)", f"({stopped.said()})")
    if not isinstance(stopped, StopNow):
        return STOPPED_IN_A_RESUME
    return STOPPED_IN_A_RESUME.replace("Stopped when asked (Ctrl+C)",
                                       f"Stopped at once when asked ({stopped.said()})")


def _its_rule_not_applied(root: Path) -> bool:
    """Whether a study of one run has a stopping rule its run was stopped
    (or killed) before: its record planned, or said stopped before the rule
    was applied. Its resume carries the run on and then applies the rule,
    as `stopping.json` says, where it ran only the run's analyses."""
    import json

    from fastmdxplora.simulation.stopping import (RECORD, STOPPED_BEFORE_THE_RULE,
                                                  STOPPED_BEFORE_THE_RULE_AGAIN)

    try:
        record = json.loads(_records.read_text(root / RECORD, encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(record, dict) or str(record.get("outcome")) not in ("stopped", "running"):
        return False
    return (not record.get("rounds")
            or record.get("said") in (STOPPED_BEFORE_THE_RULE, STOPPED_BEFORE_THE_RULE_AGAIN))


def _where_it_stands(root: Path, *, lengths: bool = True) -> tuple[bool, str | None, bool, Any]:
    """How a study of one run stands, as its resume reads it: (finished,
    the answer it stopped with, extended since it finished or owing the
    analyses of its pieces, its last piece's own answer). Raises on a
    config not read or pieces that cannot be told apart. Read through
    `record_access`, so a page held to the study reads nothing its links
    lead out to; ``lengths`` False leaves out how far the last piece was
    asked to run, which only the resume's own refusal says."""
    import yaml

    from fastmdxplora.analysis.joining import survey_segments

    config = yaml.safe_load(_records.read_text(root / "resolved_config.yml",
                                               encoding="utf-8")) or {}
    finished, stopped = _finished_record(root, config)
    # Pieces that cannot be told apart (two folders for one number) are
    # said here, as an answer, before anything reads them.
    survey_segments(root)
    # Stopped after its pieces were joined and before their analyses
    # and report had run: not finished, whatever its Manifest says.
    extended = finished and (_extended_since_it_finished(root) or analysis_owed(root))
    # A piece carried on that stopped with an answer of its own gives
    # the same one when run again: said, not set aside and run again,
    # whether the study had finished or not.
    piece = (_the_last_piece_s_answer(root, lengths=lengths)
             if extended or not finished else None)
    return finished, stopped, extended, piece


def _carried_on_first(standing: tuple[bool, str | None, bool, Any]) -> bool:
    """Whether a resume carries the run on (its piece cut, its analyses
    owed) before its rule's round is judged again, from how it stands."""
    finished, stopped, extended, piece = standing
    return not finished or stopped is not None or extended or piece is not None


def how_a_resume_finds(root: str | Path) -> dict[str, Any] | None:
    """What `fastmdx resume` finds in a study of one run (or one run of
    several), as `_carry_the_run_on` reads it, for `stopping.round_ended`
    to say what the resume does: ``piece`` (the folder of a last piece that
    stopped with an answer of its own, given again), ``own`` (the answer
    the run itself stopped with, in its analyses or report, given again),
    ``carried_on_first`` (an extension cut or its pieces' analyses owed,
    carried on before the round is judged), ``analyses_owed`` and
    ``unjoined`` (a piece the join has not taken in). None
    where it is not read."""
    import yaml

    root = Path(root)
    try:
        standing = _where_it_stands(root, lengths=False)
    except (OSError, ValueError, StudyError, yaml.YAMLError):
        return None
    _finished, stopped, _extended, piece = standing
    return {"piece": root / piece[0] if piece is not None else None,
            "own": stopped if piece is None else None,
            "carried_on_first": (_carried_on_first(standing) and stopped is None
                                 and piece is None),
            "analyses_owed": analysis_owed(root),
            # A piece the join has not taken in (one stopped at its last step).
            "unjoined": _extended_since_it_finished(root)}


def _carry_the_run_on(root: Path, base: dict[str, Any], *,
                      device_index: str | int | None = None,
                      standing: tuple[bool, str | None, bool, Any] | None = None,
                      said_whole: bool = True) -> dict[str, Any]:
    """A study of one run carried on to the end of its plan, its rule's
    rounds aside; ``standing`` is how it stood (`_where_it_stands`), where
    read before this resume took its process record. ``said_whole`` False
    leaves a run stopped at its end said stopped, for its rule to be
    applied before it is said whole (`_said_whole_last`)."""
    import yaml

    from fastmdxplora import FastMDXplora
    from fastmdxplora.analysis.joining import survey_segments

    resolved = root / "resolved_config.yml"
    try:
        config = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        return {**base, "ok": False, "did": RESUMED_NOTHING,
                "error": f"The study's config could not be read: {exc}"}
    try:
        finished, stopped, extended, piece_said = standing or _where_it_stands(root)
    except (StudyError, ValueError) as exc:
        return {**base, "ok": False, "did": RESUMED_NOTHING, "error": str(exc)}
    if extended:
        finished = False
    if piece_said is not None:
        name, why, asked, past = piece_said
        why = why.splitlines()[0].strip()
        if not why.endswith((".", "!", "?")):
            why += "."

        def extend(more: float) -> str:
            return (f"(`simulation.resume_from: {root}` with `extra_ns: {more:g}` "
                    "in a config given to fastmdx explore)")

        # Moved out, the piece is not asked for again by a resume: a
        # finished study reads as finished, so its extension is asked for
        # again whole; past an unfinished plan, what reached past it is.
        if extended and asked > 1e-9:
            again = (f"move {name} out of the study's folder and extend the study by "
                     f"{asked:g} ns again {extend(asked)}")
        elif past > 1e-9:
            again = (f"move {name} out of the study's folder, resume it, and extend it "
                     f"by {past:g} ns {extend(past)}")
        else:
            again = f"move {name} out of the study's folder and resume"
        return {**base, "ok": False, "did": RESUMED_NOTHING,
                "error": f"The study's last piece, {name}, stopped with an answer, not an "
                         f"interruption: {why} Running it again "
                         f"would give the same one. To run it again anyway, {again}."}
    if stopped is not None:
        return {**base, "ok": False, "did": RESUMED_NOTHING,
                "error": f"The study stopped with an answer, not an interruption "
                         f"({stopped}). Running it again would give the same one."}
    if finished:
        # Stopped at its end with nothing after it to run (its analyses not
        # asked), or by a resume ended before it said the run whole: said
        # so now, where it said stopped.
        if said_whole and _said_whole_last(root):
            # A resume ended before it said the run whole: its records
            # finished now, said as what this one did, not "Nothing to do".
            return {**base, "ok": True, "did": RESUMED_RECORDS,
                    **_production_now(root, {"ok": True}),
                    "detail": RECORDS_FINISHED}
        return {**base, "ok": True, "did": RESUMED_NOTHING,
                **_production_now(root, {"ok": True}),
                "detail": NOTHING_TO_CARRY_ON}

    # An extension stopped before its first checkpoint left nothing to go
    # on from: the piece before it is carried on instead.
    aside = _set_aside_a_segment_never_checkpointed(root)
    if aside is not None:
        base["set_aside"] = str(aside)
    plan = extension_of(root)
    # A segment set aside runs again as it was asked, an extension past the
    # plan included, not cut back to the plan (nor refused where the plan
    # was already done).
    asked = _length_asked(root, plan.checkpoint)
    if asked is not None:
        plan = extension_of(root, more_ns=asked)
    else:
        # The last piece stopped past its checkpoint, short of the length it
        # was asked to run, and that ask reaches past what is left of the
        # plan (an extension past it): the rest of its own ask, not the
        # plan's remainder (the rest lost for good), nor a join refused for
        # a piece that did not finish at every resume after.
        owed = _owed_by_the_last_piece(root)
        left = (plan.production_planned_ns - plan.production_done_ns) if plan.possible else 0.0
        # A piece's length is written to 6 places (a fraction of one step at
        # any timestep the software runs): within that, it is the plan's.
        if owed is not None and owed > left + 1e-6:
            asked = owed
            plan = extension_of(root, more_ns=asked)
    if plan.possible:
        # Before and after by one reading, so what the resume ran is their
        # difference (by the clock for a variable-step run, where steps
        # times the timestep was given before it and its clock after).
        from fastmdxplora.simulation.lengths import _variable_step

        simulation = config.get("simulation") if isinstance(config, dict) else None
        if _variable_step(simulation if isinstance(simulation, dict) else {}):
            before = _production_now(root, {"ok": True})
        else:
            # A fixed step's is its steps times the timestep, as the
            # checkpoints count them, the pieces' records after it: the same
            # length, given also where the first piece, stopped, wrote none.
            before = {"production_done_ns": round(plan.production_done_ns, 9),
                      "production_done_source": FROM_CHECKPOINT_STEPS}
        # The plan as it now stands: an extension asked for past the study's
        # own plan is part of it (the done and before are the study's
        # totals, so 0.006 planned beside 0.008 done read 2 ps past it).
        planned = plan.production_planned_ns
        if asked is not None:
            planned = max(planned, plan.production_done_ns + float(asked))
        answer = extend_study(root, more_ns=asked, device_index=device_index,
                              checkpoint_interval_steps=_checkpoint_interval_asked(root))
        return {**base, **answer, "did": RESUMED_PRODUCTION,
                **_production_now(root, answer),
                **{key.replace("production_done", "production_done_before"): value
                   for key, value in before.items()},
                "production_planned_ns": round(planned, 9)}

    done = plan.production_done_ns
    planned = plan.production_planned_ns
    if planned and done >= planned - 1e-9:
        # Production is whole; what stopped was the analyses or the report.
        pieces = survey_segments(root)
        if len(pieces) > 1:
            keep_frames, refusal = _frames_to_keep(root, last_too=False)
            if refusal is not None:
                from fastmdxplora.refusals import MissingResultError, refusal_of

                said = refusal_of(MissingResultError(
                    refusal["error"], code="simulation.resume.unsealed", path=str(root)))
                return {**base, "ok": False, "did": RESUMED_ANALYSIS,
                        "stage": "joining", **refusal, "refusal": said.as_dict()}
            if _the_analysis_chosen(root):
                # The person's analysis, chosen after the join (`fastmdx
                # analyze --rerun --trajectory`), its report moved aside by
                # that rerun: the GUI says the study completed, and what is
                # owed is the report of it, not the join's analyses over it.
                answer = _report_of_the_chosen(root)
                if answer.get("ok"):
                    _phases_after_ended_well(root)
                return {**base, **answer, "did": RESUMED_REPORT,
                        **_production_now(root, answer)}
            saved = _save_what_is_replaced(root)
            answer = _join_and_analyse(root, config, segment=None,
                                       keep_frames=keep_frames or None)
            return {**base, **answer, "did": RESUMED_ANALYSIS,
                    **({"saved_in_previous": saved} if saved else {}),
                    **_production_now(root, answer)}
        whole = dict(config)
        whole["output"] = str(root)
        whole["include_phase"] = ["analysis", "report"]
        whole.pop("exclude_phase", None)
        analysis = dict(whole.get("analysis") or {})
        # Its own frames where the study is now, not at the path the record
        # gave them when it ran: a study moved since would analyse whatever
        # sits at its old place.
        from fastmdxplora.config.recorded import leave_the_study_s_own_files

        leave_the_study_s_own_files(analysis, config.get("output"), root)
        simulation_dir = root / "simulation"
        analysis.setdefault("trajectory", str(simulation_dir / "production.dcd"))
        topology = simulation_dir / "trajectory_topology.pdb"
        if topology.is_file():
            analysis.setdefault("topology", str(topology))
        whole["analysis"] = analysis
        ran = FastMDXplora(config_data=whole, output_dir=str(root)).explore(force=True)
        if all(result.status == "ok" for result in ran) and said_whole:
            _said_whole_last(root)
        return {**base, "ok": True, "did": RESUMED_ANALYSIS,
                **_production_now(root, {"ok": True}), "production_planned_ns": planned}

    if done > 0:
        # Production began and cannot be carried on from what is there: a
        # checkpoint off the frame grid, or one that is not sealed. Saying
        # so, rather than starting again and discarding what was run.
        return {**base, "ok": False, "did": RESUMED_NOTHING,
                "error": f"{done:.3f} ns of production is written but cannot be "
                         f"continued: {plan.refusal}"}
    if _extended(root) and _production_written(root):
        # An extended study with no checkpoint left to carry it on: run
        # again from its start, it would overwrite every piece. A first
        # piece stopped before its first checkpoint is run again, as below.
        return {**base, "ok": False, "did": RESUMED_NOTHING,
                "error": "The study was extended, and no piece of it has a "
                         "checkpoint left to carry it on from. Running it again "
                         "from its start would overwrite the frames of every "
                         "piece, those set aside included; run it again in a "
                         "new folder."}

    # Nothing a run can continue from: setup or equilibration was under way,
    # or production short of its first checkpoint. They are the cheap part
    # of a study, so it starts again from the top.
    again = {key: value for key, value in config.items() if key != "output"}
    if device_index is not None:
        again["simulation"] = {**(again.get("simulation") or {}),
                               "device_index": device_index}
    # Its structure found where the study is, not where the resume runs.
    again["systems"] = [
        {**entry, "system": _found_from(root, entry.get("system"))}
        if isinstance(entry, dict) else entry
        for entry in again.get("systems") or []]
    ran = FastMDXplora(config_data=again, output_dir=str(root)).explore(force=True)
    # Said as it ended. It was reported as run whatever happened, so a
    # study that failed again the same way came back as carried on.
    failed = [result for result in ran if result.status != "ok"]
    if failed:
        return {**base, "ok": False, "did": RESUMED_FROM_START,
                "error": failed[0].message or "Run again from its start, it did "
                                              "not finish."}
    return {**base, "ok": True, "did": RESUMED_FROM_START,
            **_production_now(root, {"ok": True}),
            "detail": "Production had not begun, or not as far as its first "
                      "checkpoint; the study was run from its start."}


def _production_now(root: Path, answer: dict[str, Any]) -> dict[str, Any]:
    """The study's production once carried on, as its records give it
    (`lengths.recorded_stage_lengths`, the methods' reading: a variable-step
    run's by its clock), and from what; nothing where the carrying on did
    not end well. In every answer that ends well, and before a production
    is carried on by the same reading. A resume said what was done before
    it ran, 0.004 ns for a study that then held 2,000 + 6,000 steps."""
    if not answer.get("ok"):
        return {}
    from fastmdxplora.simulation.lengths import recorded_stage_lengths

    try:
        said = recorded_stage_lengths(root)
    except (OSError, ValueError):
        return {"production_done_ns": None, "production_done_source": "not read"}
    found = {"production_done_ns": said.get("production_ran_ns"),
             "production_done_source": said.get("production_ran_source")}
    if said.get("production_ran_ns") is None and said["production"].get("said"):
        found["production_done_said"] = said["production"]["said"]
    return found


#: Where a fixed-step study's production before a resume carried it on is
#: read from (``production_done_before_source``).
FROM_CHECKPOINT_STEPS = "steps times the timestep, from the checkpoints"


#: What `resume_batch` did, as a program reads it.
RESUMED_RUNS = "runs"


def _found_from(root: Path, given: Any) -> Any:
    """A structure path the study named, made absolute where the study's
    folder shows where it was.

    A config names its structure relative to where `fastmdx` was run, and a
    resume may be run from anywhere. A run that started has its structure in
    its own folder; one that never started needs the file, so a relative
    path found beside the study, or inside it, is used from there.
    """
    if not isinstance(given, str) or not given:
        return given
    path = Path(given).expanduser()
    if path.is_absolute() or path.exists():
        return given
    # The nearest folder holding it, from the study outwards: a run of a
    # campaign sits two folders below the study, and the study beside the
    # folder it was started from.
    for base in (root, *Path(root).parents):
        if (base / path).is_file():
            return str((base / path).resolve())
    return given


#: Studies of one run whose rule this process is applying again.
_JUDGED_AGAIN_HERE: set[Path] = set()


def _judge_the_run_again(root: Path, base: dict[str, Any]) -> dict[str, Any]:
    """A study of one run whose rule's round failed, was stopped or was cut
    (`stopping.round_ended`): its rule applied again over its rounds so far,
    as its record states it (the run's own config holds none), extending
    the run until what it asks is determined, and its report written again
    with the record. Said finished, with nothing run, before."""
    import json

    from fastmdxplora.batch.explorer import BatchExplorer, _within_the_rounds
    from fastmdxplora.simulation.stopping import (RECORD, _ceiling_a_resume_uses, round_ended,
                                                  run_until_known, targets_of)

    try:
        record = json.loads(_records.read_text(root / RECORD, encoding="utf-8"))
        measures = [{key: value for key, value in target.items() if value is not None}
                    for target in record["targets"]]
        # The ceiling `round_ended` says it runs to: the study's config's.
        ceiling = _ceiling_a_resume_uses(root, record)
        stop_when = {"measures": measures,
                     "max_duration_ns": float(record["max_duration_ns"] if ceiling is None
                                              else ceiling),
                     "independent_starts": str(record.get("independent_starts")
                                               or "not_required")}
        targets = targets_of(stop_when)
    except (OSError, ValueError, KeyError, TypeError, StudyError) as exc:
        return {**base, "ok": False, "did": RESUMED_NOTHING,
                "error": f"The study's record of its rounds could not be read: {exc}"}
    rounds = [entry for entry in record.get("rounds") or [] if isinstance(entry, dict)]
    _JUDGED_AGAIN_HERE.add(root)
    try:
        with _within_the_rounds():
            judged = run_until_known([root], targets, stop_when, record_in=root,
                                     earlier_rounds=rounds)
        BatchExplorer._report_again(root)
    finally:
        _JUDGED_AGAIN_HERE.discard(root)
    ended = round_ended(root)
    # Said as what ran: production carried on where a round extended it,
    # else only the rule applied (it judged the run as it stands).
    extended = any(entry.get("decision") in ("extend", "stopped")
                   for entry in (judged or {}).get("rounds", [])[len(rounds):]
                   if isinstance(entry, dict))
    answer = {**base, "ok": ended is None,
              "did": RESUMED_PRODUCTION if extended else RESUMED_JUDGED,
              "rule_said": str((judged or {}).get("said") or ""),
              **_production_now(root, {"ok": True})}
    if ended is not None:
        answer["error"] = ended.as_text()
        answer["round_ended"] = ended.as_record()
    return answer


#: What a resume of a finished study says.
NOTHING_TO_CARRY_ON = "The study finished; there is nothing to carry on."
#: What a resume did that finished the records of a run whose production,
#: analyses and rule were done (`_said_whole_last`), and what it says.
RESUMED_RECORDS = "records"
RECORDS_FINISHED = "The run's records were finished: it reads completed."


def resume_batch(root: str | Path) -> dict[str, Any]:
    """Carry a study of several runs on to the end of its plan.

    Each run that started is carried on as :func:`resume_study` carries on a
    study of one, each that never started is run, and the study's own
    results across them (the aggregate, an umbrella study's free energy, the
    comparison) are rebuilt once every run has its answer. Runs share out
    the workers and devices the study asked for, as they did the first time.
    Refused while any run of the study is still going.
    """
    import yaml

    from fastmdxplora.batch.explorer import BatchExplorer

    root = Path(root).expanduser().resolve()
    base = {"study": str(root)}
    if _still_running(root):
        return {**base, "ok": False, "did": RESUMED_NOTHING,
                "error": "The study is still running. Resume it once it has stopped."}
    runs = root / "runs"
    going = [member.name for member in sorted(runs.iterdir())
             if member.is_dir() and _still_running(member)] if runs.is_dir() else []
    if going:
        return {**base, "ok": False, "did": RESUMED_NOTHING,
                "error": f"{', '.join(going)} of this study "
                         f"{'is' if len(going) == 1 else 'are'} still running. "
                         "Resume it once every run has stopped."}
    try:
        config = yaml.safe_load((root / "resolved_config.yml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        return {**base, "ok": False, "did": RESUMED_NOTHING,
                "error": f"The study's config could not be read: {exc}"}
    config["systems"] = [
        {**entry, "system": _found_from(root, entry.get("system"))}
        if isinstance(entry, dict) else entry
        for entry in (config.get("systems") or [])]
    config["output"] = str(root)
    try:
        batch = BatchExplorer(config_data=config, output_dir=str(root), resume=True)
        from fastmdxplora.simulation.stopping import carried_on_marked

        # Its record says this resume carries it on, before its work.
        with carried_on_marked(root):
            results = batch.run()
    except Exception as exc:  # noqa: BLE001 - said, not raised, as a resume is
        from fastmdxplora.refusals import refusal_of

        return {**base, "ok": False, "did": RESUMED_NOTHING, "error": str(exc),
                "refusal": refusal_of(exc).as_dict()}
    # Each run's production, as a study of one gives its own, where it
    # ended well and its folder is known.
    listed = [{"run": r.run_id, "status": r.status, "message": r.message,
               **(_production_now(Path(r.output_dir), {"ok": r.status == "ok"})
                  if r.output_dir else {})}
              for r in results]
    failed = [r for r in results if r.status != "ok"]
    from fastmdxplora.batch.explorer import RESUME_REFUSED
    from fastmdxplora.simulation.stopping import round_ended

    refused = [r.run_id for r in results if getattr(r, "error_type", None) == RESUME_REFUSED]

    # Every run carried on, and the rule's round failed again, or a run not
    # carried on and the round left as it ended: not done.
    ended = round_ended(root)
    # Every run finished and its rule judged them at these lengths already:
    # nothing was run, and the study says so, not that its runs were carried on.
    quiet = all(r.status == "ok" and r.message == NOTHING_TO_CARRY_ON for r in results)
    # Its runs finished: nothing ran where its rule judged them at these
    # lengths already, or only its rule was applied, no round extending them.
    did = (RESUMED_NOTHING if quiet and getattr(batch, "_rule_ran_nothing", False)
           else RESUMED_JUDGED if quiet and not getattr(batch, "_rule_extended", True)
           else RESUMED_RUNS)
    from fastmdxplora.batch.explorer import (CUT_IN_THE_ROUNDS, STOPPED_ERROR_TYPE,
                                             STOPPED_IN_THE_ROUNDS, WORKER_ENDED,
                                             _was_stopped)

    # A run stopped when asked, in the round it carried on or not: a stop
    # asked, which the CLI exits 130 for. Not where a worker ended abruptly
    # (killed): no stop was asked, its saved survivor aside, and it exits 1
    # as `fastmdx explore` does (`ended_by_a_stop_asked`).
    stopped = any(_was_stopped(r) or getattr(r, "error_type", None) == STOPPED_IN_THE_ROUNDS
                  for r in results) and not any(
        getattr(r, "error_type", None) == WORKER_ENDED for r in results)
    # A run ended in an error of its own (its worker killed, say), as
    # `fastmdx explore` counts it: exit 1, whatever its round left the study
    # as (a round stopped when asked before said 130 beside "Batch failed").
    erred = any(r.status == "error" and not _was_stopped(r)
                and getattr(r, "error_type", None) not in (
                    STOPPED_ERROR_TYPE, STOPPED_IN_THE_ROUNDS, CUT_IN_THE_ROUNDS,
                    RESUME_REFUSED)
                for r in results)
    answer = {**base, "ok": not failed and ended is None, "did": did,
              "runs": listed, **({"refused": refused} if refused else {}),
              **({"stopped": True} if stopped else {}),
              **({"erred": True} if erred else {})}
    if not failed and ended is None and did != RESUMED_NOTHING:
        import json as _json

        from fastmdxplora.simulation.stopping import RECORD

        # What its rule decided, said last, as a study of one says it.
        try:
            record = _json.loads((root / RECORD).read_text(encoding="utf-8"))
            if str(record.get("outcome") or "") in ("met", "ceiling", "rounds", "stopped"):
                answer["rule_said"] = str(record.get("said") or "")
        except (OSError, ValueError, AttributeError):
            pass
    if ended is not None:
        # What carries it on, as every page says it, said last in the study's
        # summary, so not said again: a run's own refusal advised an
        # extension outside the rule, in ns.
        answer["error"] = ended.as_text()
        answer["said_above"] = True
        answer["round_ended"] = ended.as_record()
    elif failed:
        answer["error"] = (f"{len(failed)} of {len(results)} runs did not finish: "
                           + "; ".join(f"{r.run_id}: {r.message}" for r in failed[:3]))
    return answer
