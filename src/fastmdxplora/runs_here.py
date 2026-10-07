"""Which studies a workspace has running, and starting them one at a time.

The GUI's Run and an AI app's ``start_study`` (``fastmdx mcp``) start a
study the same way, and keep the same rule about when they may: one study
runs in a workspace at a time, so each has the machine to itself and its
timings mean what they say. Two things in the workspace make the rule hold
between them, and between two windows or two AI apps:

- **a starting lock** (:data:`STARTING_FILE`), held from the check that
  nothing is running to the start itself, so two starters cannot both find
  the workspace free. The lock is the operating system's on that file, not
  the file being there: it goes with the process holding it, however that
  process ends, so none is ever left behind to be taken over;
- **the runs started there** (:data:`RUNS_FILE`), each with its folder,
  process and command, so a run is known to every starter from the moment
  it starts, before it has written its own record, and wherever its folder
  is.

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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastmdxplora.refusals import CodedError

__all__ = ["STARTING_FILE", "RUNS_FILE", "StartRefused", "starting_in", "running_in",
           "record_start", "said_going"]

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
    from fastmdxplora.orchestrator import RUN_PROCESS_FILE

    root = Path(workspace).resolve()
    going: dict[Path, str] = {}
    for run in _started_here(root):
        folder = Path(str(run.get("folder") or ""))
        if folder.is_absolute() and _going(run.get("pid"), folder, run.get("argv"), run):
            going[folder] = str(run.get("by") or "")
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
                going[Path(here)] = ""
    return sorted(going.items())


def record_start(workspace: Path, folder: Path, pid: int, argv: list[str], *,
                 by: str) -> None:
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
                 "started_at": datetime.now(timezone.utc).isoformat(), **this_machine()})
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
    """The refusal for a start while others run, naming them, each from
    the first of ``roots`` it is inside."""
    roots = [Path(r).resolve() for r in (roots if isinstance(roots, list) else [roots])]
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
    verb = "is" if len(named) == 1 else "are"
    return (f"{', '.join(named)} {verb} running in this workspace. One study runs here at a "
            f"time, so each has the machine to itself and its timings mean what they say; "
            f"{then}")
