"""Exploration layer for the FastMDXplora GUI.

The live dashboard normally watches an existing project output directory.
This module adds a small, deliberately conservative exploration layer so the
same local server can start before a run exists, validate a configuration,
and launch the normal ``python -m fastmdxplora explore`` workflow in a child
process.  It does not reimplement any setup, simulation, analysis, or report
science.
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from fastmdxplora.batch.explorer import ALREADY_HOLD_RESULTS
from fastmdxplora.dependencies import dependency_error_message, missing_dependencies
from fastmdxplora.refusals import StudyError


from fastmdxplora.utils.logging import get_logger

logger = get_logger("gui.exploration")

_FORCEFIELDS = ("auto", "charmm36", "amber14", "amber-fb15", "amber-openff")
_PLATFORMS = ("auto", "CPU", "CUDA", "OpenCL", "HIP")
# The accepted values are declared once, in the schema, and read here. The
# GUI used to keep its own copies alongside the CLI's, and a control
# offering something the CLI rejects is a worse failure than either list
# being wrong: it looks like the tool disagreeing with itself.
def _schema_choices(phase: str, field: str) -> tuple[str, ...]:
    """The accepted values for one option, from the schema that declares them."""
    from fastmdxplora.config.schema import PHASE_SCHEMAS

    return next(
        f.choices for f in PHASE_SCHEMAS[phase].fields if f.name == field
    )


_PRECISIONS = _schema_choices("simulation", "precision")
_INTEGRATORS = _schema_choices("simulation", "integrator")
_ANALYSES = (
    "rmsd",
    "rmsf",
    "rg",
    "hbonds",
    "sasa",
    "ss",
    "qvalue",
    "cluster",
    "dimred",
    "dihedrals",
)


# Returned as the data root when no run is current. Never created.
_NO_CURRENT_RUN = ".fastmdxplora-no-current-run"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Looking at a process this server did not start.
#
# Three questions, each answered differently by platform: is it alive, what
# is it running, and how is it ended. On POSIX a zero signal probes without
# touching, /proc or ps gives the command line, and SIGTERM asks nicely.
# On Windows there is no zero signal -- os.kill maps every signal but the
# two console events to TerminateProcess, so the POSIX liveness check
# killed the process it was checking -- and no ps: the kernel is asked
# through ctypes, and the command line through PowerShell.
# ---------------------------------------------------------------------------

_WINDOWS = sys.platform.startswith("win")


def _process_alive(pid: int) -> bool:
    """Alive, and on POSIX not a zombie. Never touches the process."""
    if _WINDOWS:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)],
                               capture_output=True, text=True, timeout=2).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return True
    if not state:
        return False
    return not state.startswith("Z")


def _terminate_process(pid: int, *, force: bool = False) -> None:
    """Ask a process to stop; with force, make it. On Windows both are
    TerminateProcess, which is the only end the platform offers."""
    import signal

    try:
        if _WINDOWS:
            os.kill(pid, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)
    except (ProcessLookupError, OSError):
        pass


def _end_what_is_left_of(group: int) -> None:
    """Kill whatever is left in a stopped run's process group.

    Nothing, normally: a parallel study's parent passes a stop on to its
    workers and waits for them. Only where the parent had to be killed are
    its workers still there, and they are ended with it rather than left
    running a study nobody can see. Not on Windows, which has no groups.
    """
    import signal

    if _WINDOWS or not hasattr(os, "killpg"):
        return
    try:
        os.killpg(int(group), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError, ValueError):
        pass


#: Phase and run statuses that mean something went wrong.
FAILED_STATUSES = frozenset({"error", "failed"})


def _phases_recorded(root: Path) -> dict[str, dict[str, Any]]:
    manifest = _json_mapping(Path(root) / "manifest.json")
    return {str(p.get("name")): p for p in (manifest.get("phases") or [])
            if isinstance(p, dict) and p.get("name")}


def _runs_that_failed(root: Path) -> list[str] | None:
    """The members of a study of several runs that failed; None where the
    folder is a study of one."""
    batch = _json_mapping(Path(root) / "batch_manifest.json")
    if not batch:
        return None
    return [str(r.get("run_id")) for r in (batch.get("runs") or [])
            if isinstance(r, dict) and str(r.get("status") or "").lower() in FAILED_STATUSES]


def _finished_cleanly(root: Path) -> bool:
    """Whether a study that is no longer running finished without a failed
    phase or run, from what it recorded."""
    failed = _runs_that_failed(root)
    if failed is not None:
        batch = _json_mapping(Path(root) / "batch_manifest.json")
        return bool(batch.get("runs")) and not failed
    phases = _phases_recorded(root)
    return bool(phases) and not any(
        str(p.get("status") or "").lower() in FAILED_STATUSES for p in phases.values())


class _AdoptedProcess:
    """A run this server did not start, held by its PID.

    Everything the runtime does with a Popen -- poll, terminate, kill,
    wait, pid -- works here through signals. The exit code of a process
    that was never this server's child cannot be read, so once it is gone poll
    reports 0 if the study's manifest says it completed, else 1.
    """

    def __init__(self, pid: int, root: Path, record: Mapping[str, Any] | None = None) -> None:
        self.pid = int(pid)
        self.root = Path(root)
        #: The run's process record, by which it is told from a process
        #: given its number since (`_what_the_process_is`).
        self.record: Mapping[str, Any] = record if record is not None else {"pid": self.pid}
        self._returncode: int | None = None

    def _alive(self) -> bool:
        if not _process_alive(self.pid):
            return False
        # Its start where the record has it: a number given to another
        # process once the run ended is not the run going on.
        from fastmdxplora.orchestrator import process_start_ticks

        ticks = self.record.get("start_ticks")
        now = process_start_ticks(self.pid) if isinstance(ticks, int) else None
        return now is None or now == ticks

    def what_it_is(self) -> str:
        """What the process is now to the run held (`_what_the_process_is`)."""
        return _what_the_process_is(self.pid, self.root, self.record)

    def poll(self) -> int | None:
        if self._returncode is not None:
            return self._returncode
        if self._alive():
            return None
        # The Manifest has no overall status to read: it was looked for and
        # never found, so every adopted run that finished read as failed.
        self._returncode = 0 if _finished_cleanly(self.root) else 1
        return self._returncode

    def terminate(self) -> None:
        # Never a process that is not the run: asked again before each signal.
        if self.what_it_is() == "the run":
            _terminate_process(self.pid)

    def kill(self) -> None:
        if self.what_it_is() == "the run":
            _terminate_process(self.pid, force=True)

    def wait(self, timeout: float | None = None) -> int:
        import time

        deadline = None if timeout is None else time.monotonic() + timeout
        while self.poll() is None:
            if deadline is not None and time.monotonic() > deadline:
                raise subprocess.TimeoutExpired(cmd=f"pid {self.pid}", timeout=timeout or 0)
            time.sleep(0.1)
        return self._returncode or 0


#: How long a run whose command line cannot yet be read is retried, and how
#: often. Reading it on Windows starts PowerShell, which on a loaded machine
#: can take longer than its ten-second timeout the first time and be quick
#: the next. Module-level so a test can shorten them.
ADOPTION_RETRY_SECONDS = 60.0
ADOPTION_RETRY_INTERVAL = 2.0


def runs_of_a_study(root: Path) -> list[dict[str, Any]] | None:
    """Each run a study of several will make, with where it stands: waiting,
    running, completed, failed, stopped or interrupted, and the fraction of its steps
    done; ``so_far`` where its study still extends it in rounds and its
    pieces so far are there to read. From the
    plan the batch manifest records before any run starts, and each run's own
    telemetry. None where the folder is a study of one run."""
    from fastmdxplora import record_access
    from fastmdxplora.gui.telemetry import (
        _GOING_STATUSES,
        between_rounds,
        how_the_run_ended,
        read_study_status,
        rounds_of_a_study,
    )

    with record_access.refusals_noted() as refused:
        manifest = _json_mapping(Path(root) / "batch_manifest.json")
    if refused and not manifest:
        # Linked out of the study, it is not read (`record_access`): its runs
        # are named by their folders, with no values or results of its.
        manifest = {"planned": _runs_by_their_folders(Path(root))}
    planned = manifest.get("planned") or manifest.get("runs") or []
    if not manifest or not isinstance(planned, list) or not planned:
        return None
    results = {r.get("run_id"): r for r in (manifest.get("runs") or []) if isinstance(r, dict)}
    entries = [entry for entry in planned if isinstance(entry, dict) and entry.get("run_id")]
    # Each run read once, as one run across the pieces it was carried on in
    # (`segment-001/`, ...): a run its study extends in rounds goes on after
    # its first record, and its result, said it completed.
    records: dict[str, dict[str, Any]] = {}
    going: list[str] = []
    ended: set[str] = set()
    for entry in entries:
        run_id = str(entry["run_id"])
        folder = Path(root) / "runs" / run_id
        record = (read_study_status(folder)
                  if record_access.is_dir(folder / "simulation") else {})
        records[run_id] = record
        if str(record.get("status") or "").lower() in _GOING_STATUSES:
            if how_the_run_ended(folder, record) is None:
                going.append(run_id)
            else:
                # Its record says going, and its process is gone: killed.
                ended.add(run_id)
    # The study's rounds, read once for all its runs.
    rounds = rounds_of_a_study(Path(root))
    out = []
    for entry in entries:
        run_id = str(entry["run_id"])
        folder = Path(root) / "runs" / run_id
        live = records[run_id]
        result = results.get(run_id) or {}
        state = "waiting"
        fraction = None
        waiting = (between_rounds(folder, live, going=going, rounds=rounds)
                   if live and rounds is not None and run_id not in going else None)
        # Done so far: a run its study is still extending, whose analyses of
        # the pieces it has finished are there to read.
        so_far = False
        if run_id in going and (live.get("piece") or result.get("status") not in (
                "failed", "error")):
            # Going: in a later piece, or with no result that says otherwise.
            state = "running"
            # A later piece of the study's rounds: the first was done and
            # analysed. One carried on by hand or by `fastmdx resume` is not.
            so_far = bool(live.get("piece")) and rounds is not None \
                and run_id in rounds["runs"]
            step, total = live.get("current_step"), live.get("total_planned_steps")
            if isinstance(step, (int, float)) and isinstance(total, (int, float)) and total > 0:
                fraction = min(1.0, float(step) / float(total))
        elif waiting is not None:
            # Its pieces so far done, the study's next round still to run it.
            so_far = True
            to_ns, done = waiting.get("to_ns"), waiting.get("done_ns")
            if isinstance(to_ns, (int, float)) and to_ns > 0 and isinstance(done, (int, float)):
                fraction = min(1.0, done / to_ns)
        elif run_id in ended and (live.get("piece") or not result):
            # Interrupted, as its card says: in a later piece, or before any
            # result was written.
            state = "interrupted"
        elif live and str(live.get("status") or "").lower() in ("failed", "error", "stopped"):
            # The run's own record says it failed or was stopped: believed
            # over the study's result, as the run's state is everywhere else
            # (`workspace.study_state`, the Compare page). A later piece that
            # failed or was stopped, past a first result that said the run
            # completed; or a run whose results are written once its study's
            # rounds end, hours on; or one the study recorded "ok" though its
            # production was stopped part way.
            state = "stopped" if str(live.get("status")).lower() == "stopped" else "failed"
        elif result.get("status") in ("ok", "success", "completed"):
            state, fraction = "completed", 1.0
        elif result.get("status") in ("failed", "error"):
            state = "failed"
        elif live:
            stage = str(live.get("stage") or live.get("status") or "").lower()
            step, total = live.get("current_step"), live.get("total_planned_steps")
            if isinstance(step, (int, float)) and isinstance(total, (int, float)) and total > 0:
                fraction = min(1.0, float(step) / float(total))
            # `completed` is what the orchestrator records when a run ends.
            if stage in ("completed", "complete") or fraction == 1.0:
                state, fraction = "completed", 1.0
            elif stage in ("failed", "error"):
                state = "failed"
            else:
                state = "running"
        elif folder.exists():
            state = "running"
        out.append({"run_id": run_id, "path": str(folder), "system": entry.get("system"),
                    "values": entry.get("sweep_values") or {}, "state": state,
                    "fraction": fraction, "so_far": so_far,
                    "stage": ("production" if waiting is not None
                              else live.get("stage") if live else None)})
    # Each run by what sets it apart, said one way wherever a run is named
    # (the sidebar, Runs compared and its overlay).
    axes = axes_that_differ(out)
    for run in out:
        run["label"] = run_label(run["values"], axes) or run["run_id"]
    return out


def _runs_by_their_folders(root: Path) -> list[dict[str, Any]]:
    """A study's runs as its `runs/` folder has them, for a study whose
    batch manifest was not read: each by its folder's name alone."""
    from fastmdxplora import record_access

    try:
        return [{"run_id": folder.name} for folder in sorted(record_access.iterdir(root / "runs"))
                if not folder.name.startswith(".") and record_access.is_dir(folder)]
    except OSError:
        return []


def _runs_held(root: Path) -> dict[str, Any]:
    """The open folder's runs (`runs_of_a_study`) and, for a run of a study,
    its study (`study_a_run_belongs_to`), read held to the study as its
    pages read its records (`server._confined`): a batch manifest linked
    out of the study is not read, its runs named by their folders, and it
    is said not read (``unavailable``, ``not_read``). Paths are said as the
    folder was opened."""
    from fastmdxplora.gui.server import _as_opened, _confined, _said_not_read

    answer = _said_not_read(_confined(Path(root), lambda inside: {
        "runs": runs_of_a_study(inside), "run_of": study_a_run_belongs_to(inside)}))
    if not isinstance(answer, dict):
        return {}
    inside, opened = str(Path(root).resolve()), str(root)
    return answer if inside == opened else _as_opened(answer, inside, opened)


def axes_that_differ(runs: list[dict[str, Any]]) -> list[str]:
    """The swept settings whose values differ between the runs, in the
    order they first appear."""
    every: list[str] = []
    for run in runs:
        for axis in run.get("values") or {}:
            if axis not in every:
                every.append(axis)
    return [axis for axis in every
            if len({json.dumps((run.get("values") or {}).get(axis), sort_keys=True, default=str)
                    for run in runs}) > 1]


#: A setting's unit, as its name ends: `simulation.temperature_K` reads
#: "temperature 310 K".
_UNIT_ENDINGS = ("K", "ns", "ps", "fs", "nm", "M", "bar", "atm")


def _setting_and_unit(axis: str) -> tuple[str, str]:
    name = str(axis).split(".")[-1]
    if name.startswith("random_"):
        name = name[len("random_"):]
    unit = next((u for u in _UNIT_ENDINGS if name.endswith("_" + u)), "")
    if unit:
        name = name[: -len(unit) - 1]
    return name.replace("_", " "), unit


def setting_said(axis: str) -> str:
    """A swept setting as a column names it, in the words `run_label` says
    a run by: "seed", "temperature (K)"; a length by its name alone,
    "duration", since each of its values carries its own unit
    (`value_said`: "250 ps", "2 ns")."""
    name, unit = _setting_and_unit(axis)
    return f"{name} ({unit})" if unit and unit != "ns" else name


def value_said(axis: str, value: Any) -> str:
    """A swept setting's value as a run is named by it: with its unit, and a
    length in nanoseconds as every page says one, in picoseconds under one
    nanosecond ("250 ps", not "0.25 ns")."""
    from fastmdxplora.simulation.sampling_ask import length_said

    _, unit = _setting_and_unit(axis)
    if unit == "ns" and isinstance(value, (int, float)) and not isinstance(value, bool) \
            and math.isfinite(value):
        return length_said(value)
    return f"{value}" + (f" {unit}" if unit else "")


def lengths_said(record: dict[str, Any]) -> dict[str, Any]:
    """A setting that differs (`config.diff`'s record: ``setting``,
    ``first``, ``second``), a length in nanoseconds also said as every
    page says one, in picoseconds under one nanosecond (``first_said``,
    ``second_said``), as Compare and the Timeline give it."""
    from fastmdxplora.simulation.sampling_ask import length_said

    if str(record.get("setting", "")).endswith("_ns"):
        for side in ("first", "second"):
            value = record.get(side)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                record[f"{side}_said"] = length_said(value)
    return record


def run_label(values: dict[str, Any], axes: list[str] | None = None) -> str:
    """A run in a few words, by the values that set it apart: "seed 1",
    "temperature 310 K", "duration 250 ps", several joined by commas; ""
    with none. The setting by its last part, `random_` left off and its
    value said with its unit (`value_said`) ("random_seed = 1" was cut to
    "random_see…" in the sidebar)."""
    said = []
    for axis in (axes if axes is not None else list(values or {})):
        if axis not in (values or {}):
            continue
        name, _ = _setting_and_unit(axis)
        said.append(f"{name} {value_said(axis, values[axis])}")
    return ", ".join(said)


def study_a_run_belongs_to(root: Path) -> dict[str, Any] | None:
    """For a run under a study of several: that study's folder and this run's
    swept values, so the page can say where it belongs and go back."""
    from fastmdxplora import record_access
    from fastmdxplora.gui.browse import run_of

    with record_access.refusals_noted() as refused:
        member = run_of(Path(root))
    study = Path(root).parent.parent
    if not member and refused and Path(root).parent.name == "runs":
        # Its study's batch manifest not read: a run of it, by its folder.
        member = {"study": study.name}
    if not member:
        return None
    # Its study's runs, so the place can offer the others (place.js), and
    # this run by what sets it apart among them.
    runs = runs_of_a_study(study) or []
    this = next((run for run in runs if run["run_id"] == Path(root).name), None)
    return {"path": str(study), "study": member["study"],
            "values": member.get("values") or {},
            "run_id": Path(root).name,
            "label": (this or {}).get("label") or run_label(member.get("values") or {})
            or Path(root).name,
            "runs": runs}


def _as_a_record(record: Any) -> Mapping[str, Any]:
    """A run's process record; a bare command line (``argv``), as earlier
    callers gave one, as a record of it alone."""
    if isinstance(record, Mapping):
        return record
    return {"argv": record} if isinstance(record, list) else {}


def _what_the_process_is(pid: int, root: Path, record: Any = None, *,
                         asking: bool = False) -> str:
    """What the process numbered ``pid`` is to the run that wrote
    ``record`` in the study ``root``: the one test of identity every reader
    asks (the page's word on the run, adopting it, the workspace's runs,
    Stop, resume), so that none of them takes a process for the run that
    another does not.

    - "the run": it is the run;
    - "gone": no process has the number;
    - "another": another process given the number since;
    - "not a run": alive, its command line not this run's, and nothing
      says it began since (a run started from a script, a notebook or a
      pool's worker has such a command line);
    - "unread": alive, its command line not read yet (PowerShell did not
      answer in time).

    Where the record has the run's start in ticks since boot
    (``start_ticks``, with ``boot``), the process is the run only where its
    start is the same, whatever its command line: exact against a reused
    number, and unmoved by a step of the clock. A record written before
    them is judged by its command line: one carrying the arguments the run
    recorded is the run; where the record has no arguments (the Python
    API's), or the command line is not a run's, a process begun more than
    a few seconds after the record was written is another one
    (`telemetry._began_since`, on Linux). ``asking``: the process is the
    one asking or what started it, whose command line is not read: it is the
    run unless it began more than a few seconds after the record was written.
    """
    from fastmdxplora.gui.telemetry import _began_since
    from fastmdxplora.orchestrator import process_start_ticks, this_machine

    record = _as_a_record(record)
    if not _process_alive(pid):
        return "gone"
    ticks = record.get("start_ticks")
    if isinstance(ticks, int) and not isinstance(ticks, bool):
        there, here = record.get("boot") or "", this_machine().get("boot") or ""
        if there and here and there != here:
            return "gone"
        now = process_start_ticks(pid) if there and here else None
        if now is not None:
            return "the run" if now == ticks else "another"
    argv = record.get("argv")
    arguments = isinstance(argv, list) and bool(" ".join(str(a) for a in argv[1:]).split())
    if asking:
        # Its command line is not read, so it is judged by when it began: the
        # asking process begun since the record was written is not the run.
        return "another" if _began_since(pid, dict(record)) else "the run"
    line = _command_line_of(pid)
    if line is None:
        return "unread"
    a_run = _command_line_is_a_run(line, root, argv)
    if a_run and arguments:
        return "the run"
    if _began_since(pid, dict(record)):
        return "another"
    return "the run" if a_run else "not a run"


def _identify_run(pid: int, root: Path, record: Any = None) -> bool | None:
    """Whether the PID is this study's run (`_what_the_process_is`): True,
    False, or None for cannot tell yet. ``record`` is the run's process
    record (or, as earlier callers gave it, its ``argv`` alone).

    False is definite -- the process is gone, or another process was given
    its number, or its command line was read and names something else.
    None is not: the process is alive and its command line could not be
    read, which on Windows means PowerShell did not answer in time.
    Adopting on None would let Stop kill a process the OS gave the number
    to, so it is never adopted on None -- but it is asked again, rather
    than taken as a final answer.
    """
    said = _what_the_process_is(pid, root, record)
    return True if said == "the run" else None if said == "unread" else False


def _process_is_this_run(pid: int, root: Path, record: Any = None) -> bool:
    """The PID is this run (`_what_the_process_is`). "Cannot tell" is not
    "yes": undetermined is not adopted."""
    return _identify_run(pid, root, record) is True


def _carries_the_recorded_arguments(tokens: list[str], argv: list[str]) -> bool:
    """Whether the arguments the run recorded appear, in order, in the live
    command line. Only the arguments: the program itself is written one way
    in `sys.argv` (a script's path) and another on the command line (`-m`
    and a module, or an `.exe`), and is judged by the check below. Both are
    split the same way, so an argument holding a space compares as the same
    words."""
    wanted = [tok.strip('"\'') for tok in " ".join(argv[1:]).split()]
    if not wanted:
        return True
    return any(tokens[start:start + len(wanted)] == wanted
               for start in range(len(tokens) - len(wanted) + 1))


def _command_line_is_a_run(line: str, root: Path, argv: Any = None) -> bool:
    """Whether a command line is FastMDXplora running this study.

    Judged by what is being run, never by where the interpreter lives.
    "fastmdx" as a substring matched the whole command line, and on a
    machine whose conda environment is named fastmdxplora every Python
    process carries that substring in its interpreter path: a stale PID
    reused by any Python at all would have been adopted, and Stop would
    have killed it. A run names the study as an argument, or runs the
    program -- the fastmdx entry point or the fastmdxplora.cli module --
    as a token of its own.
    """
    # Quotes off, as a Windows command line carries them; either
    # separator, as either platform writes paths.
    tokens = [tok.strip('"\'') for tok in line.split()]
    # And the arguments the run recorded, where it recorded them. Any
    # `fastmdx` process was taken for this run, so with two studies
    # running, a stale record whose PID the other study's run had been
    # given was adopted, and Stop would have stopped the other study.
    if (isinstance(argv, list) and argv
            and not _carries_the_recorded_arguments(tokens, [str(a) for a in argv])):
        return False
    study = str(root)
    study_norm = study.replace("\\", "/").rstrip("/").lower()
    for token in tokens:
        norm = token.replace("\\", "/").rstrip("/").lower()
        if norm == study_norm or norm.startswith(study_norm + "/"):
            return True
    for token in tokens:
        name = token.replace("\\", "/").rsplit("/", 1)[-1].lower()
        if name in ("fastmdx", "fastmdx.exe", "fastmdxplora", "fastmdxplora.exe"):
            return True
        if token.startswith("fastmdxplora.cli"):
            return True
    return False


def _command_line_of(pid: int) -> str | None:
    """The whole command line, not what fits a terminal. ps cuts its output
    at the terminal's width, and a study's path is long enough that the
    part naming it was cut off; the check then said a real run was not
    this run. /proc has the full line on Linux; -ww asks ps for it
    elsewhere."""
    if _WINDOWS:
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"(Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}').CommandLine"],
                capture_output=True, text=True, timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        return out.strip() or None
    proc = Path("/proc") / str(pid) / "cmdline"
    try:
        raw = proc.read_bytes()
        if raw:
            return raw.replace(b"\0", b" ").decode("utf-8", "replace").strip()
    except OSError:
        pass
    try:
        out = subprocess.run(["ps", "-ww", "-o", "command=", "-p", str(pid)],
                             capture_output=True, text=True, timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return out.strip() or None


def _slug(value: str, fallback: str = "fastmdxplora_run") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value).strip()).strip("._-")
    return cleaned[:96] or fallback


def _number(
    payload: Mapping[str, Any],
    key: str,
    *,
    default: float,
    minimum: float,
    maximum: float,
    integer: bool = False,
) -> int | float:
    raw = payload.get(key, default)
    try:
        value = int(raw) if integer else float(raw)
    except (TypeError, ValueError) as exc:
        raise StudyError(f"{key} must be a number", code="config.option.wrong_type") from exc
    if value < minimum or value > maximum:
        raise StudyError(f"{key} must be between {minimum:g} and {maximum:g}", code="config.option.wrong_type")
    return value


def exploration_environment_error(config: Mapping[str, Any]) -> str | None:
    """Return an actionable error when the dashboard cannot run the workflow.

    The normal CLI deliberately imports the chemistry stack lazily and lets
    phase pipelines write a warning manifest when optional packages are
    missing.  That behavior is useful for inspecting configuration on light
    installations, but it is misleading for the dashboard's *Run Simulation*
    action: the child exits successfully without producing a simulation.
    """
    missing = _missing_for(config, missing_dependencies)
    if not missing:
        return None
    return dependency_error_message(missing)


def _missing_for(config: Mapping[str, Any], missing_dependencies: Any) -> list[Any]:
    """The backends the config's plan needs and this Python lacks. Analysis
    is in the plan unless the phase list leaves it out; OpenMM where setup
    or simulation runs; PDBFixer where setup repairs a structure, which it
    does not for one given as `setup.fixed_pdb` (every system's). A Run of
    a structure already fixed was refused for PDBFixer, which the command
    line skips for it."""
    from fastmdxplora.gui.config_builder import phases_it_runs

    phases = config.get("include_phase")
    runs_analysis = not isinstance(phases, list) or "analysis" in phases
    missing = missing_dependencies(include_analysis=runs_analysis)
    if not missing:
        return []
    running = set(phases_it_runs(dict(config)))
    setup = config.get("setup") if isinstance(config.get("setup"), dict) else {}
    systems = [s for s in config.get("systems") or () if isinstance(s, dict)]

    def fixed(system: Mapping[str, Any]) -> bool:
        own = system.get("setup") if isinstance(system.get("setup"), dict) else {}
        return bool(own.get("fixed_pdb") or setup.get("fixed_pdb"))

    repairs = "setup" in running and not (
        (systems and all(fixed(s) for s in systems)) or (not systems and setup.get("fixed_pdb")))
    needed = {"openmm.app": bool(running & {"setup", "simulation"}),
              "pdbfixer": repairs}
    return [item for item in missing if needed.get(item.module, True)]


def _backend_refusal(config: Mapping[str, Any]) -> dict[str, Any]:
    """The refusal for a missing backend with its code and the install
    command as its fix, for a caller that shows a refusal's fix; nothing
    where no backend is missing."""
    from fastmdxplora.dependencies import MissingBackendError, missing_dependencies
    from fastmdxplora.gui.config_builder import refused

    missing = _missing_for(config, missing_dependencies)
    if not missing:
        return {}
    said = refused(MissingBackendError(missing))
    return {"code": said["refusal"]["code"], **said}


def _json_mapping_or_yaml(path: Path) -> dict[str, Any]:
    """A config as written, for the preflight: the file the launch wrote."""
    import yaml

    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, RecursionError, yaml.YAMLError):
        # Not text, or nested past what the parser can follow: none to read,
        # and the check that follows says which.
        return {}
    return value if isinstance(value, dict) else {}


def _process_record_held(root: Path) -> dict[str, Any]:
    """The run's process record in ``root``, read held to the study as the
    pages read its records (`server._confined`): one linked out of the
    study is not read, so its process is never adopted or signalled; it is
    said in the log."""
    from fastmdxplora.gui.server import _confined
    from fastmdxplora.orchestrator import RUN_PROCESS_FILE

    answer = _confined(Path(root), lambda inside: {
        "record": _json_mapping(inside / RUN_PROCESS_FILE)})
    if not isinstance(answer, dict):
        return {}
    if answer.get("unavailable"):
        logger.info("%s in %s was not read: it leads outside the study, so its process "
                    "is not taken for the run.", RUN_PROCESS_FILE, root)
        return {}
    record = answer.get("record")
    return dict(record) if isinstance(record, dict) else {}


def _json_mapping(path: Path) -> dict[str, Any]:
    # Through `record_access`: held to its study where the read is.
    from fastmdxplora import record_access

    try:
        value = json.loads(record_access.read_text(path, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _manifest_note(manifest: Mapping[str, Any]) -> str | None:
    notes = manifest.get("notes")
    if not isinstance(notes, list):
        return None
    for note in notes:
        text = str(note or "").strip()
        if text:
            return text
    return None


def _is_nonempty_file(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _made_by(folder: Path) -> Path | None:
    """The outermost of ``folder`` and the folders above it that are not
    there yet, which a launch into it makes; None where it is there."""
    made = None
    for each in (Path(folder), *Path(folder).parents):
        if each.exists():
            break
        made = each
    return made


def _taken_back(config_path: Path | None, folder: Path, made: Path | None) -> None:
    """What a launch refused as it started wrote, taken back: its config
    (where it wrote one into ``folder``), and the folders it made, up to
    ``made`` (:func:`_made_by`), where they hold nothing else, so the same
    folder can be given again once there is room."""
    folder = Path(folder)
    try:
        if config_path is not None and Path(config_path).parent == folder:
            Path(config_path).unlink(missing_ok=True)
    except OSError:
        pass
    if made is None:
        return
    for each in (folder, *folder.parents):
        try:
            each.rmdir()
        except OSError:
            return
        if each == made:
            return


def _defaults_found_from(output_dir: Path) -> Any:
    """Your fastmdx-defaults.yml as `fastmdx explore --output` finds it for
    a run written into ``output_dir``; None where there is none, or where
    it is wrong (the run says so, naming it)."""
    from fastmdxplora.config.defaults_file import defaults_for
    from fastmdxplora.config.loader import ConfigError

    try:
        return defaults_for(output_dir)
    except ConfigError:
        return None


@dataclass
class DashboardRuntime:
    """Mutable state shared by all request-handler threads."""

    workspace_root: Path
    exploration_root: Path
    active_root: Path | None = None
    #: Where the running process is writing, which is not always what is
    #: being viewed. The two were one field, so looking at a finished
    #: study while another ran was refused: Stop would have killed a run
    #: nobody was looking at, and the staleness check would have measured
    #: one study's telemetry against another's start time.
    running_root: Path | None = None
    process: subprocess.Popen[Any] | None = None
    process_started_at: str | None = None
    process_finished_at: str | None = None
    process_returncode: int | None = None
    completion_error: str | None = None
    data_stale: bool = False
    log_path: Path | None = None
    command: list[str] = field(default_factory=list)
    #: The folders whose run was ended by Stop here, so how it ended is
    #: said as stopped, not guessed from its return code (a kill by the
    #: system reads the same; on Windows a stop exits with 1).
    stopped_roots: set[Path] = field(default_factory=set)
    #: Whether the process last run here ended because Stop was pressed.
    stopped_here: bool = False
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    # A study whose run could not yet be identified, and the thread asking
    # again. Cleared when a different study is opened, so a retry never
    # adopts the run of a study the person has already left.
    _adoption_root: Path | None = field(default=None, repr=False)
    _adoption_thread: threading.Thread | None = field(default=None, repr=False)
    #: Set when the GUI is served to someone else (`fastmdx gui --hosted`):
    #: then every folder a run is written to must be inside its workspace.
    hosting: Any = None
    #: Who a run started here is said to be started by, to another starter
    #: that finds it going (`fastmdxplora.runs_here`).
    started_by: str = "from the GUI"

    def __post_init__(self) -> None:
        self.workspace_root = self.workspace_root.expanduser().resolve()
        self.exploration_root = self.exploration_root.expanduser().resolve()
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.exploration_root.mkdir(parents=True, exist_ok=True)
        if self.active_root is not None:
            self.active_root = self.active_root.expanduser().resolve()
            self._adopt_if_running(self.active_root)

    def _no_study_open(self) -> bool:
        """Whether the folder open holds no study: files, none of them
        FastMDXplora's, and no run of this GUI's in it. However it came to be
        opened, it is then shown as no study rather than as an empty one."""
        root = self.active_root
        if root is None or self.data_stale or root == self.running_root:
            return False
        if self.process is not None and self.running_root is None:
            # A process with no folder of its own is the open folder's.
            return False
        if self.log_path is not None and Path(self.log_path).parent == root:
            return False
        from fastmdxplora.gui.browse import holds_no_study

        return holds_no_study(root)

    def data_root(self) -> Path:
        with self.lock:
            if self.data_stale or self.active_root is None or self._no_study_open():
                # Preserve the old run on disk, but do not expose its
                # telemetry when no current run is active. The path is
                # deliberately never created: every handler under it reads
                # files, so a root that does not exist is how they all come
                # back empty without a second branch in each one.
                return self.workspace_root / _NO_CURRENT_RUN
            return self.active_root or self.workspace_root

    def _progress_of(self, root: Path | None) -> dict[str, Any] | None:
        """The running study's stage and fraction complete, for the sidebar's
        Running line. Read from its own telemetry; nothing is guessed. A
        study of several runs has no telemetry of its own: its stage is how
        many of its runs are completed, and its fraction is theirs summed."""
        if root is None:
            return None
        runs = runs_of_a_study(Path(root))
        if runs is not None:
            done = sum(1 for r in runs if r["state"] == "completed")
            going = sum(1 for r in runs if r["state"] == "running")
            failed = sum(1 for r in runs if r["state"] == "failed")
            stopped = sum(1 for r in runs if r["state"] == "stopped")
            interrupted = sum(1 for r in runs if r["state"] == "interrupted")
            between = sum(1 for r in runs if r["state"] == "waiting" and r.get("so_far"))
            stage = f"{done} of {len(runs)} completed"
            if going:
                stage += f", {going} running"
            if between:
                stage += f", {between} waiting for the next round"
            if failed:
                stage += f", {failed} failed"
            if stopped:
                stage += f", {stopped} stopped"
            if interrupted:
                stage += f", {interrupted} interrupted"
            out: dict[str, Any] = {"stage": stage, "runs": len(runs), "completed": done}
            fractions = [r.get("fraction") for r in runs if r.get("fraction") is not None]
            if runs:
                out["percent"] = round(100.0 * sum(fractions) / len(runs), 1)
            return out
        from fastmdxplora.gui.telemetry import read_study_status

        status = read_study_status(root)
        step, total = status.get("current_step"), status.get("total_planned_steps")
        out: dict[str, Any] = {"stage": status.get("stage")}
        if isinstance(step, (int, float)) and isinstance(total, (int, float)) and total > 0:
            out["percent"] = round(100.0 * float(step) / float(total), 1)
        return out

    def _viewing_the_running_study(self) -> bool:
        # An unset running_root means the process belongs to whatever is
        # viewed, which is what one field used to mean and what every
        # caller that sets active_root and process directly still means.
        if self.running_root is None:
            return True
        return self.active_root is not None and self.active_root == self.running_root

    def _telemetry_predates_process(self) -> bool:
        if self.active_root is None or not self.process_started_at:
            return False
        if not self._viewing_the_running_study():
            # The viewed study is not the one the process is writing; its
            # telemetry is older by definition and that says nothing.
            return False
        from fastmdxplora.gui.telemetry import read_study_status

        status = read_study_status(self.active_root)
        # A piece carried on is the newest record, written by this process.
        recorded = ((status.get("last_update_timestamp") if status.get("piece") else None)
                    or status.get("run_started_at") or status.get("last_update_timestamp"))
        if not recorded:
            return False
        # Both sides through the same parser, which normalises a naive
        # timestamp to UTC and returns None rather than raising. Parsing
        # inline caught ValueError only, and a naive `run_started_at` --
        # what an older run or any writer not using an aware datetime
        # leaves on disk -- reached the comparison and raised TypeError
        # instead, inside the dashboard's polling path.
        from fastmdxplora.gui.telemetry import _parse_iso_datetime

        telemetry_time = _parse_iso_datetime(recorded)
        process_time = _parse_iso_datetime(self.process_started_at)
        if telemetry_time is None or process_time is None:
            return False
        return telemetry_time < process_time

    def _process_failure_message(self) -> str:
        detail = ""
        # The run's own word on why it failed, where it recorded one: the
        # log's last line was "Manifest: .../manifest.json" for a run that
        # became unstable in NVT.
        where = self.running_root or self.active_root
        if where is not None:
            try:
                from fastmdxplora.gui.telemetry import read_study_status

                recorded = read_study_status(Path(where))
                if str(recorded.get("status") or "").lower() == "failed" \
                        and recorded.get("latest_error"):
                    detail = str(recorded["latest_error"]).strip().splitlines()[0]
            except Exception:  # noqa: BLE001 - the log stands in
                detail = ""
        if self.log_path is not None and not detail:
            try:
                lines = [
                    line.strip()
                    for line in self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()
                    if line.strip()
                ]
            except OSError:
                lines = []
            for index, line in enumerate(lines):
                if ALREADY_HOLD_RESULTS.lower() in line.lower():
                    detail = " ".join(lines[index:index + 2])
                    break
            # The last ERROR line, not the last line. A run that fails
            # keeps logging afterwards -- the resolved config gets written,
            # handlers close -- so the final line is usually a DEBUG about
            # housekeeping. Reported from the browser: a setup failure
            # showed "Wrote resolved config: ..." while the log held "No
            # structure at 'protein.pdb'" four lines above it.
            if not detail:
                errors = [line for line in lines if " - ERROR - " in line]
                if errors:
                    # Without the timestamp and level, which the panel
                    # already shows and which crowd out the sentence.
                    detail = errors[-1].split(" - ERROR - ", 1)[1]
            if not detail and lines:
                detail = lines[-1]
        if detail:
            # The reason first. "The workflow exited with code 1" is true
            # of every failure and says nothing about this one.
            return (
                f"{detail} (exit code {self.process_returncode}; see "
                f"{self.log_path or self.workspace_root / 'exploration.log'} "
                "for the full log.)"
            )
        return (
            f"The workflow exited with code {self.process_returncode}. "
            f"See {self.log_path or self.workspace_root / 'exploration.log'} for the full log."
        )

    def _refresh_process(self) -> None:
        if self.process is None:
            return
        returncode = self.process.poll()
        if returncode is None:
            return
        self.process_returncode = int(returncode)
        if self.process_finished_at is None:
            self.process_finished_at = _utc_now()
        where = self.running_root or self.active_root
        if (self.process_returncode != 0 and self.completion_error is None
                and where is not None and Path(where).resolve() in self.stopped_roots):
            # Stopped here, as asked: not a failure. Its reason was the
            # log's last line (a citation) with "exit code -15".
            self.stopped_here = True
        elif self.process_returncode != 0 and self.completion_error is None:
            self.completion_error = self._process_failure_message()
            self.data_stale = self._telemetry_predates_process()
            if self.data_stale:
                self.active_root = None
        elif self.process_returncode == 0 and self.completion_error is None:
            self.completion_error = self._completed_run_error()
            if self.completion_error:
                self._record_completion_failure(self.completion_error)

    def _completed_run_error(self) -> str | None:
        """Reject a false-success child that produced no simulation results.

        Only what the study set out to do is asked for. Every study was
        held to a prepared system in its own ``setup/`` and a finished
        simulation, so an analysis of a supplied trajectory, a setup-only
        study, a run given `setup_from` and a study of several runs all
        exited cleanly and were shown as failed.
        """
        root = self.active_root
        if root is None or not self.command or "explore" not in self.command:
            return None

        failed_runs = _runs_that_failed(root)
        if failed_runs is not None:
            if not failed_runs:
                return None
            return (
                f"{len(failed_runs)} of the study's runs failed "
                f"({', '.join(failed_runs)}). See "
                f"{self.log_path or root / 'exploration.log'} for the full log.")

        phases = _phases_recorded(root)
        failed = [name for name, p in phases.items()
                  if str(p.get("status") or "").lower() in FAILED_STATUSES]
        if failed:
            message = str(phases[failed[0]].get("message") or "").strip()
            return (f"The {failed[0]} phase failed" + (f": {message}" if message else ".")
                    + f" See {self.log_path or root / 'exploration.log'} for the full log.")
        ran = {name for name, p in phases.items()
               if str(p.get("status") or "").lower() == "ok"}
        # Without a Manifest nothing says what was planned, so the whole of
        # an ordinary study is asked for, as before.
        check_setup = not phases or "setup" in ran
        check_simulation = not phases or "simulation" in ran

        setup_dir = root / "setup"
        required_setup = ("system.xml", "state.xml", "topology.pdb")
        missing_setup = ([name for name in required_setup if not (setup_dir / name).is_file()]
                         if check_setup else [])
        if missing_setup:
            setup_manifest = _json_mapping(setup_dir / "setup_parameters.json")
            detail = _manifest_note(setup_manifest)
            suffix = f" Details: {detail}" if detail else ""
            return (
                "Setup did not produce the files required for simulation "
                f"({', '.join(missing_setup)}).{suffix} See "
                f"{self.log_path or root / 'exploration.log'} for the full log."
            )

        if not check_simulation:
            return None
        simulation_dir = root / "simulation"
        simulation_manifest = _json_mapping(
            simulation_dir / "simulation_parameters.json"
        )
        final_state = simulation_dir / "state_final.xml"
        simulated = (
            _is_nonempty_file(final_state)
            and simulation_manifest.get("platform_used") not in (None, "")
            and simulation_manifest.get("duration_ns_actual") is not None
        )
        if not simulated:
            detail = _manifest_note(simulation_manifest)
            suffix = f" Details: {detail}" if detail else ""
            return (
                "The workflow exited without producing a completed molecular "
                f"dynamics simulation.{suffix} See "
                f"{self.log_path or root / 'exploration.log'} for the full log."
            )
        return None

    def _record_completion_failure(self, detail: str) -> None:
        """Make the overview and health panels reflect post-run validation."""
        if self.active_root is None:
            return
        try:
            from fastmdxplora.gui.telemetry import TelemetryWriter, read_status

            status = read_status(self.active_root)
            states = status.get("stage_states")
            states = dict(states) if isinstance(states, dict) else {}
            setup_ready = all(
                (self.active_root / "setup" / name).is_file()
                for name in ("system.xml", "state.xml", "topology.pdb")
            )
            if setup_ready:
                stage = "production"
                states["production"] = "failed"
                states["analysis"] = "skipped"
                states["report"] = "skipped"
            else:
                stage = "setup"
                states["setup"] = "failed"
                for name in (
                    "minimization",
                    "nvt",
                    "npt",
                    "production",
                    "analysis",
                    "report",
                ):
                    states[name] = "skipped"
            writer = TelemetryWriter(self.active_root / "simulation", enabled=True)
            writer.write_status(
                stage=stage,
                status="failed",
                latest_error=detail,
                stage_states=states,
            )
            writer.event(detail, level="error")
        except Exception:  # noqa: BLE001 - status reporting must not mask the result
            return

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            self._refresh_process()
            running = self.process is not None and self.process.poll() is None
            viewing_running = self._viewing_the_running_study()
            status = "idle"
            if running and viewing_running:
                status = "running"
            elif running:
                # A run is going, elsewhere. The viewed study is what it is
                # on disk; the sidebar says where the live one is.
                status = "idle"
            elif self.stopped_here and viewing_running:
                status = "stopped"
            elif self.completion_error and viewing_running:
                status = "failed"
            elif self.process is not None and self.process_returncode == 0 and viewing_running:
                status = "completed"
            elif self.process is not None and self.process_returncode is not None and viewing_running:
                status = "failed"
            no_study = self._no_study_open()
            open_root = None if no_study or self.data_stale else self.active_root
            # Its runs, and the study it is a run of, read held to the study.
            held = _runs_held(open_root) if open_root else {}
            return {
                "mode": "home" if open_root is None else "run",
                "status": status,
                "active_run": str(open_root) if open_root else None,
                # A folder opened that holds no study, said as such.
                "no_study_in": str(self.active_root) if no_study else None,
                "workspace": str(self.workspace_root),
                "exploration_root": str(self.exploration_root),
                "process_running": running and viewing_running,
                "running_elsewhere": (str(self.running_root)
                                      if running and not viewing_running else None),
                "running_elsewhere_progress": (self._progress_of(self.running_root)
                                               if running and not viewing_running else None),
                "returncode": self.process_returncode,
                "error": self.completion_error if viewing_running else None,
                "started_at": self.process_started_at,
                "finished_at": self.process_finished_at,
                "log_path": str(self.log_path) if self.log_path else None,
                # The runs of a study of several, each with its state, so the
                # page can list them and open one. None for a study of one.
                "runs": held.get("runs"),
                # A study that analysed a trajectory and ran no simulation
                # here, so has no record of one to show or narrate.
                "analysed_only": _analysed_only(open_root) if open_root else False,
                "run_of": held.get("run_of"),
                # Its batch manifest, or its study's, linked out and not read.
                **({key: held[key] for key in ("unavailable", "unavailable_why", "not_read")
                    if key in held}),
                "command": list(self.command),
                "can_launch": not running,
                # Whether this installation carries the demo study to open.
                "demo_available": _demo_available() and self.hosting is None,
                "demo_to_fetch": _demo_to_fetch() if self.hosting is None else 0,
            }

    def _spawn(
        self,
        command: list[str],
        output_dir: Path,
        dashboard_url: str | None,
        *,
        config_path: str | Path | None = None,
    ) -> dict[str, Any]:
        """Start a run and take ownership of the process.

        Shared, because there is more than one way to describe a run but only
        one way to run it. Assumes the caller holds the lock and has already
        refused a second concurrent run of its own. Beside runs started by
        anyone else in the workspace (another window, an AI app), the rule an
        AI app is held to decides, under the workspace's starting lock
        (`fastmdxplora.runs_here.may_start`): a study the config at
        ``config_path`` says simulates on a GPU goes where it fits on this
        computer's GPUs, given that GPU; anything else waits for the other
        work on the CPU.
        """
        from fastmdxplora.gpu_here import start_sampler
        from fastmdxplora.refusals import refusal_of
        from fastmdxplora.runs_here import StartRefused, may_start, record_start, starting_in

        folders = self._rule_folders()
        config = None
        if config_path is not None:
            read = _json_mapping_or_yaml(Path(config_path))
            config = dict(read) if isinstance(read, Mapping) else None
        try:
            with starting_in(*folders):
                start = may_start(folders, config, self.exploration_root, walk=False,
                                  target=output_dir)
                if start.refused is not None:
                    return start.refused
                since = time.time()
                started = self._spawn_now(command, output_dir, dashboard_url,
                                          **({"env": start.env} if start.env else {}))
                for folder in folders:
                    record_start(folder, output_dir, started["pid"], command,
                                 by=self.started_by, gpu=start.gpu)
                if start.choice is not None and start.choice.learn:
                    start_sampler(int(started["pid"]), Path(output_dir), start.choice, since)
                if start.notes:
                    started["shared"] = list(start.notes)
                return started
        except StartRefused as exc:
            found = refusal_of(exc)
            return {"ok": False, "error": found.message, "code": found.code}

    def _while_starting(self, launch: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        """``launch`` with the workspace's starting lock held, as an AI app
        holds it: refused where another process is starting a study."""
        from fastmdxplora.refusals import refusal_of
        from fastmdxplora.runs_here import StartRefused, starting_in

        try:
            with starting_in(*self._rule_folders()):
                return launch()
        except StartRefused as exc:
            found = refusal_of(exc)
            return {"ok": False, "error": found.message, "code": found.code}

    def _rule_folders(self) -> list[Path]:
        """Where the one-study-at-a-time rule is kept for this window: the
        folder it was started in and the folder it puts new studies in, so
        an AI app given either as its workspace keeps it with this
        window. Never the home folder or a folder above it, which an
        AI app is never given and which are not to be written in."""
        home = Path.home().expanduser().resolve()
        return [folder for folder in dict.fromkeys((self.workspace_root, self.exploration_root))
                if folder != home and folder not in home.parents]

    def _others_running(self, *, on_cpu: bool = False) -> dict[str, Any] | None:
        """The refusal for a start that must wait for work started elsewhere
        (another window, an AI app) in this window's folders, or None.
        Asked before anything is written for a run, where it is certain: for
        work on the CPU (``on_cpu``), and for any study on a computer whose
        GPUs nvidia-smi does not read (or that has none installed). A study
        that may go on a GPU is decided as it starts, from its config
        (:meth:`_spawn`)."""
        from fastmdxplora.gpu_here import nvidia_smi_here
        from fastmdxplora.runs_here import may_start

        if not on_cpu and nvidia_smi_here():
            # Its GPUs are read once, as it starts.
            return None
        return may_start(self._rule_folders(), None, walk=False).refused

    def _spawn_now(self, command: list[str], output_dir: Path,
                   dashboard_url: str | None, *,
                   env: Mapping[str, str] | None = None) -> dict[str, Any]:
        """The process itself, started and held, given ``env`` (the GPU it
        was chosen) beside this process's environment."""
        log_path = output_dir / "exploration.log"
        given = dict(env or {})
        env = os.environ.copy()
        env.update(given)
        env["FASTMDX_DASHBOARD_ACTIVE"] = "1"
        env["FASTMDX_DASHBOARD_OUTPUT"] = str(output_dir)
        if dashboard_url:
            env["FASTMDX_DASHBOARD_URL"] = dashboard_url
        log_handle = log_path.open("a", encoding="utf-8", buffering=1)
        try:
            # Its own session, so the run outlives the server. Without this
            # it sat in the terminal's process group, and Ctrl-C on the
            # server sent SIGINT to the run as well: the server shut down
            # cleanly and a day-long simulation died mid-step, not by any
            # decision but because the terminal delivers the signal to the
            # whole group. A study launched at five should be there in the
            # morning whether the GUI is or not. Stop still stops it: the
            # server holds the handle and signals the child directly.
            process = subprocess.Popen(
                command,
                cwd=str(self.exploration_root),
                env=env,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                shell=False,
                start_new_session=True,
            )
        except Exception:
            log_handle.close()
            raise
        # Popen owns an inherited OS handle. Closing this copy avoids a
        # long-lived Python file object while the child continues writing.
        log_handle.close()
        self.active_root = output_dir
        self.running_root = output_dir
        self.stopped_roots.discard(Path(output_dir).resolve())
        self.process = process
        self.process_started_at = _utc_now()
        self.process_finished_at = None
        self.process_returncode = None
        self.completion_error = None
        self.stopped_here = False
        self.log_path = log_path
        self.command = command
        return {
            "launched": True,
            "output": str(output_dir),
            "pid": process.pid,
            "command": command,
            "state": self.snapshot(),
        }

    def _output_folder(self, requested: str) -> Path | None:
        """Where a run named `requested` is written: an absolute path as it
        is, a bare name beside the others this GUI made. Hosted, only inside
        the workspace, a path shown as ``~/...`` included; None otherwise."""
        if self.hosting is not None:
            if requested.startswith("~") or Path(requested).is_absolute() or "/" in requested:
                return self.hosting.inside(requested)
            return (self.exploration_root / _slug(requested)).resolve()
        candidate = Path(requested).expanduser()
        if candidate.is_absolute():
            return candidate.resolve()
        return (self.exploration_root / _slug(requested)).resolve()

    def launch_from_config(
        self,
        state: Mapping[str, Any] | None,
        *,
        dashboard_url: str | None = None,
        config: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run what a config describes, rather than what a form was wired for.

        The other launch translates a fixed set of form fields into a fixed
        set of flags, which is why it can only start the kind of run somebody
        thought to wire up -- and why analysing a trajectory that already
        existed was not among them. This one writes the config and runs it, so
        anything a config can say, the GUI can start.
        """
        with self.lock:
            self._refresh_process()
            if self.process is not None and self.process.poll() is None:
                return {
                    "ok": False,
                    "error": "A FastMDXplora workflow is already running.",
                }
            refused = self._others_running()
            if refused is not None:
                return refused

            # Held from the check that the folder is free to the start, so no
            # two starters (another window, an AI app) both find it free.
            return self._while_starting(
                lambda: self._launch_config_now(state, config, dashboard_url))

    def _launch_config_now(self, state: Mapping[str, Any] | None,
                           config: Mapping[str, Any] | None,
                           dashboard_url: str | None) -> dict[str, Any]:
        """:meth:`launch_from_config` past its first checks, with the
        workspace's starting lock held."""
        from fastmdxplora.gui.run_from_config import prepare_run

        # A results folder may be a name or a path. Browsing to one puts
        # an absolute path in the box, and running that through the slug
        # turned /Users/someone/work into a folder called
        # Users_someone_work sitting inside the launch directory -- which
        # is neither where they pointed nor anywhere they would look.
        source: Mapping[str, Any] = config if config is not None else (state or {})
        continued = self._study_being_continued(source)
        if continued is not None:
            return self._continue_in_place(continued, state, config, dashboard_url)
        requested = str(dict(source).get("output") or "").strip()
        if not requested:
            # Timestamped, as the CLI's and the builder's defaults are.
            # A fixed name meant the second study the Agent wrote
            # collided with the first: "Output folder already exists
            # and is not empty". The refusal is right; the default
            # should not make it fire.
            from fastmdxplora.naming import default_output_name, system_of

            requested = default_output_name(system_of(dict(source)))
        # A bare name is a folder beside the others this GUI made.
        output_dir = self._output_folder(requested)
        if output_dir is None:
            return {"ok": False,
                    "error": "The output folder must be inside your workspace."}

        # An output directory must be empty or absent, as `launch` and
        # `launch_existing_config` both already require. Two reasons, and
        # only the first was written down:
        #
        #   - it must not clobber a previous run; and
        #   - `_spawn` makes this directory `active_root`, which is the
        #     root `/artifacts/<path>` serves files from. An absolute
        #     path is accepted here on purpose -- browsing to a folder
        #     puts one in the box -- so without this check any directory
        #     nameable in an unauthenticated POST became readable over
        #     HTTP. A directory holding an SSH key is not empty; a
        #     directory a run can be written into is.
        #
        # The traversal guard in `_send_artifact` is correct and was
        # never the issue: it confines paths *within* the root, and the
        # root itself was the thing being chosen.
        if output_dir.exists() and any(output_dir.iterdir()):
            detail = (
                f"Output folder already exists and is not empty: "
                f"{output_dir}. Choose a new output folder to start a "
                f"new simulation."
            )
            self.data_stale = True
            self.completion_error = detail
            return {
                "ok": False,
                "error": detail,
                "next_action": (
                    "Choose a new output folder; anything already there "
                    "was left untouched."
                ),
            }

        made = _made_by(output_dir)
        prepared = prepare_run(dict(state) if state else None, output_dir,
                               config=dict(config) if config is not None else None,
                               defaults=_defaults_found_from(output_dir))
        if not prepared["ok"]:
            return prepared
        # Refused here, before a process is spawned, when the chemistry
        # stack it needs is not installed: the phase would fail inside
        # the run with the same message, minutes later and off screen.
        written = _json_mapping_or_yaml(Path(prepared["config_path"]))
        # Judged as the run will judge it, as `launch_existing_config` judges
        # its file: a config with no systems, or one naming a structure that
        # cannot be read, stopped `fastmdx explore` at once and left a study
        # folder of a config and a log in the workspace's list.
        from fastmdxplora.gui.config_builder import check_config_file

        checked = check_config_file(prepared["config_path"], to_run=True,
                                    defaults=_defaults_found_from(output_dir),
                                    base=self.exploration_root)
        if not checked["ok"]:
            _taken_back(Path(prepared["config_path"]), output_dir, made)
            return {**checked, "config_path": None, "command": None}
        environment_error = exploration_environment_error(written)
        if environment_error:
            # What this launch wrote taken back, so the same folder can be
            # given again once the backend is there: it was left, and the
            # next Run of that name was refused as a folder not empty.
            refusal = _backend_refusal(written)
            _taken_back(Path(prepared["config_path"]), output_dir, made)
            return {"ok": False, "error": environment_error,
                    "config_path": None, "command": None, **refusal}

        # A previous rejected launch may have hidden its old telemetry.
        # This is a new process and must become the current run even when
        # it is launched from the config-based Run page.
        self.data_stale = False
        started = self._spawn(prepared["command"], output_dir, dashboard_url,
                              config_path=prepared["config_path"])
        if started.get("ok") is False:
            _taken_back(Path(prepared["config_path"]), output_dir, made)
            return started
        return {
            "ok": True,
            "error": None,
            "config_path": prepared["config_path"],
            **started,
        }

    def _study_being_continued(self, source: Mapping[str, Any]) -> Path | None:
        """The study a config extends in place, where it does.

        `simulation.resume_from` naming a study directory is a continuation
        of that study: the command line runs its next segment inside it,
        joins every segment and reruns the analyses there. A checkpoint file
        is the raw mechanism, an ordinary run of its own.
        """
        simulation = dict(source).get("simulation") or {}
        named = simulation.get("resume_from") if isinstance(simulation, Mapping) else None
        if not named:
            return None
        from fastmdxplora.gui.browse import is_study

        study = Path(str(named)).expanduser()
        if not study.is_absolute():
            study = self.exploration_root / study
        study = study.resolve()
        return study if study.is_dir() and is_study(study) else None

    def _continue_in_place(self, study: Path, state: Mapping[str, Any] | None,
                           config: Mapping[str, Any] | None,
                           dashboard_url: str | None) -> dict[str, Any]:
        """Extend a study where it is, and watch it there.

        The GUI launched a continuation into a new folder and watched that,
        while the run wrote its segment, the join and the new analyses into
        the study it continued: the folder watched stayed empty and the run
        was marked failed. The config is kept beside the other continuations
        in the workspace rather than in the study, where it would replace
        the study's own record of how it was started.
        """
        from fastmdxplora.gui.run_from_config import prepare_run

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        kept = self.workspace_root / "continuations" / f"{study.name}-{stamp}"
        made = _made_by(kept)
        prepared = prepare_run(dict(state) if state else None, kept,
                               config=dict(config) if config is not None else None)
        if not prepared["ok"]:
            return prepared
        self.data_stale = False
        started = self._spawn(prepared["command"], study, dashboard_url,
                              config_path=prepared["config_path"])
        if started.get("ok") is False:
            _taken_back(Path(prepared["config_path"]), kept, made)
            return started
        return {"ok": True, "error": None, "config_path": prepared["config_path"],
                "continues": str(study), **started}

    def save_config_for_elsewhere(self, state: Mapping[str, Any]) -> dict[str, Any]:
        """The builder's config saved in the workspace, beside the folder the
        run will write, for a service to run on its own compute.

        Hosted only (`--runs-url`). Nothing is started here: the service's
        page opens with the two paths filled in, and the person confirms
        there, since the compute is the service's to give. The results
        folder must be new, as it must for a run here; the config is
        ``<results folder>.yml`` beside it, or ``-2``, ``-3`` and so on
        after an earlier one, and a file is never written over.
        """
        from fastmdxplora.gui.config_builder import config_yaml
        from fastmdxplora.naming import default_output_name, system_of

        if self.hosting is None:
            return {"ok": False, "error": "Only a hosted GUI saves a config for elsewhere."}
        source = dict(state or {})
        requested = str(source.get("output") or "").strip()
        if not requested:
            requested = default_output_name(system_of(source))
        output_dir = self._output_folder(requested)
        if output_dir is None or output_dir == self.hosting.workspace:
            return {"ok": False,
                    "error": "The results folder must be a new folder inside your workspace."}
        if output_dir.exists():
            return {"ok": False,
                    "error": f"{self.hosting.shown(output_dir)} is in your workspace already. "
                             "Give a new results folder."}
        source["output"] = requested
        built = config_yaml(source, full=bool(source.get("full")))
        if not built["ok"]:
            return {"ok": False, "error": built["error"]}
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        for n in range(1, 1000):
            target = output_dir.parent / (f"{output_dir.name}.yml" if n == 1
                                          else f"{output_dir.name}-{n}.yml")
            try:
                with target.open("x", encoding="utf-8") as out:
                    out.write(built["yaml"])
            except FileExistsError:
                continue
            return {"ok": True, "error": None, "config": str(target),
                    "output": str(output_dir)}
        return {"ok": False, "error": "Too many configs of that name; give another folder."}

    def launch_existing_config(
        self,
        config_path: str,
        *,
        output: str | None = None,
        dashboard_url: str | None = None,
    ) -> dict[str, Any]:
        """Run a config exactly as it is, without writing to it.

        The file may be committed beside a paper, shared with somebody, or the
        record of a run that already happened. Running it must not change it,
        and must not quietly rewrite it into this software's own house style
        either -- what ran should be what the person has.
        """
        with self.lock:
            self._refresh_process()
            if self.process is not None and self.process.poll() is None:
                return {
                    "ok": False,
                    "error": "A FastMDXplora workflow is already running.",
                }
            refused = self._others_running()
            if refused is not None:
                return refused

            return self._while_starting(
                lambda: self._launch_existing_now(config_path, output, dashboard_url))

    def _launch_existing_now(self, config_path: str, output: str | None,
                             dashboard_url: str | None) -> dict[str, Any]:
        """:meth:`launch_existing_config` past its first checks, with the
        workspace's starting lock held."""
        from fastmdxplora.gui.config_builder import check_config_file

        output_dir = self.output_of_existing_config(config_path, output)
        if output_dir is None:
            return {"ok": False,
                    "error": "The output folder must be inside your workspace."}
        # Judged as the run will judge it: systems required, and with the
        # defaults the run will find where it writes.
        checked = check_config_file(config_path, to_run=True,
                                    defaults=_defaults_found_from(output_dir),
                                    base=self.exploration_root)
        if not checked["ok"]:
            return checked

        source = Path(checked["path"])
        if output_dir.exists() and any(output_dir.iterdir()):
            detail = f"Output folder already exists and is not empty: {output_dir}. Choose a new output folder to start a new simulation."
            self.data_stale = True
            self.completion_error = detail
            return {
                "ok": False,
                "error": detail,
                "next_action": "Choose a new output folder; the previous run was preserved.",
            }
        made = _made_by(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        self.data_stale = False

        command = [
            sys.executable, "-m", "fastmdxplora", "explore",
            "--config", str(source), "--output", str(output_dir),
        ]
        started = self._spawn(command, output_dir, dashboard_url, config_path=source)
        if started.get("ok") is False:
            _taken_back(None, output_dir, made)
            return started
        return {
            "ok": True,
            "error": None,
            "config_path": str(source),
            "modified": False,
            **started,
        }

    def output_of_existing_config(self, config_path: str,
                                  output: str | None = None) -> Path | None:
        """Where a config run as it stands writes: the folder asked for; else
        the config's own `output:`, read as `fastmdx explore` reads it from
        the folder runs start in; else beside the config, under a name taken
        from it, so a config kept with its data leaves its results there too.
        None for a folder outside a hosted workspace."""
        requested = (output or "").strip()
        if requested:
            return self._output_folder(requested)
        source = Path(config_path).expanduser()
        own = _json_mapping_or_yaml(source).get("output")
        if isinstance(own, str) and own.strip():
            if self.hosting is not None:
                return self.hosting.inside(own.strip())
            named = Path(own.strip()).expanduser()
            return (named if named.is_absolute() else self.exploration_root / named).resolve()
        return (source.parent / f"{source.stem}_output").resolve()

    def _adopt_if_running(self, root: Path | None) -> bool:
        """If the study at root has a live run this server did not start,
        hold it as the process: it shows as running, and Stop reaches it.

        A GUI reopened on a running study could watch it but not stop it,
        and its sidebar called it idle. The run records its PID in the
        folder; this reads it, checks the process is alive and is this
        run, and adopts it.
        """
        # Whatever study was being retried, this one replaces it.
        self._adoption_root = None
        if root is None or self.process is not None and self.process.poll() is None:
            return False
        record = _process_record_held(Path(root))
        pid = record.get("pid")
        if not isinstance(pid, int) or pid <= 0:
            return False
        from fastmdxplora.orchestrator import record_is_from_elsewhere

        if record_is_from_elsewhere(record):
            # Written on another machine, or before a reboot: its process
            # number names nothing of this study here.
            return False
        identity = _identify_run(pid, Path(root), record)
        if identity is None:
            # Alive, and not yet identifiable. Not adopted -- the sidebar
            # says idle, which is the honest word for "cannot tell" -- but
            # asked again, so a slow first answer is not the last one.
            logger.info("Process %s is alive but its command line could not be read "
                        "yet; asking again.", pid)
            self._retry_adoption(Path(root))
            return False
        if not identity:
            return False
        self._hold(Path(root), record, pid)
        return True

    def _hold(self, root: Path, record: Mapping[str, Any], pid: int) -> None:
        """Take a run this server did not start as the one it is watching."""
        self.process = _AdoptedProcess(pid, Path(root), dict(record))
        self.running_root = Path(root)
        self.process_started_at = str(record.get("started_at") or _utc_now())
        self.process_finished_at = None
        self.process_returncode = None
        self.completion_error = None
        self.stopped_here = False
        self.log_path = Path(root) / "exploration.log"
        self.command = [str(a) for a in (record.get("argv") or [])]

    def _retry_adoption(self, root: Path) -> None:
        """Ask again, in the background, until the run is identified, is
        found not to be this study's, ends, or the window closes."""
        with self.lock:
            thread = self._adoption_thread
            if thread is not None and thread.is_alive() and self._adoption_root == root:
                return
            self._adoption_root = root
            self._adoption_thread = threading.Thread(
                target=self._keep_trying_to_adopt, args=(root,),
                name="fastmdx-adopt", daemon=True)
            self._adoption_thread.start()

    def _keep_trying_to_adopt(self, root: Path) -> None:
        import time

        deadline = time.monotonic() + ADOPTION_RETRY_SECONDS
        while time.monotonic() < deadline:
            time.sleep(ADOPTION_RETRY_INTERVAL)
            with self.lock:
                if self._adoption_root != root:
                    return  # the person has opened a different study
                if self.process is not None and self.process.poll() is None:
                    return  # already watching a run
            record = _process_record_held(root)
            pid = record.get("pid")
            if not isinstance(pid, int) or pid <= 0:
                return  # the run finished and took its record with it
            # Outside the lock: on Windows this can take ten seconds, and
            # the page's own requests must not wait behind it.
            identity = _identify_run(pid, root, record)
            if identity is False:
                return
            if identity is True:
                with self.lock:
                    if (self._adoption_root == root
                            and (self.process is None or self.process.poll() is not None)):
                        self._hold(root, record, pid)
                        self._adoption_root = None
                return
        logger.warning("Process for %s stayed unidentifiable for %.0f s; not adopting it. "
                       "Reopen the study to try again.", root, ADOPTION_RETRY_SECONDS)

    def open_the_demo(self) -> dict[str, Any]:
        """The demo study copied into the workspace and opened: a finished
        study to look at before running one (fastmdxplora.demo)."""
        from fastmdxplora.demo import DemoMissing, copy_demo

        if self.hosting is not None:
            # A service's workspace is the person's; the demo is for a GUI
            # on their own machine.
            return {"ok": False, "error": "The demo study opens only in a GUI on your own "
                    "computer.", "state": self.snapshot()}
        try:
            copied = copy_demo(self.exploration_root)
        except DemoMissing as exc:
            return {"ok": False, "error": str(exc), "state": self.snapshot()}
        except OSError as exc:
            return {"ok": False, "error": f"The demo study could not be copied: {exc}",
                    "state": self.snapshot()}
        answer = self.switch_to(copied)
        answer["folder"] = str(copied)
        return answer

    def open_shared_study(self, source: str) -> dict[str, Any]:
        """A shared study, from its DOI, its Zenodo address or a zip on this
        computer, checked file by file and opened in a folder of its own in
        the workspace (fastmdxplora.sharing.opening)."""
        from fastmdxplora.refusals import CodedError
        from fastmdxplora.sharing.opening import open_shared

        if self.hosting is not None:
            return {"ok": False, "error": "A shared study opens only in a GUI on your own "
                    "computer: fastmdx gui --open.", "state": self.snapshot()}
        try:
            opened = open_shared(source, workspace=self.exploration_root)
        except CodedError as exc:
            return {"ok": False, "error": str(exc), "state": self.snapshot()}
        except OSError as exc:
            return {"ok": False, "error": f"The shared study could not be opened: {exc}",
                    "state": self.snapshot()}
        answer = self.switch_to(opened)
        answer["folder"] = str(opened)
        return answer

    def switch_to(self, folder: str | Path) -> dict[str, Any]:
        """Watch a different output folder without relaunching.

        The GUI was bound to the one folder given at launch. A person with
        several finished studies had to stop the server and start it again
        with a new --output to look at another; this makes it a control in
        the page. Refused while a run is in progress here -- the dashboard
        watches one study at a time, and swapping the folder under a live
        run would read one study's telemetry against another's process.
        """
        with self.lock:
            self._refresh_process()
            path = Path(folder).expanduser().resolve()
            if self.hosting is not None and self.hosting.inside(path) is None:
                return {"ok": False, "error": "That folder is outside your workspace.",
                        "state": self.snapshot()}
            if not path.is_dir():
                return {"ok": False, "error": f"No such folder: {path}",
                        "state": self.snapshot()}
            # A study folder is one FastMDXplora wrote, by the same rule the
            # file browser uses -- which includes a study of several runs.
            # Anything else is a wrong turn in the picker, and loading it
            # would show an empty study rather than say so.
            from fastmdxplora.gui.browse import is_study

            if not is_study(path):
                return {"ok": False,
                        "error": f"{path.name} does not look like a "
                                 "FastMDXplora output folder.",
                        "state": self.snapshot()}
            # Look at the new folder. The process, if any, keeps running
            # where it is and stays stoppable; a switch changes what is
            # viewed, not what is happening. Only a finished process is
            # forgotten, so its completion does not colour another study.
            # A live process that was never pinned to a folder belongs to
            # the folder being left.
            if (self.process is not None and self.process.poll() is None
                    and self.running_root is None):
                self.running_root = self.active_root
            self.active_root = path
            self.data_stale = False
            self._adopt_if_running(path)
            if self.process is None or self.process.poll() is not None:
                self.process = None
                self.running_root = None
                self.process_started_at = None
                self.process_finished_at = None
                self.process_returncode = None
                self.completion_error = None
                self.stopped_here = False
                self.log_path = None
                self.command = []
            return {"ok": True, "active_run": str(path), "state": self.snapshot()}

    def run_a_fix(self, index: Any, dashboard_url: str | None = None) -> dict[str, Any]:
        """Run what would fix the study on screen, by its place in the list
        `fixes_payload` gives (`fastmdxplora.remedies`).

        Only a fix that is this software's own command, and not one waiting
        on a choice only the person can make; the arguments are the ones
        the remedy built, never text from the request. The run is watched
        in the study it fixes, as a continuation is.
        """
        import sys

        from fastmdxplora.gui.fixes_view import runnable, study_of
        from fastmdxplora.remedies import remedies_of

        try:
            index = int(index)
        except (TypeError, ValueError):
            return {"ok": False, "error": "Say which fix to run."}
        with self.lock:
            self._refresh_process()
            if self.process is not None and self.process.poll() is None:
                return {"ok": False,
                        "error": "A FastMDXplora workflow is already running."}
            refused = self._others_running(on_cpu=True)
            if refused is not None:
                return refused
            study = study_of(self.active_root)
            if study is None or not study.is_dir():
                return {"ok": False, "error": "No study is open."}
            found = remedies_of(study)
            if not 0 <= index < len(found):
                return {"ok": False, "error": "Nothing stopped this study that a "
                                              "command would fix."}
            remedy = found[index]
            if not runnable(remedy):
                return {"ok": False, "error": (
                    "This fix is not a command run from here: " + remedy.fix)}
            self.data_stale = False
            started = self._spawn([sys.executable, "-m", "fastmdxplora", *remedy.argv],
                                  study, dashboard_url)
            return {"ok": True, "error": None, "fix": remedy.as_record(), **started}

    def run_windows_again(self, windows: Any, *, force_constant: Any = None,
                          duration_ns: Any = None,
                          dashboard_url: str | None = None) -> dict[str, Any]:
        """Run umbrella windows the person names again, at the values they
        name, in the study on screen (`remedies.windows_again`).

        The command is built from the study's record and the values checked
        as numbers, never taken as text; the page asks the person first,
        with the price.
        """
        import sys

        from fastmdxplora.gui.fixes_view import study_of
        from fastmdxplora.refusals import StudyError, refusal_of
        from fastmdxplora.remedies import windows_again

        with self.lock:
            self._refresh_process()
            if self.process is not None and self.process.poll() is None:
                return {"ok": False,
                        "error": "A FastMDXplora workflow is already running."}
            refused = self._others_running(on_cpu=True)
            if refused is not None:
                return refused
            study = study_of(self.active_root)
            if study is None or not study.is_dir():
                return {"ok": False, "error": "No study is open."}
            try:
                remedy = windows_again(study, windows, force_constant=force_constant,
                                       duration_ns=duration_ns)
            except StudyError as exc:
                found = refusal_of(exc)
                return {"ok": False, "error": found.message, "code": found.code}
            self.data_stale = False
            started = self._spawn([sys.executable, "-m", "fastmdxplora", *remedy.argv],
                                  study, dashboard_url)
            return {"ok": True, "error": None, "fix": remedy.as_record(), **started}

    def write_the_report_again(self, dashboard_url: str | None = None) -> dict[str, Any]:
        """The report phase alone on the study on screen (`run_again`)."""
        return self.run_again(["report"], dashboard_url=dashboard_url)

    def run_again(self, phases: Any, analyses: Any = None, *,
                  dashboard_url: str | None = None) -> dict[str, Any]:
        """Run the study on screen's analysis or report again, in its folder.

        By the phase command itself, with ``--rerun`` (`fastmdxplora.again`
        names it): from the study's records and the settings it recorded,
        simulating nothing, what it replaces kept in ``previous/``; a study
        of several runs run by run, with the comparison of its runs built
        again. Checked here first, so a refusal is said at once; started and
        watched as any run here is, under the workspace's start rule. The
        page asks first.
        """
        import sys

        from fastmdxplora import again
        from fastmdxplora.gui.browse import is_study
        from fastmdxplora.refusals import refusal_of

        with self.lock:
            self._refresh_process()
            if self.process is not None and self.process.poll() is None:
                return {"ok": False,
                        "error": "A FastMDXplora workflow is already running."}
            refused = self._others_running(on_cpu=True)
            if refused is not None:
                return refused
            # The folder open, not its campaign as for a fix: a run of a
            # study of several is run again alone, and its study's
            # comparison built again after it.
            study = Path(self.active_root) if self.active_root else None
            if study is None or not study.is_dir() or not is_study(study):
                return {"ok": False, "error": "No study is open."}
            try:
                planned = again.plan(study, phases, analyses)
            except Exception as exc:  # noqa: BLE001 - a refusal, said
                found = refusal_of(exc)
                return {"ok": False, "error": found.message, "code": found.code}
            self.data_stale = False
            started = self._spawn([sys.executable, "-m", "fastmdxplora", *planned.command()],
                                  study, dashboard_url)
            if started.get("ok") is False:
                return started
            return {"ok": True, "error": None, "said": planned.said(), **started}

    def stop(self) -> dict[str, Any]:
        with self.lock:
            refused = self._not_the_run_held()
            if refused is not None:
                return {"stopped": False, "detail": refused, "state": self.snapshot()}
            self._refresh_process()
            if self.process is None or self.process.poll() is not None:
                return {"stopped": False, "detail": "No workflow is currently running.", "state": self.snapshot()}
            where = self.running_root or self.active_root
            if where is not None:
                self.stopped_roots.add(Path(where).resolve())
            self.process.terminate()
            if hasattr(self.process, "wait"):
                # Long enough for a run in production to step on to its next
                # frame and write a checkpoint there, which it does when asked
                # to stop, so that it can be carried on. Five seconds killed it
                # first, and it was carried on from its last interval
                # checkpoint instead.
                from fastmdxplora.simulation.runner import stop_grace_seconds

                try:
                    self.process.wait(timeout=int(stop_grace_seconds()) + 10)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
                # A run this server started is in a session of its own, so
                # its group is its own: a parallel study's workers outlive a
                # parent that had to be killed, and are ended with it. Not a
                # run adopted from an earlier server, whose group is unknown.
                if isinstance(self.process, subprocess.Popen):
                    _end_what_is_left_of(self.process.pid)
            if where is not None:
                _record_the_stop(Path(where))
            self._refresh_process()
            return {
                "stopped": True,
                "detail": ("Workflow stopped. A run in production ends at its next "
                           "frame with a checkpoint there, and `fastmdx resume` "
                           "carries it on."),
                "state": self.snapshot(),
            }


    def _not_the_run_held(self) -> str | None:
        """Why the run adopted here is not signalled, where its process is
        no longer certainly the run (`_what_the_process_is`): its number
        given to another process since, or what it is not told just now.
        None where it is the run, or nothing adopted is held. Called with
        the lock held."""
        held = self.process
        if not isinstance(held, _AdoptedProcess):
            return None
        found = held.what_it_is()
        if found in ("the run", "gone"):
            return None
        if found == "unread":
            return (f"Nothing was stopped: whether process {held.pid} is still the study's run "
                    "could not be told just now. Press Stop again in a moment.")
        # Not the run: no longer held, so the page says the study as its
        # records have it.
        self.process = None
        self.running_root = None
        return (f"Nothing was stopped: process {held.pid}, which the study's record names, is "
                "no longer its run; the system has given its number to another process.")


def _record_the_stop(root: Path) -> None:
    """Record a run stopped here as stopped where it did not record it: a
    run stopped before production ends without a word (minimisation is one
    call that cannot be interrupted), so its record still said it was
    running, the open page said "Running" and, read again, "Interrupted:
    the machine restarted", with a checkpoint that was never written."""
    from fastmdxplora.gui.telemetry import (
        _GOING_STATUSES, TelemetryWriter, read_status, read_study_status)

    try:
        status = read_study_status(root)
        if str(status.get("status") or "").lower() not in _GOING_STATUSES:
            return
        piece = status.get("piece")
        folder = root / str(piece) if isinstance(piece, str) and piece and \
            str(read_status(root / str(piece)).get("status") or "").lower() in _GOING_STATUSES \
            else root
        stage = str(status.get("stage") or "")
        if stage in ("analysis", "report"):
            # The simulation ended long since: only its status and reason
            # are written, its times and stages left as the run left them
            # (a full write made its time 78 hours and its date today's).
            TelemetryWriter(root / "simulation", phases_only=True).write_status(
                status="stopped",
                latest_error=(f"Stopped here in the {'analysis' if stage == 'analysis' else 'report'}"
                              ", as asked. The simulation is finished and kept."))
            return
        said = {"minimization": "minimisation", "nvt": "NVT equilibration",
                "npt": "NPT equilibration", "production": "production"}.get(stage, stage or "its run")
        before = stage in {"setup", "minimization", "nvt", "npt"}
        TelemetryWriter(folder / "simulation").write_status(
            status="stopped",
            latest_error=(
                f"Stopped here in {said}, as asked."
                + (" It had not reached production, so it wrote no checkpoint to carry on "
                   "from: carrying it on runs it again from the start."
                   if before else "")))
    except Exception:  # noqa: BLE001 - the stop stands whatever is recorded
        return


def _demo_available() -> bool:
    from fastmdxplora.demo import offered

    return offered()


def _demo_to_fetch() -> int:
    """What the demo study weighs where it has still to be fetched, or 0."""
    from fastmdxplora.demo import cached, packaged, source

    return 0 if packaged() or cached() else source()["bytes"]


def _analysed_only(root: Path | str) -> bool:
    """Whether a study's record lists the phases it ran and simulation is
    not among them, nor among those its settings asked for: a trajectory
    analysed, not a run that stopped before its simulation began."""
    import yaml

    base = Path(root)
    try:
        manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    phases = manifest.get("phases") if isinstance(manifest, dict) else None
    if not isinstance(phases, list) or not phases:
        return False
    if any(isinstance(p, dict) and p.get("name") == "simulation" for p in phases):
        return False
    try:
        config = yaml.safe_load((base / "resolved_config.yml").read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return False
    asked = config.get("include_phase") if isinstance(config, dict) else None
    return isinstance(asked, list) and "simulation" not in asked
