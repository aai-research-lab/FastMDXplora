"""A waiting line for one card, and a budget it cannot talk its way past.

A trajectory takes hours. A caller that blocks on one can do nothing else,
and a caller that forgets about one has lost it. So work is submitted to a
line, a single worker takes jobs off it one at a time, and the caller asks
later whether a job is done.

Three things make this worth having over a list of shell commands.

**Long runs are chained segments.** A hundred nanoseconds is submitted as
ten ten-nanosecond jobs, each depending on the one before. A crash costs
one segment rather than the run; a caller gets a decision point every few
hours rather than one at the end; and a study that has already gone wrong
can be abandoned at twenty nanoseconds instead of at a hundred. On a
single card that last one is the difference between finishing three
candidates in a week and finishing one.

**The budget is enforced here, in code.** A worker refuses to start a job
whose estimate would take the campaign past its allocation. This is not a
number in a prompt that a model is asked to respect: a model that has been
told to be mindful of compute, and can submit jobs, will spend the
allocation. The refusal is arithmetic and the model has no say in it.

**The line survives the process.** State is a SQLite file, so a worker can
be restarted, a caller can come back tomorrow, and a machine that lost
power has a record of what it was doing.

Deliberately not a scheduler. There is no fair-share, no preemption, no
multi-tenancy and no distributed anything. One machine, one card, one line,
and the simplest thing that is correct.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from fastmdxplora.refusals import Refusal, StudyError, refusal_of

__all__ = [
    "Job",
    "Queue",
    "Budget",
    "READY",
    "RUNNING",
    "DONE",
    "FAILED",
    "BLOCKED",
    "ABANDONED",
]

READY = "ready"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
#: Waiting on a segment before it, which has not finished.
BLOCKED = "blocked"
#: Cancelled before it ran, because an earlier segment settled the question.
ABANDONED = "abandoned"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign    TEXT NOT NULL,
    kind        TEXT NOT NULL,
    payload     TEXT NOT NULL,
    parent      INTEGER,
    segment     INTEGER NOT NULL DEFAULT 0,
    of_segments INTEGER NOT NULL DEFAULT 1,
    estimate_s  REAL NOT NULL DEFAULT 0.0,
    spent_s     REAL NOT NULL DEFAULT 0.0,
    status      TEXT NOT NULL,
    refusal     TEXT,
    result      TEXT,
    created_at  REAL NOT NULL,
    started_at  REAL,
    ended_at    REAL
);
CREATE TABLE IF NOT EXISTS budgets (
    campaign    TEXT PRIMARY KEY,
    allowance_s REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_by_status ON jobs (campaign, status, id);
"""


@dataclass(frozen=True)
class Job:
    id: int
    campaign: str
    kind: str
    payload: dict[str, Any]
    parent: int | None
    segment: int
    of_segments: int
    estimate_s: float
    spent_s: float
    status: str
    refusal: Refusal | None = None
    result: dict[str, Any] | None = None

    @property
    def is_segment(self) -> bool:
        return self.of_segments > 1

    def __str__(self) -> str:
        where = (f" segment {self.segment + 1}/{self.of_segments}"
                 if self.is_segment else "")
        return f"job {self.id} ({self.kind}{where}) — {self.status}"


@dataclass(frozen=True)
class Budget:
    """What a campaign is allowed, and what it has left."""

    campaign: str
    allowance_s: float
    spent_s: float
    committed_s: float

    @property
    def remaining_s(self) -> float:
        """Allowance less what is spent and what is already running."""
        return self.allowance_s - self.spent_s - self.committed_s

    @property
    def exhausted(self) -> bool:
        return self.remaining_s <= 0

    def as_record(self) -> dict[str, Any]:
        return {
            "campaign": self.campaign,
            "allowance_hours": self.allowance_s / 3600,
            "spent_hours": self.spent_s / 3600,
            "remaining_hours": self.remaining_s / 3600,
        }


def _row_to_job(row: sqlite3.Row) -> Job:
    refusal_json = row["refusal"]
    result_json = row["result"]
    return Job(
        id=row["id"], campaign=row["campaign"], kind=row["kind"],
        payload=json.loads(row["payload"]), parent=row["parent"],
        segment=row["segment"], of_segments=row["of_segments"],
        estimate_s=row["estimate_s"], spent_s=row["spent_s"],
        status=row["status"],
        refusal=(Refusal.from_record(json.loads(refusal_json))
                 if refusal_json else None),
        result=(json.loads(result_json) if result_json else None),
    )


class Queue:
    """The line. One per machine; open it wherever the studies live."""

    def __init__(self, path: Path | str = "fastmdxplora-queue.db") -> None:
        self.path = Path(path)
        self._db = sqlite3.connect(str(self.path), isolation_level=None)
        self._db.row_factory = sqlite3.Row
        # WAL so a worker writing and a caller reading do not block each
        # other. A caller asking "is it done yet" every few seconds should
        # never be the reason a run pauses.
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> "Queue":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- budget ----------------------------------------------------------
    def set_budget(self, campaign: str, hours: float) -> Budget:
        """What this campaign may spend, in GPU-hours of wall clock."""
        if hours <= 0:
            raise StudyError(
                f"A budget of {hours} hours leaves nothing to run. Give a "
                "positive allowance, or do not set one.",
                code="config.option.wrong_type",
                option="hours", found_type="non-positive",
            )
        self._db.execute(
            "INSERT INTO budgets (campaign, allowance_s) VALUES (?, ?) "
            "ON CONFLICT(campaign) DO UPDATE SET allowance_s=excluded.allowance_s",
            (campaign, hours * 3600.0))
        return self.budget(campaign)

    def budget(self, campaign: str) -> Budget | None:
        row = self._db.execute(
            "SELECT allowance_s FROM budgets WHERE campaign=?",
            (campaign,)).fetchone()
        if row is None:
            return None
        spent = self._db.execute(
            "SELECT COALESCE(SUM(spent_s), 0) AS s FROM jobs "
            "WHERE campaign=? AND status IN (?, ?)",
            (campaign, DONE, FAILED)).fetchone()["s"]
        committed = self._db.execute(
            "SELECT COALESCE(SUM(estimate_s), 0) AS s FROM jobs "
            "WHERE campaign=? AND status=?",
            (campaign, RUNNING)).fetchone()["s"]
        return Budget(campaign, self._db.execute(
            "SELECT allowance_s FROM budgets WHERE campaign=?",
            (campaign,)).fetchone()["allowance_s"], spent, committed)

    # -- submitting ------------------------------------------------------
    def submit(self, campaign: str, kind: str, payload: dict[str, Any], *,
               estimate_s: float = 0.0, segments: int = 1) -> list[int]:
        """Put work in the line. Returns the job ids, in order.

        ``segments`` greater than one chains that many jobs, each blocked
        on the one before. The estimate is divided between them, since a
        segment is a fraction of the run and should be charged as one.
        """
        if segments < 1:
            raise StudyError(
                f"A job runs in at least one segment; got {segments}.",
                code="config.option.wrong_type",
                option="segments", found_type="below one",
            )
        now = time.time()
        per_segment = estimate_s / segments
        ids: list[int] = []
        parent: int | None = None
        for index in range(segments):
            payload_for = dict(payload)
            if segments > 1:
                payload_for["segment"] = index
                payload_for["of_segments"] = segments
            cursor = self._db.execute(
                "INSERT INTO jobs (campaign, kind, payload, parent, segment, "
                "of_segments, estimate_s, status, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (campaign, kind, json.dumps(payload_for), parent, index,
                 segments, per_segment,
                 READY if index == 0 else BLOCKED, now))
            parent = int(cursor.lastrowid)
            ids.append(parent)
        return ids

    # -- working ---------------------------------------------------------
    def claim(self, campaign: str | None = None) -> Job | None:
        """The next job a worker should run, or ``None``.

        Returns ``None`` rather than raising when the budget is spent: an
        exhausted allowance is a normal end to a campaign, not a fault,
        and a worker looping over several campaigns should move on to the
        next rather than stop.
        """
        where = "WHERE status=?" + (" AND campaign=?" if campaign else "")
        args: tuple = (READY,) if not campaign else (READY, campaign)
        row = self._db.execute(
            f"SELECT * FROM jobs {where} ORDER BY id LIMIT 1", args).fetchone()
        if row is None:
            return None

        job = _row_to_job(row)
        allowance = self.budget(job.campaign)
        if allowance is not None and job.estimate_s > allowance.remaining_s:
            # The arithmetic, not a judgement. A job that would take the
            # campaign past its allowance does not start, and saying so on
            # the job rather than only to the worker means a caller reading
            # the queue later sees why it stopped.
            self._db.execute(
                "UPDATE jobs SET status=?, refusal=?, ended_at=? WHERE id=?",
                (FAILED, json.dumps(Refusal(
                    code="environment.budget.exhausted",
                    message=(
                        f"This job is estimated at {job.estimate_s / 3600:.1f} "
                        f"GPU-hours and the campaign has "
                        f"{allowance.remaining_s / 3600:.1f} left of "
                        f"{allowance.allowance_s / 3600:.1f}."),
                    details={"estimate_hours": job.estimate_s / 3600,
                             "remaining_hours": allowance.remaining_s / 3600},
                ).as_dict()), time.time(), job.id))
            return None

        self._db.execute(
            "UPDATE jobs SET status=?, started_at=? WHERE id=?",
            (RUNNING, time.time(), job.id))
        return self.job(job.id)

    def finish(self, job_id: int, *, seconds: float,
               result: dict[str, Any] | None = None) -> None:
        """Record a job as done, and release whatever waited on it."""
        self._db.execute(
            "UPDATE jobs SET status=?, spent_s=?, result=?, ended_at=? "
            "WHERE id=?",
            (DONE, seconds, json.dumps(result) if result else None,
             time.time(), job_id))
        self._db.execute(
            "UPDATE jobs SET status=? WHERE parent=? AND status=?",
            (READY, job_id, BLOCKED))

    def fail(self, job_id: int, exc: BaseException | Refusal, *,
             seconds: float = 0.0) -> None:
        """Record a refusal, and abandon the segments that waited on it.

        Abandoning rather than failing them. They never ran and nothing is
        wrong with them; what happened is that the question they were part
        of was settled earlier. Marking them failed would put six failures
        in a report where there was one.
        """
        refusal = exc if isinstance(exc, Refusal) else refusal_of(exc)
        self._db.execute(
            "UPDATE jobs SET status=?, spent_s=?, refusal=?, ended_at=? "
            "WHERE id=?",
            (FAILED, seconds, json.dumps(refusal.as_dict()), time.time(),
             job_id))
        self._abandon_after(job_id)

    def abandon(self, job_id: int, reason: str) -> int:
        """Stop a chain early. Returns how many segments were dropped.

        The point of segmenting. A trajectory whose interface has already
        come apart at twenty nanoseconds does not need the other eighty,
        and on one card those eighty hours are another candidate.
        """
        # RUNNING is included deliberately. A worker that has claimed a
        # segment, watched it, and decided the run is already lost must be
        # able to abandon the one in its hand -- and leaving it RUNNING
        # would hold its estimate against the budget for good, so the
        # campaign would slowly lose allowance to runs nobody is doing.
        self._db.execute(
            "UPDATE jobs SET status=?, refusal=?, ended_at=? "
            "WHERE id=? AND status IN (?,?,?)",
            (ABANDONED, json.dumps(Refusal(
                code="simulation.run.abandoned", message=reason).as_dict()),
             time.time(), job_id, READY, BLOCKED, RUNNING))
        return self._abandon_after(job_id, reason=reason) + 1

    def _abandon_after(self, job_id: int, reason: str = "") -> int:
        dropped = 0
        frontier = [job_id]
        while frontier:
            current = frontier.pop()
            rows = self._db.execute(
                "SELECT id FROM jobs WHERE parent=? AND status IN (?,?)",
                (current, READY, BLOCKED)).fetchall()
            for row in rows:
                self._db.execute(
                    "UPDATE jobs SET status=?, refusal=? WHERE id=?",
                    (ABANDONED, json.dumps(Refusal(
                        code="simulation.run.abandoned",
                        message=reason or "An earlier segment settled this.",
                    ).as_dict()), row["id"]))
                frontier.append(row["id"])
                dropped += 1
        return dropped

    # -- reading ---------------------------------------------------------
    def job(self, job_id: int) -> Job | None:
        row = self._db.execute(
            "SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return _row_to_job(row) if row else None

    def jobs(self, campaign: str | None = None,
             status: str | None = None) -> Iterator[Job]:
        clauses, args = [], []
        if campaign:
            clauses.append("campaign=?")
            args.append(campaign)
        if status:
            clauses.append("status=?")
            args.append(status)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        for row in self._db.execute(
                f"SELECT * FROM jobs {where} ORDER BY id", args):
            yield _row_to_job(row)

    def summary(self, campaign: str) -> dict[str, Any]:
        """What a caller coming back tomorrow wants to know."""
        counts: dict[str, int] = {}
        for row in self._db.execute(
                "SELECT status, COUNT(*) AS n FROM jobs WHERE campaign=? "
                "GROUP BY status", (campaign,)):
            counts[row["status"]] = row["n"]
        allowance = self.budget(campaign)
        return {
            "campaign": campaign,
            "jobs": counts,
            "budget": allowance.as_record() if allowance else None,
        }
