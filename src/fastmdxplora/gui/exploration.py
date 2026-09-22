"""Exploration layer for the FastMDXplora GUI.

The live dashboard normally watches an existing project output directory.
This module adds a small, deliberately conservative exploration layer so the
same local server can start before a run exists, validate a configuration,
and launch the normal ``fastmdxplora.cli.main explore`` workflow in a child
process.  It does not reimplement any setup, simulation, analysis, or report
science.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

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


class _AdoptedProcess:
    """A run this server did not start, held by its PID.

    Everything the runtime does with a Popen -- poll, terminate, kill,
    wait, pid -- works here through signals. The exit code of a process
    that was never this server's child cannot be read, so once it is gone poll
    reports 0 if the study's manifest says it completed, else 1.
    """

    def __init__(self, pid: int, root: Path) -> None:
        self.pid = int(pid)
        self.root = Path(root)
        self._returncode: int | None = None

    def _alive(self) -> bool:
        return _process_alive(self.pid)

    def poll(self) -> int | None:
        if self._returncode is not None:
            return self._returncode
        if self._alive():
            return None
        manifest = _json_mapping(self.root / "manifest.json")
        done = str(manifest.get("status") or "").lower() in {"completed", "complete", "success"}
        self._returncode = 0 if done else 1
        return self._returncode

    def terminate(self) -> None:
        _terminate_process(self.pid)

    def kill(self) -> None:
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
    running, completed or failed, and the fraction of its steps done. From the
    plan the batch manifest records before any run starts, and each run's own
    telemetry. None where the folder is a study of one run."""
    manifest = _json_mapping(Path(root) / "batch_manifest.json")
    planned = manifest.get("planned") or manifest.get("runs") or []
    if not manifest or not isinstance(planned, list):
        return None
    results = {r.get("run_id"): r for r in (manifest.get("runs") or []) if isinstance(r, dict)}
    out = []
    for entry in planned:
        if not isinstance(entry, dict) or not entry.get("run_id"):
            continue
        run_id = str(entry["run_id"])
        folder = Path(root) / "runs" / run_id
        live = _json_mapping(folder / "simulation" / "live_status.json")
        result = results.get(run_id) or {}
        state = "waiting"
        fraction = None
        if result.get("status") in ("ok", "success", "completed"):
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
                    "fraction": fraction, "stage": live.get("stage") if live else None})
    return out


def study_a_run_belongs_to(root: Path) -> dict[str, Any] | None:
    """For a run under a study of several: that study's folder and this run's
    swept values, so the page can say where it belongs and go back."""
    from fastmdxplora.gui.browse import run_of

    member = run_of(Path(root))
    if not member:
        return None
    return {"path": str(Path(root).parent.parent), "study": member["study"],
            "values": member.get("values") or {}}


def _identify_run(pid: int, root: Path) -> bool | None:
    """Whether the PID is this study's run: True, False, or None for cannot
    tell yet.

    False is definite -- the process is gone, or its command line was read
    and names something else, which is what guards against a stale record
    whose PID the OS has reused. None is not: the process is alive and its
    command line could not be read, which on Windows means PowerShell did
    not answer in time. Adopting on None would let Stop kill a process the
    OS gave the number to, so it is never adopted on None -- but it is
    asked again, rather than taken as a final answer.
    """
    if not _process_alive(pid):
        return False
    line = _command_line_of(pid)
    if line is None:
        return None
    return _command_line_is_a_run(line, root)


def _process_is_this_run(pid: int, root: Path) -> bool:
    """The PID is alive and its command line names this study or the
    program. "Cannot tell" is not "yes": undetermined is not adopted."""
    return _identify_run(pid, root) is True


def _command_line_is_a_run(line: str, root: Path) -> bool:
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
    # Analysis is in the plan unless the phase list leaves it out.
    phases = config.get("include_phase")
    runs_analysis = not isinstance(phases, list) or "analysis" in phases
    missing = missing_dependencies(include_analysis=runs_analysis)

    if not missing:
        return None
    return dependency_error_message(missing)


def _json_mapping_or_yaml(path: Path) -> dict[str, Any]:
    """A config as written, for the preflight: the file the launch wrote."""
    import yaml

    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return value if isinstance(value, dict) else {}


def _json_mapping(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
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
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    # A study whose run could not yet be identified, and the thread asking
    # again. Cleared when a different study is opened, so a retry never
    # adopts the run of a study the person has already left.
    _adoption_root: Path | None = field(default=None, repr=False)
    _adoption_thread: threading.Thread | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.workspace_root = self.workspace_root.expanduser().resolve()
        self.exploration_root = self.exploration_root.expanduser().resolve()
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.exploration_root.mkdir(parents=True, exist_ok=True)
        if self.active_root is not None:
            self.active_root = self.active_root.expanduser().resolve()
            self._adopt_if_running(self.active_root)

    def data_root(self) -> Path:
        with self.lock:
            if self.data_stale or self.active_root is None:
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
            stage = f"{done} of {len(runs)} completed"
            if going:
                stage += f", {going} running"
            if failed:
                stage += f", {failed} failed"
            out: dict[str, Any] = {"stage": stage, "runs": len(runs), "completed": done}
            fractions = [r.get("fraction") for r in runs if r.get("fraction") is not None]
            if runs:
                out["percent"] = round(100.0 * sum(fractions) / len(runs), 1)
            return out
        status = _json_mapping(Path(root) / "simulation" / "live_status.json")
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
        status = _json_mapping(self.active_root / "simulation" / "live_status.json")
        recorded = status.get("run_started_at") or status.get("last_update_timestamp")
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
        if self.log_path is not None:
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
        if self.process_returncode != 0 and self.completion_error is None:
            self.completion_error = self._process_failure_message()
            self.data_stale = self._telemetry_predates_process()
            if self.data_stale:
                self.active_root = None
        elif self.process_returncode == 0 and self.completion_error is None:
            self.completion_error = self._completed_run_error()
            if self.completion_error:
                self._record_completion_failure(self.completion_error)

    def _completed_run_error(self) -> str | None:
        """Reject a false-success child that produced no simulation results."""
        root = self.active_root
        if root is None or not self.command or "explore" not in self.command:
            return None

        setup_dir = root / "setup"
        required_setup = ("system.xml", "state.xml", "topology.pdb")
        missing_setup = [name for name in required_setup if not (setup_dir / name).is_file()]
        if missing_setup:
            setup_manifest = _json_mapping(setup_dir / "setup_parameters.json")
            detail = _manifest_note(setup_manifest)
            suffix = f" Details: {detail}" if detail else ""
            return (
                "Setup did not produce the files required for simulation "
                f"({', '.join(missing_setup)}).{suffix} See "
                f"{self.log_path or root / 'exploration.log'} for the full log."
            )

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
            elif self.completion_error and viewing_running:
                status = "failed"
            elif self.process is not None and self.process_returncode == 0 and viewing_running:
                status = "completed"
            elif self.process is not None and self.process_returncode is not None and viewing_running:
                status = "failed"
            return {
                "mode": "home" if self.active_root is None or self.data_stale else "run",
                "status": status,
                "active_run": str(self.active_root) if self.active_root and not self.data_stale else None,
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
                "runs": (runs_of_a_study(self.active_root)
                         if self.active_root and not self.data_stale else None),
                "run_of": (study_a_run_belongs_to(self.active_root)
                           if self.active_root and not self.data_stale else None),
                "command": list(self.command),
                "can_launch": not running,
            }

    def _spawn(
        self,
        command: list[str],
        output_dir: Path,
        dashboard_url: str | None,
    ) -> dict[str, Any]:
        """Start a run and take ownership of the process.

        Shared, because there is more than one way to describe a run but only
        one way to run it. Assumes the caller holds the lock and has already
        refused a second concurrent run.
        """
        log_path = output_dir / "exploration.log"
        env = os.environ.copy()
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
        self.process = process
        self.process_started_at = _utc_now()
        self.process_finished_at = None
        self.process_returncode = None
        self.completion_error = None
        self.log_path = log_path
        self.command = command
        return {
            "launched": True,
            "output": str(output_dir),
            "pid": process.pid,
            "command": command,
            "state": self.snapshot(),
        }

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
        from fastmdxplora.gui.run_from_config import prepare_run

        with self.lock:
            self._refresh_process()
            if self.process is not None and self.process.poll() is None:
                return {
                    "ok": False,
                    "error": "A FastMDXplora workflow is already running.",
                }

            # A results folder may be a name or a path. Browsing to one puts
            # an absolute path in the box, and running that through the slug
            # turned /Users/someone/work into a folder called
            # Users_someone_work sitting inside the launch directory -- which
            # is neither where they pointed nor anywhere they would look.
            source: Mapping[str, Any] = config if config is not None else (state or {})
            requested = str(dict(source).get("output") or "").strip()
            if not requested:
                # Timestamped, as the CLI's and the builder's defaults are.
                # A fixed name meant the second study the Agent wrote
                # collided with the first: "Output folder already exists
                # and is not empty". The refusal is right; the default
                # should not make it fire.
                from fastmdxplora.naming import default_output_name, system_of

                requested = default_output_name(system_of(dict(source)))
            candidate = Path(requested).expanduser()
            if candidate.is_absolute():
                output_dir = candidate.resolve()
            else:
                # A bare name is a folder beside the others this GUI made.
                output_dir = (self.exploration_root / _slug(requested)).resolve()

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

            prepared = prepare_run(dict(state) if state else None, output_dir,
                                   config=dict(config) if config is not None else None)
            if not prepared["ok"]:
                return prepared
            # Refused here, before a process is spawned, when the chemistry
            # stack it needs is not installed: the phase would fail inside
            # the run with the same message, minutes later and off screen.
            environment_error = exploration_environment_error(
                _json_mapping_or_yaml(Path(prepared["config_path"])))
            if environment_error:
                return {"ok": False, "error": environment_error,
                        "config_path": prepared["config_path"], "command": None}

            # A previous rejected launch may have hidden its old telemetry.
            # This is a new process and must become the current run even when
            # it is launched from the config-based Run page.
            self.data_stale = False
            started = self._spawn(prepared["command"], output_dir, dashboard_url)
            return {
                "ok": True,
                "error": None,
                "config_path": prepared["config_path"],
                **started,
            }

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
        from fastmdxplora.gui.config_builder import check_config_file

        with self.lock:
            self._refresh_process()
            if self.process is not None and self.process.poll() is None:
                return {
                    "ok": False,
                    "error": "A FastMDXplora workflow is already running.",
                }

            checked = check_config_file(config_path)
            if not checked["ok"]:
                return checked

            source = Path(checked["path"])
            requested = (output or "").strip()
            if requested:
                candidate = Path(requested).expanduser()
                output_dir = (
                    candidate.resolve()
                    if candidate.is_absolute()
                    else (self.exploration_root / _slug(requested)).resolve()
                )
            else:
                # Beside the config, under a name taken from it, so a config
                # kept with its data leaves its results there too.
                output_dir = (source.parent / f"{source.stem}_output").resolve()
            if output_dir.exists() and any(output_dir.iterdir()):
                detail = f"Output folder already exists and is not empty: {output_dir}. Choose a new output folder to start a new simulation."
                self.data_stale = True
                self.completion_error = detail
                return {
                    "ok": False,
                    "error": detail,
                    "next_action": "Choose a new output folder; the previous run was preserved.",
                }
            output_dir.mkdir(parents=True, exist_ok=True)
            self.data_stale = False

            command = [
                sys.executable, "-m", "fastmdxplora.cli.main", "explore",
                "--config", str(source), "--output", str(output_dir),
            ]
            started = self._spawn(command, output_dir, dashboard_url)
            return {
                "ok": True,
                "error": None,
                "config_path": str(source),
                "modified": False,
                **started,
            }

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
        from fastmdxplora.orchestrator import RUN_PROCESS_FILE

        record = _json_mapping(Path(root) / RUN_PROCESS_FILE)
        pid = record.get("pid")
        if not isinstance(pid, int) or pid <= 0:
            return False
        identity = _identify_run(pid, Path(root))
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
        self.process = _AdoptedProcess(pid, Path(root))
        self.running_root = Path(root)
        self.process_started_at = str(record.get("started_at") or _utc_now())
        self.process_finished_at = None
        self.process_returncode = None
        self.completion_error = None
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

        from fastmdxplora.orchestrator import RUN_PROCESS_FILE

        deadline = time.monotonic() + ADOPTION_RETRY_SECONDS
        while time.monotonic() < deadline:
            time.sleep(ADOPTION_RETRY_INTERVAL)
            with self.lock:
                if self._adoption_root != root:
                    return  # the person has opened a different study
                if self.process is not None and self.process.poll() is None:
                    return  # already watching a run
            record = _json_mapping(root / RUN_PROCESS_FILE)
            pid = record.get("pid")
            if not isinstance(pid, int) or pid <= 0:
                return  # the run finished and took its record with it
            # Outside the lock: on Windows this can take ten seconds, and
            # the page's own requests must not wait behind it.
            identity = _identify_run(pid, root)
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
                self.log_path = None
                self.command = []
            return {"ok": True, "active_run": str(path), "state": self.snapshot()}

    def stop(self) -> dict[str, Any]:
        with self.lock:
            self._refresh_process()
            if self.process is None or self.process.poll() is not None:
                return {"stopped": False, "detail": "No workflow is currently running.", "state": self.snapshot()}
            self.process.terminate()
            if hasattr(self.process, "wait"):
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
            self._refresh_process()
            return {
                "stopped": True,
                "detail": "Workflow terminated.",
                "state": self.snapshot(),
            }
