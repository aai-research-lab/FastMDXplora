"""Which studies a workspace has running, and when another may start.

The GUI's Run and an AI app's ``start_study`` (``fastmdx mcp``) start a
study the same way, and keep the same rule about when they may
(:func:`may_start`): a study on a GPU of this computer starts beside the
others where it fits, as on a workstation (:mod:`fastmdxplora.gpu_here`);
work on the CPU, and every study on a computer whose GPUs ``nvidia-smi``
does not read, runs one at a time, so each has the processors to itself
and its timings mean what they say. Two things in the workspace make the
rule hold between them, and between two windows or two AI apps:

- **a starting lock** (:data:`STARTING_FILE`), held from the check that
  nothing is running to the start itself, so two starters cannot both find
  the workspace free. The lock is the operating system's on that file, not
  the file being there: it goes with the process holding it, however that
  process ends, so none is ever left behind to be taken over;
- **the runs started there** (:data:`RUNS_FILE`), each with its folder,
  process and command, and the GPU it was given, so a run is known to every
  starter from the moment it starts, before it has written its own record,
  and wherever its folder is.

A run is taken as going while its process is still that run. "Cannot tell"
(the command line could not be read) counts as going, so a start is refused
rather than risked; a process that has gone, or that the system has since
given to something else, does not.

The process checks are the GUI runtime's own, imported where used, so that
the two starters judge a run by one rule.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastmdxplora.refusals import CodedError

__all__ = ["STARTING_FILE", "RUNS_FILE", "StartRefused", "Start", "starting_in",
           "running_in", "may_start", "record_start", "said_going"]

#: Held in the workspace while a study is being started.
STARTING_FILE = ".fastmdxplora-starting"

#: The runs started in the workspace, by the GUI or an AI app.
RUNS_FILE = ".fastmdxplora-runs.json"

#: How deep a workspace is searched for runs' own records, where it is.
DEEPEST = 6

#: In this process, the lock is held by one thread at a time, and a
#: thread already holding it for a folder may enter again (an AI app's
#: start holds it around the runtime's own).
_HELD = threading.RLock()
_held_here: dict[Path, int] = {}


class StartRefused(CodedError, Exception):
    """A study was not started: another is running, or being started."""

    default_code = "environment.workspace.run_going"


@contextmanager
def starting_in(*folders: Path) -> Iterator[None]:
    """Hold the starting lock of each folder named, in one order whoever
    asks. One held by another process refuses, as :class:`StartRefused`.
    Where a folder cannot hold a lock at all (not writable, or a file
    system without locks), it is held in this process only, and a start
    goes on as it did before there was a lock."""
    roots = sorted({Path(folder).resolve() for folder in folders})
    with _HELD:
        taken: list[tuple[Path, int | None]] = []
        try:
            for root in roots:
                handle = None if _held_here.get(root) else _lock(root / STARTING_FILE)
                _held_here[root] = _held_here.get(root, 0) + 1
                taken.append((root, handle))
            yield
        finally:
            for root, handle in reversed(taken):
                _held_here[root] -= 1
                if not _held_here[root]:
                    _held_here.pop(root)
                if handle is not None:
                    _unlock(handle)


def _lock(path: Path) -> int | None:
    """The lock on ``path``, as an open file; None where none can be had."""
    from fastmdxplora.orchestrator import this_machine

    try:
        handle = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    except OSError:
        return None
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        os.close(handle)
        # Held by another process; on Windows, a locking call that fails is
        # one, as the file systems it runs on all lock.
        if isinstance(exc, BlockingIOError) or os.name == "nt":
            raise StartRefused(
                "Another study is being started in this workspace right now. Try "
                "again in a minute.", code="environment.workspace.run_starting") from None
        return None
    # Who holds it, for a person who finds the file; the lock is not this.
    try:
        os.ftruncate(handle, 0)
        os.lseek(handle, 0, os.SEEK_SET)
        os.write(handle, json.dumps({"pid": os.getpid(), **this_machine()}).encode())
    except OSError:
        pass
    return handle


def _unlock(handle: int) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(handle, 0, os.SEEK_SET)
            msvcrt.locking(handle, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_UN)
    except OSError:
        pass
    finally:
        os.close(handle)


def _going(pid: Any, folder: Path, argv: Any, record: dict[str, Any]) -> bool:
    from fastmdxplora.gui.exploration import _identify_run
    from fastmdxplora.orchestrator import record_is_from_elsewhere

    return (isinstance(pid, int) and pid > 0 and not record_is_from_elsewhere(record)
            and _identify_run(pid, folder, argv) is not False)


def _started_here(root: Path) -> list[dict[str, Any]]:
    try:
        said = json.loads((root / RUNS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    runs = said.get("runs") if isinstance(said, dict) else None
    return [run for run in runs if isinstance(run, dict)] if isinstance(runs, list) else []


def running_in(workspace: Path, *, walk: bool = True) -> list[tuple[Path, str]]:
    """The studies running in the workspace, each with who started it:
    those started there by the GUI or an AI app, and, with ``walk``,
    every run found by the record it keeps while it runs, down to
    :data:`DEEPEST` folders (a run started by hand included)."""
    return [(Path(run["folder"]), run["by"]) for run in going_in(workspace, walk=walk)]


def going_in(workspace: Path, *, walk: bool = True) -> list[dict[str, Any]]:
    """As :func:`running_in`, each run as its start record has it (its
    ``folder``, ``by``, ``pid`` and ``gpu``, where it was given one); a run
    found only by its own record has no ``gpu``."""
    from fastmdxplora.orchestrator import RUN_PROCESS_FILE

    root = Path(workspace).resolve()
    going: dict[Path, dict[str, Any]] = {}
    for run in _started_here(root):
        folder = Path(str(run.get("folder") or ""))
        if folder.is_absolute() and _going(run.get("pid"), folder, run.get("argv"), run):
            going[folder] = {**run, "folder": str(folder), "by": str(run.get("by") or "")}
    if walk:
        for here, folders, files in os.walk(root, followlinks=False):
            depth = len(Path(here).relative_to(root).parts)
            folders[:] = [] if depth >= DEEPEST else [f for f in folders
                                                       if not f.startswith(".")]
            if RUN_PROCESS_FILE not in files or Path(here) in going:
                continue
            if any(Path(here).is_relative_to(folder) for folder in going):
                # A run inside a study already going (a study of several
                # runs records its process at its top too): the same study.
                continue
            try:
                record = json.loads((Path(here) / RUN_PROCESS_FILE).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(record, dict) and _going(record.get("pid"), Path(here),
                                                   record.get("argv"), record):
                going[Path(here)] = {"folder": str(Path(here)), "by": ""}
    return [going[folder] for folder in sorted(going)]


def record_start(workspace: Path, folder: Path, pid: int, argv: list[str], *,
                 by: str, gpu: dict[str, Any] | None = None) -> None:
    """Add a run just started to the workspace's list, and drop the runs
    there that have ended. Called with the starting lock held. A list that
    cannot be written leaves the run going, known by its own record once
    it has written one."""
    from fastmdxplora.orchestrator import this_machine

    root = Path(workspace).resolve()
    runs = [run for run in _started_here(root)
            if _going(run.get("pid"), Path(str(run.get("folder") or "")), run.get("argv"), run)]
    runs.append({"folder": str(Path(folder).resolve()), "pid": int(pid),
                 "argv": [str(a) for a in argv], "by": by,
                 "started_at": datetime.now(timezone.utc).isoformat(), **this_machine(),
                 **({"gpu": gpu} if gpu else {})})
    target = root / RUNS_FILE
    partial = root / f"{RUNS_FILE}.{os.getpid()}.partial"
    try:
        partial.write_text(json.dumps({"runs": runs}, indent=1), encoding="utf-8")
        os.replace(partial, target)
    except OSError:
        try:
            partial.unlink()
        except OSError:
            pass


def said_going(roots: Path | list[Path], going: list[tuple[Path, str]], *,
               then: str = "stop it, or wait for it to finish.") -> str:
    """The refusal for work on the CPU while other such work runs, naming
    it, each from the first of ``roots`` it is inside."""
    roots = [Path(r).resolve() for r in (roots if isinstance(roots, list) else [roots])]
    return (f"{_named(roots, going)} {'is' if len(going) == 1 else 'are'} running in this "
            "workspace. Work on the CPU, and a study whose GPU is not chosen here (where "
            "nvidia-smi reads no GPU, or where the study's GPUs are not checked), waits for "
            "the other such work, so each has the processors to itself and its timings mean "
            f"what they say; {then}")


def said_same(roots: list[Path], going: list[tuple[Path, str]], *,
              then: str = "stop it, or wait for it to finish.") -> str:
    """The refusal for a study started in, inside or around a folder a
    run is writing."""
    return (f"{_named(roots, going)} {'is' if len(going) == 1 else 'are'} running where "
            "this study would write, and two runs never write one folder; " + then)


def _named(roots: list[Path], going: list[tuple[Path, str]]) -> str:
    named = []
    for folder, by in going:
        shown = str(folder)
        for root in roots:
            try:
                shown = folder.relative_to(root).as_posix()
                break
            except ValueError:
                continue
        named.append(f"{shown} (started {by})" if by else shown)
    return ", ".join(named)


@dataclass
class Start:
    """Whether a study may start now, and how: refused (the reason and its
    code), or the GPU it is given and what to tell the person."""

    refused: dict[str, Any] | None = None
    #: The GPU chosen here, where one was (its record goes with the run's).
    choice: Any = None
    #: What the run is started with: the GPU it was chosen.
    env: dict[str, str] = field(default_factory=dict)
    #: The GPUs' room, and that the computer is shared, where it is.
    notes: list[str] = field(default_factory=list)

    @property
    def gpu(self) -> dict[str, Any] | None:
        from fastmdxplora.gpu_here import gpu_record

        return gpu_record(self.choice) if self.choice is not None else None


def may_start(folders: list[Path], config: dict[str, Any] | None,
              config_folder: Path | None = None, *, walk: bool = False,
              target: Path | None = None,
              then: str = "stop it, or wait for it to finish.") -> Start:
    """Whether a study may start beside those going in ``folders``. Never
    where a run going writes: in ``target`` (the folder it would write),
    inside it or around it. With ``config`` (the study's, paths read from
    ``config_folder``), a study that simulates on a GPU of this computer
    chosen here goes where it fits, refused where it does not; without one,
    or one on the CPU, or one whose GPU is not chosen here (``nvidia-smi``
    reads none, or the study's GPUs are not checked), it waits for the
    other work on the CPU (a run whose start record gives no GPU counts as
    such)."""
    from fastmdxplora.gpu_here import choice_here, env_for, held_from, room_here
    from fastmdxplora.remote.send import simulates_on_gpu

    roots = [Path(f).resolve() for f in folders]
    going: dict[str, dict[str, Any]] = {}
    for root in roots:
        for run in going_in(root, walk=walk):
            going.setdefault(run["folder"], run)
    runs = list(going.values())
    if target is not None:
        here = Path(target).resolve()
        same = [(Path(r["folder"]), r["by"]) for r in runs
                if here == Path(r["folder"]) or here.is_relative_to(Path(r["folder"]))
                or Path(r["folder"]).is_relative_to(here)]
        if same:
            return Start(refused={"ok": False, "error": said_same(roots, same, then=then),
                                  "code": "environment.workspace.run_going"})
    on_gpu = config is not None and simulates_on_gpu(config, os.cpu_count())
    room = room_here() if on_gpu else None
    choice, notes = None, []
    if room is not None and room.gpus:
        choice, notes = choice_here(config, Path(config_folder or roots[0]), held_from(runs),
                                    room=room)
    if choice is None:
        on_cpu = [(Path(r["folder"]), r["by"]) for r in runs if not isinstance(r.get("gpu"), dict)]
        if on_cpu:
            return Start(refused={"ok": False, "error": said_going(roots, on_cpu, then=then),
                                  "code": "environment.workspace.run_going"})
        return Start(notes=[*notes, *_shared(roots, runs)])
    if choice.refused:
        return Start(refused={"ok": False, "error": choice.refused,
                              "code": "environment.workspace.no_room"})
    where = (f"Runs on GPU {choice.gpu.index}" if choice.gpu is not None
             else "Runs on the GPUs the config names")
    need = (f"one run needs about {choice.need.mb:,} MB ({choice.need.how})"
            if choice.need.mb is not None else
            f"the memory one run needs is {choice.need.how}")
    lines = [*choice.lines, f"{where}; {need}."]
    return Start(choice=choice, env=env_for(choice), notes=[*lines, *notes,
                                                          *_shared(roots, runs)])


def _shared(roots: list[Path], runs: list[dict[str, Any]]) -> list[str]:
    if not runs:
        return []
    going = [(Path(r["folder"]), r["by"]) for r in runs]
    return [f"{_named(roots, going)} {'is' if len(going) == 1 else 'are'} running here too: "
            "this study shares the computer, and each runs slower than alone."]
