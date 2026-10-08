"""File-backed telemetry for local simulation monitoring."""

from __future__ import annotations

import csv
import json
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


STATUS_FILE = "live_status.json"
METRICS_FILE = "live_metrics.csv"
EVENTS_FILE = "live_events.log"

STAGE_ORDER = (
    "setup",
    "minimization",
    "nvt",
    "npt",
    "production",
    "analysis",
    "report",
)

METRIC_FIELDS = [
    "timestamp",
    "stage",
    "step",
    "simulation_time_ns",
    "potential_energy",
    "kinetic_energy",
    "total_energy",
    "temperature",
    "volume",
    "density",
    "speed",
    "pressure",
    "current_frame_count",
    "progress_percent",
]

NUMERIC_METRIC_FIELDS = [field for field in METRIC_FIELDS if field != "timestamp" and field != "stage"]

NORMAL_EXPLANATION = "Simulation is progressing normally."
NUMERIC_EXPLANATION = (
    "The simulation became numerically unstable. This usually means the timestep "
    "is too large, the starting structure has clashes, the temperature is too high, "
    "or equilibration was not gentle enough."
)
ENERGY_EXPLANATION = (
    "Energy increased sharply. This can indicate steric clashes, unstable timestep, "
    "bad contacts, or pressure/temperature coupling issues."
)
TEMPERATURE_EXPLANATION = (
    "Temperature is outside the expected range. Consider a smaller timestep, stronger "
    "friction, gentler heating, or checking the input structure."
)
INTERRUPTED_EXPLANATION = (
    "Its last update said it was still going, and the process that ran it is "
    "gone: the machine restarted, or its job was ended by a scheduler or by "
    "hand. What it wrote up to then is kept, and the command fastmdx resume "
    "says whether it can be carried on from its last checkpoint."
)

STOPPED_EXPLANATION = (
    "It was asked to stop (Ctrl+C, or Stop) and ended where it can be "
    "carried on. What it wrote up to then is kept, and fastmdx resume "
    "carries it on from there."
)

SILENT_EXPLANATION = (
    "Its last update said it was still going, and no process on this machine "
    "runs it. If it runs on another machine or under a scheduler, look there "
    "first: a job that is held, or an analysis of a long trajectory, can be "
    "quiet this long. Carry it on with fastmdx resume only once it is "
    "certainly not running, or two runs will write to the same study."
)

STALE_EXPLANATION = (
    "No telemetry update has been seen recently. The simulation may be slow, paused, "
    "or crashed."
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _simulation_dir(path: str | Path) -> Path:
    root = Path(path)
    return root if root.name == "simulation" else root / "simulation"


@dataclass
class TelemetryWriter:
    """Write live status, metrics, and event files without failing the run."""

    simulation_dir: str | Path
    enabled: bool = True
    total_steps: int | None = None
    planned_frames: int | None = None
    timestep_fs: float | None = None
    platform: str | None = None
    #: Recorded beside the platform, because the two are one answer to "what
    #: is this running on" and the page asks for both. Only the platform was
    #: written, so a run in progress showed a dash where the precision goes:
    #: the simulation manifest that carries it is not written until the run
    #: ends. ``precision_applied`` records whether the selected platform took
    #: the value -- ``Precision`` is a CUDA/OpenCL/HIP property, so on CPU the
    #: requested setting is carried but never applied, and printing it alone
    #: would claim otherwise.
    precision: str | None = None
    precision_applied: bool | None = None
    target_temperature_K: float | None = None
    start_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    _last_status: dict[str, Any] = field(default_factory=dict, init=False)

    @property
    def root(self) -> Path:
        return Path(self.simulation_dir)

    @property
    def status_path(self) -> Path:
        return self.root / STATUS_FILE

    @property
    def metrics_path(self) -> Path:
        return self.root / METRICS_FILE

    @property
    def events_path(self) -> Path:
        return self.root / EVENTS_FILE

    def write_status(self, **updates: Any) -> None:
        """Atomically update the shared live-status document.

        The project orchestrator and the OpenMM runner use separate writer
        instances.  Reading the on-disk document before every update keeps
        phase and sub-stage history intact when control moves from setup to
        simulation to analysis/report.  The file is dashboard-only telemetry;
        none of these values are read back into OpenMM.
        """
        if not self.enabled:
            return
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            now = datetime.now(timezone.utc)
            persisted = _read_status_path(self.status_path)
            status = {
                # A run that has not reported a stage does not have one yet.
                # Written as "not available" this travelled to the page and
                # was displayed where the name of a stage goes, so a run that
                # had only just started described itself that way.
                "stage": None,
                "status": "running",
                "current_step": None,
                "total_planned_steps": self.total_steps,
                "current_frame_count": None,
                "planned_frame_count": self.planned_frames,
                "simulation_time_completed_ns": None,
                "timestep_fs": self.timestep_fs,
                "platform": self.platform,
                "precision": self.precision,
                "precision_applied": self.precision_applied,
                "target_temperature_K": self.target_temperature_K,
                "current_checkpoint_path": None,
                "latest_warning": None,
                "latest_error": None,
                "stage_states": {name: "waiting" for name in STAGE_ORDER},
                "run_started_at": self.start_time.isoformat(),
            }
            status.update(self._last_status)
            # On-disk data may have been written by the simulation runner or
            # orchestrator since this instance last wrote. It is therefore
            # newer than this instance's cache -- but only where it says
            # something. A None on disk is a default nobody filled in, and
            # letting it through erased what this instance knows: the
            # orchestrator opens the file to mark the setup phase, writing
            # platform=None before the runner exists, so the runner's own
            # platform, precision and step count never reached the page. No
            # caller sets a field to None deliberately -- updates below drop
            # Nones -- so a None on disk is never a value being cleared.
            status.update({k: v for k, v in persisted.items() if v is not None})
            status.update({k: v for k, v in updates.items() if v is not None})

            states = status.get("stage_states")
            if not isinstance(states, dict):
                states = {}
            status["stage_states"] = {
                name: str(states.get(name, "waiting")).lower()
                for name in STAGE_ORDER
            }

            started = _parse_iso_datetime(status.get("run_started_at")) or self.start_time
            status["run_started_at"] = started.isoformat()
            status["elapsed_wall_time_s"] = round((now - started).total_seconds(), 3)
            status["last_update_timestamp"] = _utc_now()
            self._last_status = dict(status)
            tmp = self.status_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(status, indent=2, default=str), encoding="utf-8")
            os.replace(tmp, self.status_path)
        except Exception:
            return

    def mark_stage(
        self,
        stage: str,
        state: str,
        *,
        status: str | None = None,
        **updates: Any,
    ) -> None:
        """Record one timeline stage without erasing the other stages."""
        name = str(stage or "").strip().lower()
        if name not in STAGE_ORDER:
            return
        persisted = _read_status_path(self.status_path)
        states = persisted.get("stage_states")
        if not isinstance(states, dict):
            states = {}
        merged = {item: str(states.get(item, "waiting")).lower() for item in STAGE_ORDER}
        merged[name] = str(state or "waiting").lower()
        payload = dict(updates)
        payload.update({
            "stage": name,
            "stage_states": merged,
        })
        if status is not None:
            payload["status"] = status
        self.write_status(**payload)

    def append_metric(self, **row: Any) -> None:
        if not self.enabled:
            return
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            exists = self.metrics_path.exists()
            clean_row = {field: row.get(field, "") for field in METRIC_FIELDS}
            clean_row["timestamp"] = clean_row["timestamp"] or _utc_now()
            with self.metrics_path.open("a", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=METRIC_FIELDS)
                if not exists:
                    writer.writeheader()
                writer.writerow(clean_row)
        except Exception:
            return

    def event(self, message: str, *, level: str = "info") -> None:
        if not self.enabled:
            return
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            with self.events_path.open("a", encoding="utf-8") as fh:
                fh.write(f"{_utc_now()}\t{level}\t{message}\n")
        except Exception:
            return


def _read_status_path(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _parse_iso_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def read_status(project_root: str | Path) -> dict[str, Any]:
    path = _simulation_dir(project_root) / STATUS_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


# ---------------------------------------------------------------------------
# A study carried on in pieces.
#
# `fastmdx resume` and **What would fix it** carry a stopped production on in
# the study's next segment (`segment-001/`, ...), each with a live record of
# its own, and then join the pieces and analyse the whole in the study's own
# folder. The study's record kept saying what its first piece said: the page
# read "Production stopped" while the resume ran, then "Stopped with an
# error" (its stop) once every phase had finished, with the first piece's
# 26 of 100 frames. The GUI reads the study as one run: the stage of
# whichever record was written last, the newest piece's error and
# checkpoint, and the steps and frames of every piece counted together.
# ---------------------------------------------------------------------------

def _pieces_of(root: Path) -> list[Path]:
    """The study's segments that kept a live record, in the order run."""
    found: list[tuple[int, Path]] = []
    for folder in root.glob("segment-*"):
        try:
            index = int(folder.name.split("-", 1)[1])
        except (IndexError, ValueError):
            continue
        if index > 0 and (folder / "simulation" / STATUS_FILE).is_file():
            found.append((index, folder))
    return [folder for _, folder in sorted(found)]


def _frames_kept(folder: Path) -> int | None:
    """The production frames a piece contributes to the joined trajectory:
    all it wrote where it finished, those before its last checkpoint where
    it was stopped (the rest are run again by the next piece)."""
    from fastmdxplora.analysis.joining import _finished
    from fastmdxplora.simulation.resume import frames_before_checkpoint

    simulation = folder / "simulation"
    try:
        if _finished(simulation):
            count = read_status(folder).get("current_frame_count")
            return int(count) if isinstance(count, (int, float)) else None
        return frames_before_checkpoint(folder)
    except Exception:  # noqa: BLE001 - a record, not a verdict
        return None


def _interval_of(folder: Path) -> int | None:
    from fastmdxplora.simulation.resume import trajectory_interval_of

    try:
        return trajectory_interval_of(folder)
    except Exception:  # noqa: BLE001 - a record, not a verdict
        return None


def _offsets_of(root: Path, first: dict[str, Any], pieces: list[Path]) -> list[tuple[int, int]] | None:
    """For each piece, the study's steps and production frames before it
    began: its equilibration, then what every earlier piece kept. None
    where a piece's share cannot be counted."""
    equilibration = 0
    for key in ("nvt_steps_planned", "npt_steps_planned"):
        value = first.get(key)
        if isinstance(value, (int, float)) and value > 0:
            equilibration += int(value)
    frames = 0
    steps = equilibration
    offsets: list[tuple[int, int]] = []
    for before in [root, *pieces[:-1]]:
        kept, interval = _frames_kept(before), _interval_of(before)
        if kept is None or interval is None:
            return None
        frames += kept
        steps += kept * interval
        offsets.append((steps, frames))
    return offsets


def _newer(one: dict[str, Any], other: dict[str, Any]) -> bool:
    first = _parse_iso_datetime(str(one.get("last_update_timestamp") or ""))
    second = _parse_iso_datetime(str(other.get("last_update_timestamp") or ""))
    if first is None or second is None:
        return first is not None
    return first > second


def _as_one_run(root: Path, first: dict[str, Any]) -> dict[str, Any]:
    pieces = _pieces_of(root)
    if not pieces:
        return first
    newest = pieces[-1]
    last = read_status(newest)
    if not last:
        return first
    whole = dict(first)
    whole["piece"] = newest.name
    whole["pieces"] = len(pieces) + 1
    # What the simulation last said of itself is its newest piece's word.
    for key in ("latest_error", "latest_warning", "current_checkpoint_path", "platform",
                "precision", "precision_applied"):
        if key in last:
            whole[key] = last.get(key)
    if _newer(last, first):
        # The piece is the newest word: the study is where it is.
        stage = str(last.get("stage") or "").lower()
        whole["status"] = last.get("status")
        # A piece only produces, whatever stage it was first marked in.
        whole["stage"] = stage if stage in {"analysis", "report"} else "production"
        whole["last_update_timestamp"] = last.get("last_update_timestamp")
        states = dict(first.get("stage_states") or {}) if isinstance(
            first.get("stage_states"), dict) else {}
        said = str(last.get("status") or "").lower()
        states["production"] = ("current" if said in _GOING_STATUSES
                                else str((last.get("stage_states") or {}).get(
                                    "production") or said or "waiting"))
        for later in ("analysis", "report"):
            if states.get(later) not in (None, "skipped"):
                states[later] = "waiting"
        whole["stage_states"] = states
        elapsed = [value for value in (first.get("elapsed_wall_time_s"),
                                       last.get("elapsed_wall_time_s"))
                   if isinstance(value, (int, float))]
        if elapsed:
            whole["elapsed_wall_time_s"] = sum(elapsed)
    offsets = _offsets_of(root, first, pieces)
    if offsets is None:
        return whole
    steps_before, frames_before = offsets[-1]
    for key, before in (("current_step", steps_before), ("total_planned_steps", steps_before),
                        ("current_frame_count", frames_before),
                        ("planned_frame_count", frames_before)):
        value = last.get(key)
        if isinstance(value, (int, float)):
            whole[key] = int(before + value)
    produced = last.get("production_steps_planned")
    if isinstance(produced, (int, float)):
        whole["production_steps_planned"] = int(steps_before - (whole.get(
            "nvt_steps_planned") or 0) - (whole.get("npt_steps_planned") or 0) + produced)
    step, timestep = whole.get("current_step"), whole.get("timestep_fs")
    if isinstance(step, (int, float)) and isinstance(timestep, (int, float)):
        whole["simulation_time_completed_ns"] = float(step) * float(timestep) / 1_000_000.0
    return whole


def read_study_status(project_root: str | Path) -> dict[str, Any]:
    """The study's live record, read as one run across the pieces it was
    carried on in (`segment-001/`, ...). For the GUI; a run reads its own
    record with `read_status`."""
    root = Path(project_root)
    if root.name == "simulation":
        root = root.parent
    first = read_status(root)
    if not first:
        return first
    try:
        whole = _as_one_run(root, first)
    except Exception:  # noqa: BLE001 - a record, not a verdict
        whole = dict(first)
    if "current_checkpoint_path" in whole:
        whole["current_checkpoint_path"] = _checkpoint_here(root, whole)
    return whole


def _checkpoint_here(root: Path, status: dict[str, Any]) -> str | None:
    """The checkpoint the record names, as a file in this study, or None.

    The record names where the run will write its checkpoint from its
    start, so a run stopped before production was offered one that did not
    exist; and it names it as the run was asked to, so a study run as
    `trpcage` and opened under another name named a file in another folder.
    """
    named = str(status.get("current_checkpoint_path") or "").strip()
    if not named:
        return None
    path = Path(named)
    piece = status.get("piece")
    folders = [root / str(piece), root] if isinstance(piece, str) and piece else [root]
    candidates = [path] if path.is_absolute() else []
    # Read through another name for the folder (macOS's /private/var for
    # /var), or the study moved: the named path's own last parts, under
    # the study, from the longest down.
    tails = [Path(*path.parts[i:]) for i in range(max(len(path.parts) - 3, 1), len(path.parts))]
    for folder in folders:
        candidates += [folder / path] + [folder / tail for tail in tails]
        candidates.append(folder / "simulation" / path.name)
    root_resolved = root.resolve()
    for candidate in candidates:
        try:
            found = candidate.resolve()
        except OSError:
            continue
        if found.is_file() and (found == root_resolved or root_resolved in found.parents):
            return str(found)
    return None


#: What a run's record says while the run goes on. A record a run left
#: saying so when it ended without writing its end (the machine restarted,
#: or its job was ended by a scheduler or by hand) is not taken at its word.
_GOING_STATUSES = frozenset({"running", "starting", "paused"})

#: The stages whose steps the record counts: a run that ended in one of
#: them with every planned step taken finished its simulation.
_STEPPED_STAGES = frozenset({"minimization", "nvt", "npt", "production"})

#: How long a run's record may go unwritten, with no process on this machine
#: to ask (it runs on another machine or in a container, or kept no record
#: of its process), before it is taken to have ended. The record is written
#: every 1,000 steps unless asked otherwise, which at 2 fs and 0.1 ns a day
#: is under half an hour; setup, analysis and report write at their start
#: and end only, and an analysis of a long trajectory on a cluster node can
#: be quiet for hours. A day says nothing wrong of any of them.
SILENT_RUN_ENDED_AFTER_SECONDS = 24 * 3600

#: What its process was found to be, kept a few seconds: the page asks
#: every few seconds, and asking means starting `ps` (PowerShell on Windows).
_ASKED: dict[tuple[str, int, str], tuple[float, str | None]] = {}
_ASKED_FOR_SECONDS = 10.0


def _process_started_at(pid: int) -> float | None:
    """When a process started, as a Unix time, where the system says (Linux);
    None elsewhere."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        ticks = int(stat.rsplit(")", 1)[1].split()[19])
        booted = next(int(line.split()[1]) for line in
                      Path("/proc/stat").read_text(encoding="utf-8").splitlines()
                      if line.startswith("btime "))
        return booted + ticks / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError, StopIteration, AttributeError):
        return None


def _this_pid_space() -> str:
    try:
        return os.readlink("/proc/self/ns/pid")
    except (OSError, AttributeError):
        return ""


def _what_its_process_says(root: Path, record: dict[str, Any]) -> str | None:
    """"gone" where the record's process has certainly ended, "running"
    where it is this study's run, None where it cannot be told here (and
    the record's silence decides).

    Asked only where its number means the same process here: the same host,
    the same boot and, where both say, the same process namespace (a
    container sharing the host's name and boot numbers its processes on its
    own). The same host restarted since is certainly gone. A process alive
    whose command line is not a run's is not taken for gone: a run started
    from a script, a notebook or a pool's worker has such a command line.
    Only one that began after the record was written is another process
    given the number, and the run gone.
    """
    from fastmdxplora.orchestrator import this_machine
    from fastmdxplora.simulation.resume import _this_process_and_its_parents

    pid = record.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        return None
    here = this_machine()
    if not record.get("host") or record.get("host") != here.get("host"):
        return None
    there_boot, here_boot = record.get("boot") or "", here.get("boot") or ""
    if there_boot and here_boot and there_boot != here_boot:
        return "gone"
    if bool(there_boot) != bool(here_boot):
        # One side says which boot and the other cannot (Windows beside WSL).
        return None
    if record.get("pidns") and _this_pid_space() and record["pidns"] != _this_pid_space():
        return None
    if pid in _this_process_and_its_parents():
        return "running"
    key = (str(root), pid, str(record.get("started_at") or ""))
    now = datetime.now(timezone.utc).timestamp()
    kept = _ASKED.get(key)
    if kept and now - kept[0] < _ASKED_FOR_SECONDS:
        return kept[1]
    from fastmdxplora.gui.exploration import _identify_run, _process_alive

    try:
        if not _process_alive(pid):
            said: str | None = "gone"
        elif _identify_run(pid, root, record.get("argv")) is True:
            said = "running"
        else:
            began = _process_started_at(pid)
            written = _parse_iso_datetime(record.get("started_at"))
            said = ("gone" if began is not None and written is not None
                    and began > written.timestamp() + 5 else None)
    except Exception:  # noqa: BLE001 - a record, not a verdict
        said = None
    if len(_ASKED) > 256:
        _ASKED.clear()
    _ASKED[key] = (now, said)
    return said


def how_the_run_ended(project_root: str | Path,
                      status: dict[str, Any] | None = None) -> str | None:
    """How the run whose record says it is going is known to have ended
    without saying so: "process" (its process is gone), "silence" (no
    process here to ask, and nothing written for
    `SILENT_RUN_ENDED_AFTER_SECONDS`), or None while it may still be going.
    Never for the process asking, or what started it: a run reads its own
    record while it runs."""
    from fastmdxplora.orchestrator import RUN_PROCESS_FILE

    root = Path(project_root)
    status = read_study_status(root) if status is None else status
    if str(status.get("status") or "").lower() not in _GOING_STATUSES:
        return None
    piece = status.get("piece")
    if (isinstance(piece, str) and piece.startswith("segment-")
            and str(status.get("stage") or "") == "production"
            and (root / piece).is_dir()):
        # The piece being run keeps the record of its process.
        root = root / piece
    try:
        record = json.loads((root / RUN_PROCESS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        record = None
    said = _what_its_process_says(root, record) if isinstance(record, dict) else None
    if said == "gone":
        return "process"
    if said == "running":
        return None
    age = _timestamp_age_seconds(str(status.get("last_update_timestamp") or ""))
    return "silence" if age is not None and age > SILENT_RUN_ENDED_AFTER_SECONDS else None


def run_ended_unsaid(project_root: str | Path, status: dict[str, Any] | None = None) -> bool:
    """Whether the run whose record says it is going has ended without
    saying so (`how_the_run_ended`)."""
    return how_the_run_ended(project_root, status) is not None


def status_as_it_stands(project_root: str | Path) -> dict[str, Any]:
    """The run's record as the GUI shows it: as written, except for a run
    that ended without saying so. One that had taken every step it planned
    in a stage of the simulation finished it (the Python API's `simulate`
    writes no end of its own); any other is `interrupted`, with how that is
    known as `ended_by`. What the record said is kept as `recorded_status`.
    For the GUI's own process only; a run reads its record with
    `read_status`."""
    status = read_study_status(project_root)
    ended = how_the_run_ended(project_root, status) if status else None
    if ended is None:
        return status
    step, total = status.get("current_step"), status.get("total_planned_steps")
    finished = (str(status.get("stage") or "").lower() in _STEPPED_STAGES
                and isinstance(step, (int, float)) and isinstance(total, (int, float))
                and 0 < total <= step)
    return {**status, "status": "completed" if finished else "interrupted",
            "ended_by": ended, "recorded_status": status.get("status")}


#: The files a run leaves behind that say what it was asked to do. The first
#: is written by the GUI before the run starts, the second by the run itself.
_CONFIG_NAMES = ("exploration.yml", "resolved_config.yml", "config.yml")

#: Every phase, in the order they happen, with the stages each one accounts
#: for on the timeline. A run that includes only `analysis` has no
#: minimization to wait for, and a timeline showing one greyed out forever
#: looks exactly like a run that stalled.
PHASE_STAGES = {
    "setup": ("setup",),
    "simulation": ("minimization", "nvt", "npt", "production"),
    "analysis": ("analysis",),
    "report": ("report",),
}


def run_phases(project_root: str | Path) -> list[str]:
    """Which phases this run includes, read from the config it kept.

    Empty where nothing says -- an older run, or one started outside the GUI
    without a config beside it. Callers should treat that as "assume all",
    because hiding a stage that turns out to run is worse than showing one
    that does not.
    """
    import yaml

    root = Path(project_root)

    # What ran, where that is recorded, beats any inference about what was
    # meant to run.
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        manifest = {}
    if isinstance(manifest, dict):
        ran = [
            str(entry.get("name"))
            for entry in manifest.get("phases", [])
            if isinstance(entry, dict) and entry.get("name")
        ]
        if ran:
            return ran

    for name in _CONFIG_NAMES:
        path = root / name
        if not path.is_file():
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(data, dict):
            continue
        # Canonicalised here because the timeline reads the config file directly, not through the loader, so a study
        # written with the earlier `include` is read the same way.
        from fastmdxplora.config.loader import canonical_phase_keys

        canonical_phase_keys(data)
        included = data.get("include_phase")
        if isinstance(included, list) and included:
            return [str(phase) for phase in included]
        excluded = data.get("exclude_phase")
        if isinstance(excluded, list) and excluded:
            names = {str(phase) for phase in excluded}
            return [phase for phase in PHASE_STAGES if phase not in names]
        # Nothing further to go on. A phase block is not a statement about
        # what runs, and reading it as one hid two stages that had just
        # finished -- and only once the run ended, because the config is
        # written into the output directory at the end.
        #
        # That used to be because `write_resolved_config` wrote only phases
        # with non-empty options, so analysis and report, which run on
        # defaults, left no trace in it. It now names every setting every
        # phase used, so the blocks are always all four and say even less
        # about what ran. Either way `include` and `exclude` are the only
        # keys that answer this question.
    return []


def run_stages(project_root: str | Path) -> list[str]:
    """The timeline stages this run can actually reach."""
    phases = run_phases(project_root)
    if not phases:
        return [stage for stages in PHASE_STAGES.values() for stage in stages]
    return [
        stage
        for phase in PHASE_STAGES
        if phase in phases
        for stage in PHASE_STAGES[phase]
    ]


def read_metrics(project_root: str | Path, *, limit: int | None = 500) -> list[dict[str, Any]]:
    """Read dashboard metrics, enriching them from OpenMM's ``energy.csv``.

    Older runs and very short smoke tests may have only one of the two files.
    The live CSV has stable FastMDX field names, while OpenMM's reporter uses
    human-readable headings that vary slightly by version.  Normalising and
    merging both sources keeps the dashboard useful for live and completed
    runs without changing the scientific output files.
    """

    simulation_dir = _simulation_dir(project_root)
    rows = _metrics_of(simulation_dir)
    root = simulation_dir.parent
    pieces = _pieces_of(root) if rows else []
    if pieces:
        try:
            rows = _metrics_as_one_run(root, rows, pieces)
        except Exception:  # noqa: BLE001 - the first piece's samples stand
            pass

    # The frame count is status-level information in older telemetry schemas.
    # Attach it to the latest sample so overview metric cards can still render.
    if rows:
        status = read_study_status(project_root)
        frame_count = status.get("current_frame_count")
        if frame_count is not None and rows[-1].get("current_frame_count") in (None, ""):
            rows[-1]["current_frame_count"] = str(frame_count)
    return rows if limit is None else rows[-limit:]


def _metrics_as_one_run(root: Path, rows: list[dict[str, Any]],
                        pieces: list[Path]) -> list[dict[str, Any]]:
    """The samples of every piece on the study's own clock: the first
    piece's up to where the next carried on, then each piece's production
    samples moved on by the steps and frames before it."""
    offsets = _offsets_of(root, read_status(root), pieces)
    if offsets is None:
        return rows
    first = read_status(root)
    timestep = _safe_float(first.get("timestep_fs"))
    total = read_study_status(root).get("total_planned_steps")
    kept = [row for row in rows
            if (_safe_float(row.get("step")) or 0.0) <= offsets[0][0]]
    for index, (piece, (steps, frames)) in enumerate(zip(pieces, offsets)):
        ends = offsets[index + 1][0] if index + 1 < len(offsets) else None
        for row in _metrics_of(piece / "simulation"):
            if "production" not in str(row.get("stage") or "").lower():
                continue
            step = _safe_float(row.get("step"))
            if step is None:
                continue
            moved = dict(row)
            moved["step"] = str(int(steps + step))
            if ends is not None and steps + step > ends:
                continue
            if timestep:
                moved["simulation_time_ns"] = str((steps + step) * timestep / 1_000_000.0)
            count = _safe_float(row.get("current_frame_count"))
            if count is not None:
                moved["current_frame_count"] = str(int(frames + count))
            if isinstance(total, (int, float)) and total > 0:
                moved["progress_percent"] = str(100.0 * (steps + step) / float(total))
            kept.append(moved)
    return kept


def _metrics_of(simulation_dir: Path) -> list[dict[str, Any]]:
    """One run's samples: its live record, filled in from OpenMM's
    ``energy.csv`` where a sample lacks a value."""
    live_rows = _read_csv_rows(simulation_dir / METRICS_FILE)
    energy_rows = _read_energy_rows(simulation_dir / "energy.csv")

    if live_rows and energy_rows:
        energy_by_step = {
            str(row.get("step")): row
            for row in energy_rows
            if row.get("step") not in (None, "")
        }
        merged: list[dict[str, Any]] = []
        for row in live_rows:
            item = dict(row)
            fallback = energy_by_step.get(str(item.get("step")), {})
            for key, value in fallback.items():
                if item.get(key) in (None, "") and value not in (None, ""):
                    item[key] = value
            merged.append(item)
        rows = merged
    else:
        rows = live_rows or energy_rows
    return rows


def _read_csv_rows(path: Path) -> list[dict[str, Any]]:
    try:
        with path.open(newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    except OSError:
        return []


def _read_energy_rows(path: Path) -> list[dict[str, Any]]:
    raw_rows = _read_csv_rows(path)
    result: list[dict[str, Any]] = []
    for raw in raw_rows:
        row: dict[str, Any] = {}
        for original_key, value in raw.items():
            if original_key is None:
                continue
            key = _normalise_energy_header(original_key)
            if key:
                row[key] = value
        time_ps = _safe_float(row.pop("time_ps", None))
        if time_ps is not None:
            row["simulation_time_ns"] = str(time_ps / 1000.0)
        if row:
            row.setdefault("stage", "production")
            row.setdefault("timestamp", "")
            result.append(row)
    return result


def _normalise_energy_header(header: str) -> str | None:
    cleaned = header.strip().lstrip("\ufeff#").strip().strip('"')
    key = " ".join(cleaned.lower().split())
    aliases = {
        "step": "step",
        "time (ps)": "time_ps",
        "potential energy (kj/mole)": "potential_energy",
        "potential energy (kj/mol)": "potential_energy",
        "kinetic energy (kj/mole)": "kinetic_energy",
        "kinetic energy (kj/mol)": "kinetic_energy",
        "total energy (kj/mole)": "total_energy",
        "total energy (kj/mol)": "total_energy",
        "temperature (k)": "temperature",
        "box volume (nm^3)": "volume",
        "volume (nm^3)": "volume",
        "density (g/ml)": "density",
        "speed (ns/day)": "speed",
        "progress (%)": "progress_percent",
    }
    return aliases.get(key)


def read_events(project_root: str | Path, *, limit: int = 100) -> list[dict[str, str]]:
    path = _simulation_dir(project_root) / EVENTS_FILE
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    events: list[dict[str, str]] = []
    for line in lines[-limit:]:
        parts = line.split("\t", 2)
        if len(parts) == 3:
            events.append({"timestamp": parts[0], "level": parts[1], "message": parts[2]})
        else:
            events.append({"timestamp": "", "level": "info", "message": line})
    return events


#: Statuses a run writes when it stops. `completed` is what the orchestrator
#: records at the end; the rest are accepted so an older run, or one written
#: by something else, is not described as still going.
_FINISHED_STATUSES = frozenset({"completed", "complete", "finished", "done", "ok"})

COMPLETED_EXPLANATION = (
    "The run finished, and the last sample it wrote was within normal "
    "ranges. Nothing here is being recorded any more."
)


def analyze_health(
    status: dict[str, Any],
    metrics: list[dict[str, Any]],
    *,
    stale_after_seconds: float = 90.0,
    root: str | Path | None = None,
) -> dict[str, str]:
    """Classify the latest telemetry into ok/warning/failed with plain text.

    With `root`, a folder with no telemetry is described by what it holds:
    runs one level down, nothing at all, or a run that recorded none.
    """
    latest_error = status.get("latest_error")
    if str(status.get("status", "")).lower() == "stopped":
        # Asked to stop, not failed: its message says where it stopped.
        return {
            "state": "stopped",
            "headline": "Stopped",
            "message": str(latest_error or "The run was stopped."),
            "explanation": STOPPED_EXPLANATION,
        }
    if latest_error or str(status.get("status", "")).lower() == "failed":
        # The explanation used to be NUMERIC_EXPLANATION for every failure,
        # so "setup outputs are missing" arrived with a paragraph about
        # timesteps and clashes attached -- two reasons on screen, one of
        # them about a simulation that never took a step. The message
        # already says what happened. A second sentence is offered only
        # when the failure is the kind it describes.
        said = str(latest_error or "Simulation failed.")
        numeric = any(word in said.lower() for word in (
            "nan", "inf", "unstable", "blew up", "exploded", "particle"
            " coordinate", "energy is"))
        # A headline a card can set large, beside the message, which can run
        # to a paragraph with a path in it and was set as the headline.
        return {
            "state": "failed",
            "headline": "Stopped with an error",
            "message": said,
            "explanation": NUMERIC_EXPLANATION if numeric else "",
        }

    latest = metrics[-1] if metrics else {}
    for field_name in NUMERIC_METRIC_FIELDS:
        value = _safe_float(latest.get(field_name))
        if value is not None and not math.isfinite(value):
            return {
                "state": "failed",
                "message": f"{field_name} is NaN or infinite.",
                "explanation": NUMERIC_EXPLANATION,
            }

    energy_state = _detect_energy_spike(metrics)
    if energy_state is not None:
        return energy_state

    temp = _safe_float(latest.get("temperature"))
    target = _safe_float(status.get("target_temperature_K"))
    if temp is not None:
        if not math.isfinite(temp):
            return {
                "state": "failed",
                "message": "Temperature is NaN or infinite.",
                "explanation": NUMERIC_EXPLANATION,
            }
        if target is not None and abs(temp - target) > max(50.0, target * 0.25):
            return {
                "state": "warning",
                "message": f"Temperature {temp:.1f} K is far from target {target:.1f} K.",
                "explanation": TEMPERATURE_EXPLANATION,
            }
        if target is None and temp > 450.0:
            return {
                "state": "warning",
                "message": f"Temperature is high ({temp:.1f} K).",
                "explanation": TEMPERATURE_EXPLANATION,
            }

    timestamp = status.get("last_update_timestamp")
    if str(status.get("status", "")).lower() == "running" and timestamp:
        age = _timestamp_age_seconds(str(timestamp))
        if age is not None and age > stale_after_seconds:
            return {
                "state": "warning",
                "message": "Telemetry is stale.",
                "explanation": STALE_EXPLANATION,
            }

    if str(status.get("status", "")).lower() == "interrupted":
        silent = status.get("ended_by") == "silence"
        return {
            "state": "interrupted",
            "headline": "Interrupted",
            "message": ("The run has not written a word for over a day."
                        if silent else "The run ended without recording why."),
            "explanation": SILENT_EXPLANATION if silent else INTERRUPTED_EXPLANATION,
        }

    if str(status.get("status", "")).lower() in _FINISHED_STATUSES:
        # A run that has stopped is not progressing. Every check above still
        # applies -- they read the last sample it wrote -- but the verdict is
        # about a run that ended, and the present tense made a page opened
        # hours later read as though the simulation were still going.
        return {
            "state": "ok",
            "message": "Completed",
            "explanation": COMPLETED_EXPLANATION,
        }

    if status:
        return {"state": "ok", "message": "Normal progress", "explanation": NORMAL_EXPLANATION}
    not_a_run = _not_a_run(root) if root is not None else None
    if not_a_run is not None:
        return not_a_run
    return {
        "state": "unknown",
        "message": "Live telemetry is not available.",
        "explanation": (
            "Live telemetry is on by default, so this run either turned it "
            "off with `live_telemetry: false` under `simulation`, or predates "
            "the setting. Either way there is nothing on disk to read, and "
            "the page cannot fill. A new run records it without being asked."
        ),
    }


def _not_a_run(root: str | Path) -> dict[str, str] | None:
    """What a folder with no telemetry is, when it is not a run at all.

    A folder holding several runs was described as a run that had turned
    its telemetry off, and so was an empty one opened to start a study:
    both were told about a setting when the answer was where they were.
    """
    root = Path(root)
    if (root / "manifest.json").is_file() or (root / "simulation").is_dir():
        return None
    try:
        runs = sorted(child.name for child in root.iterdir()
                      if child.is_dir() and (child / "manifest.json").is_file())
    except OSError:
        return None
    if runs:
        named = ", ".join(runs[:6]) + (f" and {len(runs) - 6} more" if len(runs) > 6 else "")
        return {
            "state": "unknown",
            "message": f"This folder holds {len(runs)} run{'s' if len(runs) != 1 else ''} "
                       "rather than being one.",
            "explanation": (
                f"The runs are one level down: {named}. Open one of them to see "
                "its progress and results. Runs open together as one study only "
                "when they were run as one, which leaves a batch_manifest.json "
                "at the top of the folder."
            ),
        }
    return {
        "state": "unknown",
        "message": "Nothing has run in this folder yet.",
        "explanation": (
            "There is no run here: no manifest and no simulation. Start a "
            "study in it, or open a folder that holds a run."
        ),
    }


def _detect_energy_spike(metrics: list[dict[str, Any]]) -> dict[str, str] | None:
    if len(metrics) < 2:
        return None
    field_name = "total_energy"
    prev = _safe_float(metrics[-2].get(field_name))
    current = _safe_float(metrics[-1].get(field_name))
    if prev is None or current is None:
        field_name = "potential_energy"
        prev = _safe_float(metrics[-2].get(field_name))
        current = _safe_float(metrics[-1].get(field_name))
    if prev is None or current is None:
        return None
    if not (math.isfinite(prev) and math.isfinite(current)):
        return {
            "state": "failed",
            "message": f"{field_name} is NaN or infinite.",
            "explanation": NUMERIC_EXPLANATION,
        }
    delta = abs(current - prev)
    threshold = max(10000.0, abs(prev) * 5.0)
    if delta > threshold:
        return {
            "state": "warning",
            "message": f"{field_name} changed sharply ({prev:.3g} to {current:.3g}).",
            "explanation": ENERGY_EXPLANATION,
        }
    return None


def _timestamp_age_seconds(value: str) -> float | None:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds()
