"""One process taking work off the line, one job at a time.

The piece between a queue that holds jobs and a package that runs studies.
Deliberately small: it claims, runs, records, and looks again. Everything
interesting happens on either side of it -- the queue decides what may
start, the study decides what it refuses -- and a worker that had opinions
of its own would be a third place for those decisions to live.

Single-threaded and single-process on purpose. There is one card. A worker
pool sharing it would spend its time swapping contexts, and a worker pool
across machines is a distributed system, which is a different piece of
software with different failure modes. If that is ever wanted it should be
written rather than grown.

Two things the worker does decide, and both are about stopping.

**It stops when the budget is spent.** Not because it checks -- the queue
returns nothing to claim -- but it treats that as an end rather than an
error, because an exhausted allowance is how a campaign is supposed to
finish.

**It stops a chain early when asked to.** A caller supplies a ``watch``
function that sees each finished segment and may say the rest is not worth
running. That is the whole value of segmenting on one card, and it is a
callback rather than a rule here because what makes a run not worth
continuing is a question about the science.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from fastmdxplora.agent.queue import Job, Queue
from fastmdxplora.refusals import Refusal, refusal_of

__all__ = ["Runner", "Watcher", "WorkerReport", "work"]

#: Runs one job. Returns whatever the study produced, and raises to refuse.
Runner = Callable[[Job], dict[str, Any]]

#: Sees a finished job and its result. Returns a reason to abandon the rest
#: of the chain, or ``None`` to let it continue.
Watcher = Callable[[Job, dict[str, Any]], "str | None"]


@dataclass
class WorkerReport:
    """What a worker did, for a caller that was not watching.

    Refusals are kept whole rather than counted. A campaign that stopped
    is a thing somebody has to understand, and "three failed" is not the
    beginning of understanding it.
    """

    ran: int = 0
    finished: int = 0
    failed: int = 0
    abandoned: int = 0
    seconds: float = 0.0
    refusals: list[Refusal] = field(default_factory=list)
    stopped_because: str = ""
    #: Segmented studies whose every segment finished while this worker
    #: ran. Reported rather than joined, because joining reads every frame
    #: of every segment and a caller who has just spent a week of GPU time
    #: may want to look before that happens. Empty unless a campaign was
    #: named -- "finished" is a question about one campaign.
    ready_to_join: list[str] = field(default_factory=list)

    def as_record(self) -> dict[str, Any]:
        return {
            "ran": self.ran,
            "finished": self.finished,
            "failed": self.failed,
            "abandoned": self.abandoned,
            "hours": self.seconds / 3600,
            "stopped_because": self.stopped_because,
            "ready_to_join": list(self.ready_to_join),
            "refusals": [r.as_dict() for r in self.refusals],
        }

    def __str__(self) -> str:
        return (f"{self.finished} finished, {self.failed} refused, "
                f"{self.abandoned} abandoned, {self.seconds / 3600:.1f} "
                f"GPU-hours — {self.stopped_because}")


def work(
    queue: Queue,
    run: Runner,
    *,
    campaign: str | None = None,
    watch: Watcher | None = None,
    max_jobs: int | None = None,
    poll_seconds: float = 0.0,
) -> WorkerReport:
    """Take jobs off the line until there are none, or the cap is reached.

    Parameters
    ----------
    run
        Runs one job. Anything it returns is recorded as the result;
        anything it raises is recorded as a refusal, coded where the
        exception carries one.
    watch
        Sees each finished job. Returning a string abandons the rest of
        that chain with that string as the reason. This is where "the
        binder has already left the epitope" belongs, and it is a callback
        because that judgement is about the science rather than about
        queueing.
    max_jobs
        A cap on how many to take, for a caller that wants to do a bounded
        amount of work and come back. ``None`` runs until the line is
        empty or the budget is spent.
    poll_seconds
        Wait this long and look again when the line is empty rather than
        returning. Zero returns immediately, which is what a test and a
        one-shot caller want.

    Notes
    -----
    A refusal from ``run`` does not stop the worker. The job is recorded,
    the segments that waited on it are abandoned, and the next job is
    claimed -- because one study refusing says nothing about the next, and
    a worker that stopped on the first refusal would turn a campaign of
    forty candidates into a campaign of however many came before the first
    awkward structure.
    """
    report = WorkerReport()

    while max_jobs is None or report.ran < max_jobs:
        job = queue.claim(campaign)
        if job is None:
            if poll_seconds > 0 and _anything_waiting(queue, campaign):
                time.sleep(poll_seconds)
                continue
            report.stopped_because = _why_nothing(queue, campaign)
            report.ready_to_join = _ready_to_join(queue, campaign)
            return report

        report.ran += 1
        started = time.time()
        try:
            result = run(job)
        except BaseException as exc:  # noqa: BLE001 - recorded, not swallowed
            elapsed = time.time() - started
            refusal = refusal_of(exc)
            queue.fail(job.id, refusal, seconds=elapsed)
            report.failed += 1
            report.seconds += elapsed
            report.refusals.append(refusal)
            # A KeyboardInterrupt is recorded like anything else and then
            # re-raised. The record matters: a job left RUNNING would hold
            # its estimate against the campaign's allowance for good.
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                report.stopped_because = "interrupted"
                raise
            continue

        elapsed = time.time() - started
        queue.finish(job.id, seconds=elapsed, result=result)
        report.finished += 1
        report.seconds += elapsed

        if watch is not None:
            reason = watch(job, result)
            if reason:
                report.abandoned += queue.abandon(job.id, reason) - 1

    report.stopped_because = f"reached the cap of {max_jobs} jobs"
    report.ready_to_join = _ready_to_join(queue, campaign)
    return report


def _anything_waiting(queue: Queue, campaign: str | None) -> bool:
    """Whether polling could ever find something.

    Without this a worker with `poll_seconds` set would sleep forever on
    an empty queue, which looks exactly like a worker that is busy.
    """
    from fastmdxplora.agent.queue import BLOCKED, READY, RUNNING

    return any(
        job.status in (READY, BLOCKED, RUNNING)
        for job in queue.jobs(campaign)
    )


def _why_nothing(queue: Queue, campaign: str | None) -> str:
    """Why the line gave nothing, in words a caller can act on.

    "Nothing to do" covers an empty queue, a finished campaign and a spent
    allowance, and those want three different responses from whoever reads
    the report.
    """
    from fastmdxplora.agent.queue import BLOCKED, READY

    from fastmdxplora.agent.queue import FAILED

    if campaign:
        # Asked first, because this is the case a bare "nothing to do"
        # gets most wrong. A campaign whose next job was estimated above
        # its allowance has an empty line and a full budget, and telling
        # somebody the line is empty sends them looking for a job they
        # already submitted.
        for job in queue.jobs(campaign, status=FAILED):
            if job.refusal and job.refusal.code == "environment.budget.exhausted":
                return f"a job would not fit the budget — {job.refusal.message}"

        allowance = queue.budget(campaign)
        if allowance is not None and allowance.exhausted:
            return (f"the budget is spent "
                    f"({allowance.allowance_s / 3600:.1f} GPU-hours)")
        waiting = [j for j in queue.jobs(campaign)
                   if j.status in (READY, BLOCKED)]
        if waiting:
            return ("nothing could start — the next job is estimated above "
                    "what the campaign has left")
    return "the line is empty"


def _ready_to_join(queue: Queue, campaign: str | None) -> list[str]:
    """Segmented studies in this campaign with every segment done.

    Imported here rather than at the top because `run` imports `worker`'s
    Job type, and importing back would be a cycle. The dependency is real
    in one direction only: a worker runs anything, and only `run` knows
    that some of those things are segments of a study.
    """
    if campaign is None:
        return []
    try:
        from fastmdxplora.agent.run import finished_studies

        return [study for study, segments in finished_studies(queue, campaign)
                if segments > 1]
    except Exception:  # noqa: BLE001 - a report is not worth a failed run
        return []
