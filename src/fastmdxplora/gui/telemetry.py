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


def read_metrics(project_root: str | Path, *, limit: int = 500) -> list[dict[str, Any]]:
    """Read dashboard metrics, enriching them from OpenMM's ``energy.csv``.

    Older runs and very short smoke tests may have only one of the two files.
    The live CSV has stable FastMDX field names, while OpenMM's reporter uses
    human-readable headings that vary slightly by version.  Normalising and
    merging both sources keeps the dashboard useful for live and completed
    runs without changing the scientific output files.
    """

    simulation_dir = _simulation_dir(project_root)
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

    # The frame count is status-level information in older telemetry schemas.
    # Attach it to the latest sample so overview metric cards can still render.
    if rows:
        status = read_status(project_root)
        frame_count = status.get("current_frame_count")
        if frame_count is not None and rows[-1].get("current_frame_count") in (None, ""):
            rows[-1]["current_frame_count"] = str(frame_count)
    return rows[-limit:]


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
    "ranges. Nothing here is being measured any more."
)


def analyze_health(
    status: dict[str, Any],
    metrics: list[dict[str, Any]],
    *,
    stale_after_seconds: float = 90.0,
) -> dict[str, str]:
    """Classify the latest telemetry into ok/warning/failed with plain text."""
    latest_error = status.get("latest_error")
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
        return {
            "state": "failed",
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
