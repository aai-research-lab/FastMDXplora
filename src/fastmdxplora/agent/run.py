"""Running a queued job as a real study.

The last join. :mod:`fastmdxplora.agent.worker` takes a callable and knows
nothing about molecular dynamics; :func:`fastmdxplora.explore` runs a study
and knows nothing about queues. This is the twenty lines between them, and
it is deliberately thin because anything clever here would be a third
place for decisions that belong on one side or the other.

What it does add is the segment bookkeeping, because that cannot live on
either side. The queue holds ten jobs and does not know they are one
trajectory. The orchestrator runs one study and does not know it is the
seventh tenth of another. Somebody has to carry the checkpoint forward and
record where the joins were, and this is that somebody.

Usage::

    from fastmdxplora.agent import Queue, work
    from fastmdxplora.agent.run import study_runner

    with Queue("queue.db") as queue:
        submit_study(queue, "tau", config, segments=10, estimate_s=...)
        work(queue, study_runner(Path("runs")), campaign="tau", watch=watch)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from fastmdxplora.agent.queue import Job, Queue
from fastmdxplora.refusals import refusal_of
from fastmdxplora.cost import Estimate
from fastmdxplora.simulation.resume import (
    plan_segments,
    resume_provenance,
    segmentability,
)

__all__ = ["study_runner", "submit_study", "segment_directory",
           "finished_studies", "join_finished"]



def _explore_with_orchestrator(*, config: dict[str, Any],
                               output_dir: str) -> Any:
    """Run one study through the orchestrator.

    Constructed per study rather than reused. The orchestrator holds the
    resolved config and the run's output directory, and a segment is a
    different study with a different directory, so sharing one across
    segments would mean mutating the thing that is supposed to be the
    record of what ran.
    """
    from fastmdxplora import FastMDXplora

    return FastMDXplora(config_data=config, output_dir=output_dir).explore()


def segment_directory(root: Path | str, campaign: str, study: str,
                      segment: int, of_segments: int) -> Path:
    """Where one segment's output goes.

    Each segment gets its own directory rather than appending into one.
    Appending would mean a crashed segment leaving a half-written
    trajectory in the middle of the run's own output, with no way to tell
    which frames were good -- and the orchestrator's refusal to overwrite
    a finished study exists precisely so that cannot happen by accident.

    Joining the pieces is a separate, explicit step, which is the right
    shape: a joined trajectory is a derived artefact and should look like
    one.
    """
    base = Path(root) / campaign / study
    return base if of_segments == 1 else base / f"segment-{segment:03d}"


def submit_study(
    queue: Queue,
    campaign: str,
    config: dict[str, Any],
    *,
    study: str = "study",
    segments: int = 1,
    estimate: Estimate | float | None = None,
) -> list[int]:
    """Put a study in the line, split if it may be and was asked to be.

    Planning happens here rather than in the worker so that a study which
    may not be split refuses at submission -- before any of its hours are
    spent, and while whoever asked is still looking at the screen.
    """
    seconds = (estimate.seconds if isinstance(estimate, Estimate)
               else float(estimate or 0.0))
    pieces = plan_segments(config, segments=segments)
    return queue.submit(
        campaign, "study",
        {"study": study, "config": config,
         "segment_configs": [p.config for p in pieces],
         "resume_names": [p.resume_from for p in pieces]},
        estimate_s=seconds, segments=segments)


def study_runner(
    root: Path | str,
    *,
    queue: Queue | None = None,
    explore: Callable[..., Any] | None = None,
) -> Callable[[Job], dict[str, Any]]:
    """A runner for :func:`fastmdxplora.agent.worker.work`.

    Parameters
    ----------
    root
        Where study output goes. One directory per campaign, per study,
        per segment.
    queue
        The queue the jobs came from, so a segment can read what the one
        before it recorded. Only the joins need this, and they need it
        because a job's payload is fixed when it is submitted: segment
        seven cannot be told at submission what segments one to six will
        turn out to have done. Without it each segment records its own
        join and not the ones before, and a finished run says it was
        joined once when it was joined six times.
    explore
        The thing that runs a study. Defaults to
        :func:`fastmdxplora.explore`, and is a parameter so a test can
        substitute something that does not need a GPU. Not so that a
        caller can substitute the science.
    """
    root = Path(root)
    runner = explore or _explore_with_orchestrator

    def run(job: Job) -> dict[str, Any]:
        payload = job.payload
        study = str(payload.get("study", "study"))
        segment = int(payload.get("segment", 0))
        of_segments = int(payload.get("of_segments", 1))

        # What the segment before recorded, so the joins accumulate rather
        # than each segment reporting only its own.
        previous_provenance = payload.get("provenance")
        if queue is not None and job.parent is not None:
            earlier = queue.job(job.parent)
            if earlier is not None and earlier.result:
                previous_provenance = earlier.result.get("provenance")

        configs = payload.get("segment_configs") or [payload.get("config")]
        config = dict(configs[min(segment, len(configs) - 1)] or {})

        output_dir = segment_directory(root, job.campaign, study,
                                       segment, of_segments)
        # The config already says where it resumes from, relative to the
        # plan. Made absolute here, because the plan was written before
        # anyone knew which directory this campaign would land in, and a
        # relative path resolved against the working directory is a
        # different file depending on where the worker was started.
        block = dict(config.get("simulation") or {})
        if segment > 0:
            previous = segment_directory(root, job.campaign, study,
                                         segment - 1, of_segments)
            block["resume_from"] = str(
                previous / "simulation" / "checkpoint.chk")
            config["simulation"] = block

        results = runner(config=config, output_dir=str(output_dir))

        record: dict[str, Any] = {
            "output_dir": str(output_dir),
            "provenance": resume_provenance(
                previous_provenance, segment=segment,
                of_segments=of_segments,
                from_step=int(payload.get("from_step", 0)),
                checkpoint=block.get("resume_from", ""),
                # Carried into the run's own record, so a reader sees it
                # without going back to the config to re-derive it.
                qualification=segmentability(config).qualification),
        }
        # Whatever the study produced travels with it verbatim. A watcher
        # deciding whether to abandon a chain should read the study's own
        # numbers, not anything this layer invented.
        if isinstance(results, dict):
            record.update(results)
        elif isinstance(results, list):
            record["runs"] = [
                r.to_dict() if hasattr(r, "to_dict") else str(r)
                for r in results]
        return record

    return run


def finished_studies(queue: Queue, campaign: str) -> list[tuple[str, int]]:
    """Studies in this campaign whose every segment is done.

    A caller coming back to a campaign wants to know what is ready, and
    working that out means grouping jobs by study and checking that none
    of the group is still waiting. Doing it here rather than in the queue
    keeps the queue ignorant of what a study is, which is the only reason
    it can hold anything else.

    Returns pairs of study name and segment count. A study that ran in one
    piece is included with a count of one -- it is finished, and whether
    it needs joining is a separate question with an obvious answer.
    """
    from fastmdxplora.agent.queue import DONE

    by_study: dict[str, list[Job]] = {}
    for job in queue.jobs(campaign):
        study = str(job.payload.get("study", "study"))
        by_study.setdefault(study, []).append(job)

    finished: list[tuple[str, int]] = []
    for study, jobs in sorted(by_study.items()):
        if jobs and all(job.status == DONE for job in jobs):
            finished.append((study, jobs[0].of_segments))
    return finished


def join_finished(
    queue: Queue,
    campaign: str,
    root: Path | str,
    *,
    trajectory_name: str = "production.dcd",
) -> dict[str, Any]:
    """Join every segmented study in this campaign that has finished.

    The last step nobody remembers. A campaign leaves one directory per
    segment, which is right for crash safety, and then the joining is a
    separate command that has to be run -- so it is not, and six months
    later somebody analyses segment zero and calls it the run.

    Offering rather than doing it automatically at the end of the worker
    loop, because joining reads every frame of every segment and a caller
    who has just spent a week of GPU time may reasonably want to look
    before that happens.

    Studies that ran in one piece are skipped: their trajectory is already
    whole. A study whose join refuses -- a gap, an unfinished segment,
    segments from two studies -- is recorded with its refusal rather than
    stopping the rest, because one study's problem says nothing about the
    next one's.
    """
    from fastmdxplora.analysis.joining import join_segments

    joined: dict[str, Any] = {}
    refused: dict[str, Any] = {}
    skipped: list[str] = []

    for study, segments in finished_studies(queue, campaign):
        if segments <= 1:
            skipped.append(study)
            continue
        base = Path(root) / campaign / study
        try:
            joined[study] = join_segments(
                base, base / f"{study}.dcd",
                trajectory_name=trajectory_name)
        except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
            refused[study] = refusal_of(exc).as_dict()

    return {"joined": joined, "refused": refused, "already_whole": skipped}
