"""The studies sent to other machines, as this computer remembers them.

One small file per job beside the machine records, so a terminal closed
mid-run loses nothing: the job is on the machine, and this says where and
how to ask about it. The job's name is its output folder's name, the same
on both computers, so the folder ``fetch`` brings back is the folder the
job wrote.

States use the queue's words -- ``ready`` (waiting for a GPU), ``running``,
``done``, ``failed``, ``abandoned`` -- so a job means the same thing
wherever it is listed.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from fastmdxplora.refusals import StudyError
from fastmdxplora.user_dir import user_config_dir

__all__ = ["Job", "UnknownJob", "check_job_name", "job_names", "jobs_dir",
           "load_job", "save_job"]

READY = "ready"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
ABANDONED = "abandoned"
FINISHED = (DONE, FAILED, ABANDONED)


def jobs_dir() -> Path:
    """Where job records live."""
    return user_config_dir() / "jobs"


@dataclass
class Job:
    """One study sent to one machine."""

    name: str
    machine: str
    #: The job's folder on the machine; the run is written to ``run/`` in it.
    remote_dir: str
    #: ``process`` (a detached process on a workstation) or ``slurm``.
    scheduler: str
    #: The process group id, or the SLURM job id.
    handle: str
    submitted_at: str
    #: The code that sent it, as :class:`CodeIdentity` fields.
    code: dict[str, Any]
    #: Where ``fetch`` puts the run on this computer.
    local_output: str
    state: str = READY
    detail: str = ""
    fetched_at: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def run_dir(self) -> str:
        return f"{self.remote_dir}/run"


class UnknownJob(StudyError):
    """A job named that this computer did not send."""

    default_code = "remote.job.unknown"


_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")


def check_job_name(name: str) -> str:
    """The name, if it can be a folder name on both computers and in a shell."""
    if not _NAME.match(name or ""):
        raise StudyError(
            f"{name!r} cannot name a job: it becomes a folder on both "
            "computers, so letters, digits and . _ - only, not starting "
            "with a dash or dot.",
            code="remote.job.unusable_name", given=name)
    return name


def _path_for(name: str) -> Path:
    return jobs_dir() / f"{check_job_name(name)}.json"


def save_job(job: Job) -> Path:
    target = _path_for(job.name)
    target.parent.mkdir(parents=True, exist_ok=True)
    scratch = target.with_suffix(".json.part")
    scratch.write_text(json.dumps(asdict(job), indent=2) + "\n", encoding="utf-8")
    scratch.replace(target)
    return target


def job_names() -> list[str]:
    folder = jobs_dir()
    return sorted(p.stem for p in folder.glob("*.json")) if folder.is_dir() else []


def load_job(name: str) -> Job:
    try:
        record = json.loads(_path_for(name).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        known = job_names()
        raise UnknownJob(
            f"No job called {name!r} was sent from this computer (known: "
            f"{', '.join(known) or 'none yet'}).",
            given=name, permitted=known,
        ) from None
    fields = Job.__dataclass_fields__
    return Job(**{k: v for k, v in record.items() if k in fields})
