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
import os
import re
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from fastmdxplora.refusals import StudyError
from fastmdxplora.user_dir import user_config_dir

__all__ = ["Job", "UnknownJob", "check_job_name", "held", "job_names", "usable_handle",
           "jobs_dir", "load_job", "save_job"]

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
    if not _NAME.fullmatch(name or ""):
        raise StudyError(
            f"{name!r} cannot name a job: it becomes a folder on both "
            "computers, so letters, digits and . _ - only, not starting "
            "with a dash or dot.",
            code="remote.job.unusable_name", given=name)
    return name


def _path_for(name: str) -> Path:
    return jobs_dir() / f"{check_job_name(name)}.json"


_HELD: dict[str, threading.RLock] = {}
_HELD_LOCK = threading.Lock()


@contextmanager
def held(name: str) -> Iterator[None]:
    """One reader-and-writer of a job's record at a time in this process:
    an AI app's calls are served in threads, and a status written over a
    fetch's record would lose what the fetch wrote."""
    with _HELD_LOCK:
        lock = _HELD.setdefault(name, threading.RLock())
    with lock:
        yield


def save_job(job: Job) -> Path:
    target = _path_for(job.name)
    target.parent.mkdir(parents=True, exist_ok=True)
    # A scratch file of its own, so two writers never share one.
    handle, scratch = tempfile.mkstemp(dir=target.parent, prefix=f".{job.name}.",
                                       suffix=".part")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(json.dumps(asdict(job), indent=2) + "\n")
        os.replace(scratch, target)
    except BaseException:
        Path(scratch).unlink(missing_ok=True)
        raise
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
    wrong = _wrong_in(record, name)
    if wrong:
        raise UnknownJob(
            f"The record of {name!r} at {_path_for(name)} is not a job's record "
            f"({wrong}); it was written by something else, or damaged.",
            given=name, permitted=job_names())
    fields = Job.__dataclass_fields__
    return Job(**{k: v for k, v in record.items() if k in fields})


#: What each field of a job's record holds.
_TEXT = ("name", "machine", "remote_dir", "scheduler", "handle", "submitted_at",
         "local_output", "state", "detail", "fetched_at")
_REQUIRED = ("name", "machine", "remote_dir", "scheduler", "handle", "submitted_at",
             "code", "local_output")


def usable_handle(handle: str) -> bool:
    """Whether ``handle`` can be a job's process or SLURM number: digits
    only, no leading zero, more than 1 (a signal to -1 reaches every
    process the account may signal)."""
    return (handle.isascii() and handle.isdigit() and not handle.startswith("0")
            and int(handle) > 1)


def _wrong_in(record: Any, name: str) -> str:
    """What makes ``record`` no record of the job ``name``, or nothing."""
    if not isinstance(record, dict):
        return "not a mapping"
    missing = [key for key in _REQUIRED if key not in record]
    if missing:
        return f"no {', '.join(missing)}"
    for key in _TEXT:
        if key in record and not isinstance(record[key], str):
            return f"{key} is not text"
    for key in ("code", "extra"):
        if key in record and not isinstance(record[key], dict):
            return f"{key} is not a mapping"
    if record["name"] != name:
        return f"it names the job {record['name'][:60]!r}"
    # Put into commands run on the machine: a number, or the record is not
    # one FastMDXplora wrote.
    if not usable_handle(record["handle"]):
        return "handle is not a process or job number"
    extra = record.get("extra") or {}
    tail = extra.get("log_tail", [])
    if not (isinstance(tail, list) and all(isinstance(line, str) for line in tail)):
        return "extra.log_tail is not a list of lines"
    inputs = extra.get("inputs", {})
    if not (isinstance(inputs, dict) and all(
            isinstance(k, str) and isinstance(v, str) for k, v in inputs.items())):
        return "extra.inputs is not a mapping of names to paths"
    left = extra.get("left_on_machine", [])
    if not (isinstance(left, list) and all(isinstance(item, str) for item in left)):
        return "extra.left_on_machine is not a list of names"
    return ""
