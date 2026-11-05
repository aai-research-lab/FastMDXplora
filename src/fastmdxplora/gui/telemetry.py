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

from fastmdxplora import record_access as _records

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
#: What would bring a temperature back into range, said apart so a list of
#: problems can give it as a remedy and the rest as a description.
TEMPERATURE_REMEDY = (
    "Consider a smaller timestep, stronger friction, gentler heating, or checking "
    "the input structure."
)
TEMPERATURE_EXPLANATION = "Temperature is outside the expected range. " + TEMPERATURE_REMEDY
INTERRUPTED_EXPLANATION = (
    "Its last update said it was still going, and the process that ran it is "
    "gone: the machine restarted, or its job was ended by a scheduler or by "
    "hand. What it wrote up to then is kept, and the command fastmdx resume "
    "says whether it can be carried on from its last checkpoint."
)

ANALYSIS_OWED_EXPLANATION = (
    "A study carried on in pieces joins them, then runs its analyses and "
    "report again over the join. It was stopped between the two (a wall time "
    "lands where it lands), so any analyses and report it has are those of "
    "before the join. fastmdx resume runs them over the join, and nothing else."
)

#: The same, where the study was asked to stop in those analyses or report.
ANALYSIS_OWED_STOPPED_EXPLANATION = (
    "A study carried on in pieces joins them, then runs its analyses and "
    "report again over the join. It was asked to stop (Ctrl+C, or Stop) while "
    "they ran, so what it has of them is not whole. fastmdx resume runs them "
    "over the join, and nothing else."
)

STOPPED_EXPLANATION = (
    "It was asked to stop (Ctrl+C, or Stop) and ended where it can be "
    "carried on. What it wrote up to then is kept, and fastmdx resume "
    "carries it on from there."
)
#: Stopped while the trajectory was analysed or the report written: the
#: simulation is kept whole, and nothing is carried on.
STOPPED_IN_A_PHASE_AFTER_EXPLANATION = (
    "It was asked to stop while its trajectory was analysed or its report "
    "written. The simulation is finished and kept; Analyze again or Write it "
    "again runs that part once more."
)
#: Stopped before production, where no checkpoint is written: said beside a
#: message that says so, it read "ended where it can be carried on".
STOPPED_BEFORE_PRODUCTION_EXPLANATION = (
    "It was asked to stop (Ctrl+C, or Stop) before its production began. "
    "What it wrote up to then is kept, and fastmdx resume runs it again from "
    "the start."
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
    #: For a study analysed or reported again: the record is its
    #: simulation's, so only the analysis and report stages, the status and
    #: the error are written into it; its stages, times and date stay the
    #: run's. With no writer at all the page said "Completed ... nothing is
    #: being recorded any more" while the analysis ran, beside Stop.
    phases_only: bool = False
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
        if self.phases_only:
            self._write_phases(updates)
            return
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            now = datetime.now(timezone.utc)
            persisted = _own_status(self.status_path)
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
            # A new temporary file of its own, renamed into place: a link at
            # the record, or planted at a temporary name, is never followed.
            _records.save(self.status_path, json.dumps(status, indent=2, default=str))
        except Exception:
            return

    def _write_phases(self, updates: dict[str, Any]) -> None:
        """Only the analysis and report stages, the status and the error,
        over the simulation's record as it stands."""
        try:
            status = _own_status(self.status_path)
            if not status:
                return
            if not self._last_status:
                # How the run had ended, said again once these phases end
                # well: a failed run analysed again is still a failed run.
                # Kept in the record (`ended_as`) until then, so a rerun
                # stopped or failed, and run again, still knows it.
                ended = status.get("ended_as")
                if isinstance(ended, dict):
                    self._last_status = dict(ended)
                elif str(status.get("status") or "").lower() in _GOING_STATUSES:
                    # A run stopped in its own analysis or report: the
                    # record says it is going, which is not how it ended.
                    # Its simulation had finished, so these phases ending
                    # well end the study well (it read "Running" for good).
                    self._last_status = {"status": "completed", "latest_error": None}
                else:
                    self._last_status = {
                        "status": status.get("status"),
                        "latest_error": status.get("latest_error"),
                        "stage": status.get("stage")}
                status["ended_as"] = dict(self._last_status)
            if updates.get("status") == "completed":
                updates = {**updates, **self._last_status}
                status.pop("ended_as", None)
            stage = str(updates.get("stage") or "").lower()
            if stage and (stage in _PHASES_AFTER
                          or stage == str(self._last_status.get("stage") or "")):
                status["stage"] = updates.get("stage")
            states = dict(status.get("stage_states") or {})
            for name, state in (updates.get("stage_states") or {}).items():
                if name in _PHASES_AFTER:
                    states[name] = str(state).lower()
            status["stage_states"] = states
            if updates.get("status"):
                status["status"] = updates["status"]
            if "latest_error" in updates:
                status["latest_error"] = updates["latest_error"]
            status["phases_updated_at"] = _utc_now()
            # A new temporary file of its own, renamed into place: a link at
            # the record, or planted at a temporary name, is never followed.
            _records.save(self.status_path, json.dumps(status, indent=2, default=str))
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
        persisted = _own_status(self.status_path)
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
            # Never through a link (`record_access.open_to_append`).
            with _records.open_to_append(self.metrics_path, newline="") as fh:
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
            with _records.open_to_append(self.events_path) as fh:
                fh.write(f"{_utc_now()}\t{level}\t{message}\n")
        except Exception:
            return


#: The phases run again over a simulation's record (`phases_only`).
_PHASES_AFTER = ("analysis", "report")


def phases_after_ended_well(project_root: str | Path) -> None:
    """The study's record once its analyses and report have run again to
    their end where no writer of the GUI's followed them (`fastmdx resume`
    from the command line): a record stopped, failed or going in them is
    said as Analyze again leaves it, the run as it had ended before them
    (`ended_as`), completed where that is not said or was a stop. It read
    "Stopped here in the analysis" after a resume that ran them whole."""
    root = _simulation_dir(project_root)
    status = _own_status(root / STATUS_FILE)
    stage = str(status.get("stage") or "").lower()
    state = str(status.get("status") or "").lower()
    if (stage == "production" and state == "stopped" and _its_production_whole(root)):
        # Stopped at the last step of its production, whole by the join's
        # reading: its analyses and report run since, it read stopped for
        # good after the resume that ran them.
        stage = "report"
    if (stage not in _PHASES_AFTER
            or state not in {"stopped", "failed", "interrupted", *_GOING_STATUSES}):
        return
    ended = status.get("ended_as")
    before = (dict(ended) if isinstance(ended, dict)
              and str(ended.get("status") or "").lower() not in
              {"stopped", "interrupted", *_GOING_STATUSES}
              and str(ended.get("stage") or "").lower() not in _PHASES_AFTER else
              {"status": "completed", "latest_error": None, "stage": "report"})
    writer = TelemetryWriter(root, phases_only=True)
    writer._last_status = before
    writer.write_status(status="completed", stage="report",
                        stage_states={"analysis": "completed", "report": "completed"})


def _its_production_whole(simulation: Path) -> bool:
    """Whether a run's own production ran to its end (`joining.piece_finished`,
    the one reading of a stretch of production's end)."""
    from fastmdxplora.analysis.joining import piece_finished

    try:
        return piece_finished(simulation)
    except Exception:  # noqa: BLE001 - a record, not a verdict
        return False


def _read_status_path(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(_records.read_text(path, encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _own_status(path: Path) -> dict[str, Any]:
    """The status record a writer merges what it writes into, read never
    through a link (`record_access.read_own_text`): a record linked out of
    the study is read as no record, and nothing it says is merged into
    the study's own."""
    try:
        data = json.loads(_records.read_own_text(path, encoding="utf-8"))
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
        data = json.loads(_records.read_text(path, encoding="utf-8"))
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
    """The study's segments that kept a live record, in the order run. A
    segment whose live record a held read refused is one of them: it was
    not read, which is not a piece that kept none."""
    from fastmdxplora.simulation.pipeline import _refused

    found: list[tuple[int, Path]] = []
    for folder in _records.glob(root, "segment-*"):
        try:
            index = int(folder.name.split("-", 1)[1])
        except (IndexError, ValueError):
            continue
        record = folder / "simulation" / STATUS_FILE
        if index > 0 and (_records.is_file(record) or _refused(record)):
            found.append((index, folder))
    return [folder for _, folder in sorted(found)]


def live_records_refused(root: Path) -> list[Path]:
    """The study's live records a held read refused (`record_access`): its
    own, and those of the pieces it was carried on in. Where one is, the
    study's status is not read, since it is read as one run across them
    all: no piece's word stands in for the study's. None outside a held
    read."""
    from fastmdxplora.simulation.pipeline import _refused

    root = Path(root)
    if root.name == "simulation":
        root = root.parent
    try:
        pieces = _pieces_of(root)
    except OSError:
        pieces = []
    return [record for record in (folder / "simulation" / STATUS_FILE
                                  for folder in (root, *pieces))
            if _refused(record)]


def _frames_kept(folder: Path) -> int | None:
    """The production frames a piece contributes to the joined trajectory:
    all it wrote where it finished, those before its last checkpoint where
    it was stopped (the rest are run again by the next piece)."""
    from fastmdxplora.simulation.resume import frames_before_checkpoint

    simulation = folder / "simulation"
    try:
        # A clean finish (its final state written) says its frames; any other
        # end, a stop at its last step included, is counted from its
        # checkpoint, which there is all of them (a live count left stale).
        if _records.is_file(simulation / "state_final.xml"):
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


def _phases_since(root: Path, record: dict[str, Any]) -> dict[str, str]:
    """The study's analysis and report as its Manifest says them, where the
    Manifest was written after ``record``: completed, failed or skipped."""
    written = _parse_iso_datetime(str(record.get("last_update_timestamp") or ""))
    manifest = root / "manifest.json"
    try:
        if written is not None and _records.stat(manifest).st_mtime < written.timestamp():
            return {}
        phases = json.loads(_records.read_text(manifest, encoding="utf-8")).get("phases") or []
    except (OSError, ValueError, AttributeError):
        return {}
    said = {"ok": "completed", "error": "failed", "skipped": "skipped"}
    return {str(p.get("name")): said.get(str(p.get("status")), "waiting")
            for p in phases if isinstance(p, dict) and p.get("name") in ("analysis", "report")}


def _newer(one: dict[str, Any], other: dict[str, Any]) -> bool:
    first = _parse_iso_datetime(str(one.get("last_update_timestamp") or ""))
    second = _parse_iso_datetime(str(other.get("last_update_timestamp") or ""))
    if first is None or second is None:
        return first is not None
    return first > second


def _as_it_ended(piece: Path, status: dict[str, Any]) -> dict[str, Any]:
    """A piece's live record, said completed where its production reached
    its end (`joining.piece_finished`, the join's reading) and the join has
    taken it in, its record saying it was stopped (the runner stops there,
    or said it not finished before): the run read stopped for good after a
    resume that completed it. Before its join the run is as its round left
    it: stopped, its production whole, its join owed. So is a piece that
    finished as planned and was never joined (its study stopped or cut
    between the two, `_its_join_owed`): it read completed, while the Agent
    and the round's sentence said its join owed."""
    if str(status.get("status") or "").lower() in _FINISHED_STATUSES:
        return _its_join_owed(piece, status)
    if str(status.get("status") or "").lower() != "stopped":
        return status
    from fastmdxplora.analysis.joining import piece_finished

    try:
        index = int(piece.name.split("-", 1)[1])
        joined = json.loads(_records.read_text(piece.parent / "joined" / "joined.json",
                                               encoding="utf-8"))
        if (index not in {int(i) for i in joined.get("segments") or []}
                or not piece_finished(piece / "simulation")):
            return status
    except Exception:  # noqa: BLE001 - a record, not a verdict
        return status
    states = dict(status.get("stage_states") or {}) if isinstance(
        status.get("stage_states"), dict) else {}
    states["production"] = "completed"
    total = status.get("total_planned_steps")
    return {**status, "status": "completed", "latest_error": None, "stage_states": states,
            **({"current_step": total} if isinstance(total, (int, float)) else {})}


def _its_join_owed(piece: Path, status: dict[str, Any]) -> dict[str, Any]:
    """A piece's live record that says it finished, read as its study left
    it where the join has not taken it in (`joined/joined.json`) and nothing
    carries the study on now: its production whole, its join and the
    analyses of it owed, which `fastmdx resume` runs
    (`resume.how_a_resume_finds`, ``unjoined``). Stopped where the study's
    round was stopped when asked; else its process ended before the join
    (killed, out of memory): interrupted, never "stopped, as asked". Only a
    piece an extension made is joined by it, told by its own config (it
    resumes from a checkpoint) or the config written beside it; a piece
    with neither (a record written by hand) reads as it says."""
    from fastmdxplora.simulation.resume import being_carried_on

    try:
        if not _an_extension_s_piece(piece):
            return status
        index = int(piece.name.split("-", 1)[1])
        try:
            joined = json.loads(_records.read_text(piece.parent / "joined" / "joined.json",
                                                   encoding="utf-8"))
            taken = {int(i) for i in joined.get("segments") or []}
        except (OSError, ValueError):
            taken = set()
        if index in taken or being_carried_on(piece.parent):
            return status
        from fastmdxplora.simulation.stopping import round_failed

        # The study's round first: a join that failed left the piece, and
        # the study's process ended on that error, not before the join.
        failed = round_failed(_study_of(piece.parent))
        stopped = not failed and _its_round_was_stopped(piece.parent, piece)
    except Exception:  # noqa: BLE001 - a record, not a verdict
        return status
    if failed:
        # Its production whole, its join failed: the status bar says the
        # join failed ("Joining failed"), not its production.
        return {**status, "status": "failed", "join_owed": True, "stage": "joining",
                "latest_error": (f"{failed} Its piece {piece.name} ran to its end and was not "
                                 f"joined: {_resume_said(piece.parent)} joins it again and "
                                 "runs its analyses and report.")}
    how = ("the study was stopped when asked" if stopped
           else "the study's process ended")
    return {**status, "status": "stopped" if stopped else "interrupted",
            "join_owed": True,
            "latest_error": (f"Its piece {piece.name} ran to its end, and {how} before the "
                             f"piece was joined: {_resume_said(piece.parent)} "
                             "joins it and runs its analyses and report.")}


def _an_extension_s_piece(piece: Path) -> bool:
    """Whether a piece was made by an extension (or a resume), which joins
    it: its own config resumes from a checkpoint, or its config is written
    beside it (`segment-NNN.yml`)."""
    if _records.is_file(piece.with_name(piece.name + ".yml")):
        return True
    import yaml

    try:
        config = yaml.safe_load(_records.read_text(piece / "resolved_config.yml",
                                                   encoding="utf-8")) or {}
    except (OSError, ValueError, yaml.YAMLError):
        return False
    simulation = config.get("simulation") if isinstance(config, dict) else None
    return isinstance(simulation, dict) and bool(simulation.get("resume_from"))


def _study_of(run: Path) -> Path:
    """The study a run belongs to: its own folder, or the study of runs it
    is one of (``runs/<name>``)."""
    return run.parent.parent if run.parent.name == "runs" else run


def _its_round_was_stopped(run: Path, piece: Path) -> bool:
    """Whether the study's rule's round ended stopped when asked, with no
    resume since that died carrying it on (`stopping.round_stopped_when_asked`,
    from its record of its rounds alone: never from when files were written,
    nor from records of the study a run's page does not read)."""
    from fastmdxplora.simulation.stopping import round_stopped_when_asked

    return round_stopped_when_asked(_study_of(run))


def _as_one_run(root: Path, first: dict[str, Any]) -> dict[str, Any]:
    pieces = _pieces_of(root)
    if not pieces:
        return first
    newest = pieces[-1]
    last = _as_it_ended(newest, read_status(newest))
    if not last:
        return first
    whole = dict(first)
    whole["piece"] = newest.name
    whole["pieces"] = len(pieces) + 1
    # What the simulation last said of itself is its newest piece's word.
    for key in ("latest_error", "latest_warning", "current_checkpoint_path", "platform",
                "precision", "precision_applied", "join_owed"):
        if key in last:
            whole[key] = last.get(key)
    if _newer(last, first):
        # The piece is the newest word: the study is where it is.
        stage = str(last.get("stage") or "").lower()
        whole["status"] = last.get("status")
        # A piece only produces, whatever stage it was first marked in; one
        # whose join failed is said at its join (`_its_join_owed`).
        whole["stage"] = stage if stage in {"analysis", "report", "joining"} else "production"
        whole["last_update_timestamp"] = last.get("last_update_timestamp")
        states = dict(first.get("stage_states") or {}) if isinstance(
            first.get("stage_states"), dict) else {}
        said = str(last.get("status") or "").lower()
        states["production"] = ("current" if said in _GOING_STATUSES
                                else str((last.get("stage_states") or {}).get(
                                    "production") or said or "waiting"))
        # The phases after it are the study's own, run over the joined
        # trajectory once the piece ended: as its Manifest says them, where
        # it was written since; else still to come.
        after = _phases_since(root, last) if said == "completed" else {}
        # The phases the study asks for: the first piece's record can say
        # them skipped (it stopped before them), and the sidebar counted
        # "Stage 5 of 5" through the joined trajectory's analysis.
        asked = phases_asked(root)
        for later in ("analysis", "report"):
            if asked and later not in asked:
                continue
            if not asked and states.get(later) in (None, "skipped"):
                continue
            states[later] = after.get(later, "waiting")
        if after and all(after.get(name) == "completed" for name in after):
            whole["stage"] = list(after)[-1]
        # The joined trajectory analysed and reported as the study's own,
        # marked in its record since the piece last wrote: while they run
        # the study is running (it read "Completed" with its analysis going).
        phases_at = _parse_iso_datetime(first.get("phases_updated_at"))
        piece_at = _parse_iso_datetime(last.get("last_update_timestamp"))
        if phases_at and (piece_at is None or phases_at > piece_at):
            marked = first.get("stage_states") if isinstance(
                first.get("stage_states"), dict) else {}
            for later in ("analysis", "report"):
                if str(marked.get(later) or "skipped").lower() != "skipped":
                    states[later] = str(marked[later]).lower()
            first_said = str(first.get("status") or "").lower()
            first_stage = str(first.get("stage") or "").lower()
            if first_said in _GOING_STATUSES:
                whole["status"] = first.get("status")
                if first_stage in ("analysis", "report"):
                    whole["stage"] = first_stage
            elif first_said in ("stopped", "failed") and first_stage in ("analysis", "report"):
                # The joined trajectory's analysis or report stopped or
                # failed since the piece ended: the study is where they
                # left it (a stop there read "Completed").
                whole["status"] = first.get("status")
                whole["stage"] = first_stage
                whole["latest_error"] = first.get("latest_error")
            elif all(states.get(name) in ("completed", "skipped")
                     for name in ("analysis", "report")):
                whole["stage"] = "report" if states.get("report") == "completed" else "analysis"
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
    if (str(whole.get("status") or "").lower() in {"stopped", "interrupted"}
            and str(whole.get("stage") or "").lower() == "production"):
        # Stopped again while carried on: counted to the checkpoint the
        # piece wrote, as a run of one piece is (the record lags it).
        try:
            from fastmdxplora.simulation.runner import read_checkpoint_sidecar

            side = read_checkpoint_sidecar(newest / "simulation" / "checkpoint.chk") or {}
        except Exception:  # noqa: BLE001 - a record, not a verdict
            side = {}
        step, interval = side.get("step"), side.get("trajectory_interval_steps")
        if (side.get("stage") == "production" and isinstance(step, (int, float))
                and isinstance(interval, (int, float)) and interval > 0):
            current = whole.get("current_step")
            if not isinstance(current, (int, float)) or steps_before + step > current:
                whole["current_step"] = int(steps_before + step)
            frames = whole.get("current_frame_count")
            if not isinstance(frames, (int, float)) or frames_before + int(step) // int(interval) > frames:
                whole["current_frame_count"] = int(frames_before + int(step) // int(interval))
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
    if live_records_refused(root):
        # A piece's live record not read: the study's status is not read,
        # not its first piece's (a study carried on to the end read as
        # stopped with its first piece's frames).
        return {}
    try:
        whole = _as_one_run(root, first)
    except Exception:  # noqa: BLE001 - a record, not a verdict
        whole = dict(first)
    if "current_checkpoint_path" in whole:
        whole["current_checkpoint_path"] = _checkpoint_here(root, whole)
    if not whole.get("piece"):
        whole = _as_far_as_it_wrote(root, whole)
    elif (str(whole.get("status") or "").lower() == "stopped" and not whole.get("join_owed")
          and str(whole.get("stage") or "").lower() == "production"):
        # A piece stopped part way, said as the study; one run to its end
        # and not joined keeps its own words (`_its_join_owed`).
        whole["latest_error"] = _stopped_as_one_run(root, whole)
    return _the_stage_it_ended_in(whole)


#: The simulation's stages in the order a run takes them.
_RUN_STAGES = ("minimization", "nvt", "npt", "production")


def _the_stage_it_ended_in(status: dict[str, Any]) -> dict[str, Any]:
    """A run that failed in NVT is recorded at the stage "production", every
    stage after NVT marked failed with it: the sidebar said "Production
    failed" beside "unstable during NVT". The stage is the first that did
    not complete."""
    if str(status.get("status") or "").lower() not in ("failed", "stopped", "interrupted"):
        return status
    stage = str(status.get("stage") or "").lower()
    states = status.get("stage_states")
    if stage not in _RUN_STAGES or not isinstance(states, dict):
        return status
    for name in _RUN_STAGES[:_RUN_STAGES.index(stage)]:
        state = str(states.get(name) or "").lower()
        if state in ("failed", "stopped", "interrupted"):
            return {**status, "stage": name}
        if state not in ("completed", "skipped"):
            break
    return status


def _stopped_as_one_run(root: Path, whole: dict[str, Any]) -> str | None:
    """A study stopped again while carried on, said as the study: the
    piece's own words gave its own steps ("step 3,800 of 0.015 ns") and
    `fastmdx resume <study>/segment-001`, which would carry the piece on as a
    study of its own, beside the study's figures and command."""
    from fastmdxplora.gui.simulated_time import say_length

    # Production's own steps and time, as the first stop said them: the
    # whole run's clock counted the equilibration as production.
    equilibration = sum(int(whole.get(key) or 0)
                        for key in ("nvt_steps_planned", "npt_steps_planned"))
    step, total = whole.get("current_step"), whole.get("production_steps_planned")
    timestep = whole.get("timestep_fs")
    where = ""
    if (isinstance(step, (int, float)) and isinstance(total, (int, float))
            and isinstance(timestep, (int, float)) and total > 0):
        produced = max(int(step) - equilibration, 0)
        where = (f" at step {produced:,} of {int(total):,} "
                 f"({say_length(produced * timestep / 1e6)} of "
                 f"{say_length(total * timestep / 1e6)})")
    return (f"Production was stopped{where}, as asked, while the study was carried on. "
            f"{_resume_said(root)} carries it on from its last checkpoint.")


def _resume_said(root: str | Path | None) -> str:
    """`fastmdx resume` for a study, in a record `/api/status` answers
    beyond loopback: the study named by its folder, never by where it is
    on this computer. The fix card gives the whole command."""
    if root is None:
        return "fastmdx resume"
    # A run of a study of several is carried on by its study's resume.
    return f"fastmdx resume, given this study ({_study_of(Path(root)).name}),"


def _as_far_as_it_wrote(root: Path, status: dict[str, Any]) -> dict[str, Any]:
    """A run stopped in production, counted to the checkpoint it wrote.

    Asked to stop, a run steps on to its next frame and writes its
    checkpoint there; its record was last written before that, so the page
    said 5.2 ps and 26 frames of a run whose trajectory held 28 (5.6 ps).
    """
    if (str(status.get("status") or "").lower() not in {"stopped", "interrupted"}
            or str(status.get("stage") or "").lower() != "production"):
        return status
    try:
        from fastmdxplora.simulation.runner import read_checkpoint_sidecar

        side = read_checkpoint_sidecar(root / "simulation" / "checkpoint.chk") or {}
    except Exception:  # noqa: BLE001 - a record, not a verdict
        return status
    step, interval = side.get("step"), side.get("trajectory_interval_steps")
    if (side.get("stage") != "production" or not isinstance(step, (int, float))
            or not isinstance(interval, (int, float)) or interval <= 0):
        return status
    equilibration = sum(int(status.get(key) or 0)
                        for key in ("nvt_steps_planned", "npt_steps_planned"))
    said = dict(status)
    current = status.get("current_step")
    if isinstance(current, (int, float)) and equilibration + step > current:
        said["current_step"] = int(equilibration + step)
        timestep = status.get("timestep_fs")
        if isinstance(timestep, (int, float)):
            said["simulation_time_completed_ns"] = said["current_step"] * float(timestep) / 1e6
    frames = status.get("current_frame_count")
    if isinstance(frames, (int, float)) and int(step) // int(interval) > frames:
        said["current_frame_count"] = int(step) // int(interval)
    return said


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
        if (found == root_resolved or root_resolved in found.parents) and _records.is_file(found):
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


def _began_since(pid: int, record: dict[str, Any]) -> bool:
    """Whether the process ``pid`` began more than a few seconds after the
    process record was written: another process given the number, never the
    run that wrote it. False where either time is not known. Asked only of
    a record without the run's start in ticks since boot, and not of a
    process whose command line carries the arguments the record has
    (`exploration._what_the_process_is`): a time of day read this way moves
    when the clock is stepped, and a live run would read as begun since."""
    began = _process_started_at(pid)
    written = _parse_iso_datetime(record.get("started_at"))
    return began is not None and written is not None and began > written.timestamp() + 5


def _what_its_process_says(root: Path, record: dict[str, Any]) -> str | None:
    """"gone" where the record's process has certainly ended, "running"
    where it is this study's run, None where it cannot be told here (and
    the record's silence decides).

    Asked only where its number means the same process here: the same host,
    the same boot and, where both say, the same process namespace (a
    container sharing the host's name and boot numbers its processes on its
    own). The same host restarted since is certainly gone. Whether the
    process is the run is the one test every reader asks
    (`exploration._what_the_process_is`): its start in ticks since boot
    where the record has it, else its command line, and for a record with
    no arguments when it began. A process alive whose command line is not
    a run's is not taken for gone: a run started from a script, a notebook
    or a pool's worker has such a command line.
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
    from fastmdxplora.gui.exploration import _what_the_process_is

    # The process asking, or what started it: its command line is not read.
    asking = pid in _this_process_and_its_parents()
    key = (str(root), pid, str(record.get("started_at") or ""))
    now = datetime.now(timezone.utc).timestamp()
    kept = None if asking else _ASKED.get(key)
    if kept and now - kept[0] < _ASKED_FOR_SECONDS:
        return kept[1]
    try:
        found = _what_the_process_is(pid, root, record, asking=asking)
    except Exception:  # noqa: BLE001 - a record, not a verdict
        found = None
    said = ("running" if found == "the run"
            else "gone" if found in ("gone", "another") else None)
    if not asking:
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
            and _records.is_dir(root / piece)):
        # The piece being run keeps the record of its process.
        root = root / piece
    try:
        record = json.loads(_records.read_text(root / RUN_PROCESS_FILE, encoding="utf-8"))
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


def status_as_it_stands(project_root: str | Path, *,
                       include_campaign: bool = True) -> dict[str, Any]:
    """The run's record as the GUI shows it: as written, except for a run
    that ended without saying so, and a run of a study of several waiting
    for its study's next round (`between_rounds`, said `waiting`). One that had taken every step it planned
    in a stage of the simulation finished it (the Python API's `simulate`
    writes no end of its own); any other is `interrupted`, with how that is
    known as `ended_by`. What the record said is kept as `recorded_status`.
    For the GUI's own process only; a run reads its record with
    `read_status`. Set ``include_campaign=False`` to read only this run,
    without consulting its parent campaign or sibling runs."""
    status = read_study_status(project_root)
    ended = how_the_run_ended(project_root, status) if status else None
    if ended is None:
        waiting = between_rounds(project_root, status) if status and include_campaign else None
        if waiting is None:
            return _not_yet_said_whole(project_root, _owing_its_analysis(project_root, status))
        return _waiting_for_the_round(status, waiting)
    step, total = status.get("current_step"), status.get("total_planned_steps")
    finished = (str(status.get("stage") or "").lower() in _STEPPED_STAGES
                and isinstance(step, (int, float)) and isinstance(total, (int, float))
                and 0 < total <= step)
    return _not_yet_said_whole(project_root, _owing_its_analysis(project_root, {
        **status, "status": "completed" if finished else "interrupted",
        "ended_by": ended, "recorded_status": status.get("status")}))


def _not_yet_said_whole(project_root: str | Path, status: dict[str, Any]) -> dict[str, Any]:
    """A run said completed whose Manifest still records its simulation
    stopped when asked at its last step (`resume.stopped_at_its_end`, not
    yet said whole): a resume says it whole last, after its analyses and
    its rule (`resume._said_whole_last`), so one ended before that is read
    stopped, its stop said, and `fastmdx resume` finishes it. It read
    completed with its rule never applied and no fix offered."""
    if str((status or {}).get("status") or "").lower() not in _FINISHED_STATUSES:
        return status
    from fastmdxplora.simulation.resume import being_carried_on, stopped_at_its_end

    try:
        manifest = json.loads(_records.read_text(Path(project_root) / "manifest.json",
                                                 encoding="utf-8"))
    except (OSError, ValueError):
        return status
    phases = manifest.get("phases") if isinstance(manifest, dict) else None
    stop = next((phase for phase in phases or [] if stopped_at_its_end(project_root, phase)),
                None)
    if stop is None or being_carried_on(project_root):
        return status
    return {**status, "status": "stopped", "stage": "production",
            "latest_error": str(stop.get("message") or "") or status.get("latest_error"),
            "recorded_status": status.get("recorded_status") or status.get("status")}


#: ``ended_by`` for a study stopped after its pieces were joined, before
#: their analyses and report had run (`resume.analysis_owed`).
ANALYSIS_OWED = "analysis_owed"


def _owing_its_analysis(project_root: str | Path, status: dict[str, Any]) -> dict[str, Any]:
    """A run said finished, or stopped in its analysis or report, whose
    study owes the analyses and report of its joined pieces
    (`resume.analysis_owed`), said interrupted in its
    analysis, never completed: its analyses and report are of less than
    its production, and `fastmdx resume` runs them."""
    said = str((status or {}).get("status") or "").lower()
    # Or stopped from the GUI in a resume's analyses of the join: owed
    # them still, and `fastmdx resume`, not Analyze again, runs them (it
    # read Stopped with no fix offered).
    stopped_after = (said == "stopped"
                     and str(status.get("stage") or "").lower() in _PHASES_AFTER)
    if not status or (said not in _FINISHED_STATUSES and not stopped_after):
        return status
    from fastmdxplora.simulation.resume import (analysis_owed, being_carried_on,
                                                how_a_resume_finds)

    if not analysis_owed(project_root):
        return status
    if being_carried_on(project_root):
        # A resume or an extension is joining its pieces, analysing them or
        # writing their report: going, as the sidebar says, never owed
        # beside a `fastmdx resume` that would be refused.
        return {**status, "status": "running", "stage": "analysis", "piece": None,
                "current_step": None, "total_planned_steps": None,
                "carrying_on": True,
                "recorded_status": status.get("recorded_status") or status.get("status")}
    own = (how_a_resume_finds(project_root) or {}).get("own")
    if own:
        # Its analyses of the join ended in an error of their own, which a
        # resume refuses: failed, analysed again once put right, as the
        # round's card, the CLI and the Agent say.
        return {**status, "status": "failed", "stage": "analysis", "piece": None,
                "latest_error": f"Its analyses of its joined pieces ended in an error: {own}",
                "analysed_again_by": (f"fastmdx explore --output {Path(project_root)} "
                                      "--include analysis report --rerun"),
                "recorded_status": status.get("recorded_status") or status.get("status")}
    # Stopped in the study's analysis, not in a piece's production, nor at
    # its last step ("in piece 1's analysis, at step 4,400 of 4,400").
    # Where it was asked to stop, the stage it was stopped in, as the
    # health says it (it read as a wall time landing before the analyses).
    return {**status, "status": "interrupted", "stage": "analysis", "piece": None,
            "current_step": None, "total_planned_steps": None,
            "ended_by": ANALYSIS_OWED,
            "stopped_in": str(status.get("stage")).lower() if stopped_after else None,
            "recorded_status": status.get("recorded_status") or status.get("status")}


# ---------------------------------------------------------------------------
# A run of a study of several, between rounds.
#
# A study run until what it asks is determined (`simulation.stop_when`) with
# replicas judges its runs, extends each by what is needed and judges again,
# one run after another unless it runs them side by side. A run waiting for
# its turn had finished every piece so far, its analyses and its report: its
# card said Completed, its last stage Report at 100%, while the study was
# extending the run beside it and would extend this one next.
# ---------------------------------------------------------------------------

def _rounds_record(study: Path) -> dict[str, Any] | None:
    from fastmdxplora.simulation.stopping import RECORD

    try:
        record = json.loads(_records.read_text(study / RECORD, encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def _going(root: Path) -> bool:
    """Whether a run's newest record says it is going and it has not ended
    without saying so."""
    status = read_study_status(root)
    return (str(status.get("status") or "").lower() in _GOING_STATUSES
            and how_the_run_ended(root, status) is None)


#: A run's production as its pieces count it, kept while its pieces are as
#: they were: a run waiting for a round does not change, and is asked at
#: every poll of the page.
_DONE: dict[tuple[Any, ...], float | None] = {}


def _pieces_as_they_stand(root: Path) -> tuple[Any, ...]:
    """Each piece of a run by its name and the times its records were last
    written: a piece that finishes in place keeps its name."""
    stood = []
    try:
        pieces = sorted(_records.glob(root, "segment-*"))
    except OSError:
        return ()
    for piece in pieces:
        times = []
        for name in ("simulation", "simulation/checkpoint.chk.json",
                     "simulation/live_status.json", "manifest.json"):
            try:
                times.append(_records.stat(piece / name).st_mtime_ns)
            except OSError:
                times.append(None)
        stood.append((piece.name, *times))
    return tuple(stood)


def _production_done(root: Path) -> float | None:
    from fastmdxplora.simulation.resume import production_done_ns

    # Kept apart for a read held to the study (`record_access`), which
    # refuses what an open read follows: one is never answered from the other.
    held = not _records.can_read(Path(os.path.abspath(os.sep)))
    key = (str(root), held, _pieces_as_they_stand(root))
    if key not in _DONE:
        if len(_DONE) > 512:
            _DONE.clear()
        try:
            _DONE[key] = production_done_ns(root)
        except Exception:  # noqa: BLE001 - a record, not a verdict
            _DONE[key] = None
    return _DONE[key]


def rounds_of_a_study(study: Path) -> dict[str, Any] | None:
    """What a study run until what it asks is determined says of its rounds,
    read once for all its runs: its runs in the order it extends them, how
    many it extends side by side, the length its last judgement runs each
    to, and whether its own process is going ("running", "gone" or None
    where that cannot be asked here). Each run's production so far is read
    once, by the first of them asked (`between_rounds`). None where it is not in rounds
    now: no record, its rounds ended, or before its first judgement."""
    from fastmdxplora.orchestrator import RUN_PROCESS_FILE

    record = _rounds_record(study)
    if record is None or str(record.get("outcome") or "") != "running":
        return None
    rounds = [entry for entry in record.get("rounds") or [] if isinstance(entry, dict)]
    # Before its first judgement the study has decided nothing for its runs.
    if not rounds or rounds[-1].get("decision") != "extend":
        return None
    try:
        to_ns = float(rounds[-1]["production_ns"]) + float(rounds[-1]["more_ns"])
    except (KeyError, TypeError, ValueError):
        to_ns = None
    named = [str(name) for name in record.get("runs") or []]
    if not named:
        try:
            named = [folder.name for folder in sorted(_records.iterdir(study / "runs"))
                     if _records.is_dir(folder)]
        except OSError:
            named = []
    try:
        process = json.loads(_records.read_text(study / RUN_PROCESS_FILE, encoding="utf-8"))
    except (OSError, ValueError):
        process = None
    said = _what_its_process_says(study, process) if isinstance(process, dict) else None
    try:
        at_once = max(1, int(record.get("at_once") or 1))
    except (TypeError, ValueError):
        at_once = 1
    return {"study": study, "runs": named, "to_ns": to_ns, "process": said,
            "at_once": at_once}


def between_rounds(project_root: str | Path, status: dict[str, Any] | None = None, *,
                   going: list[str] | None = None,
                   rounds: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """For a run of a study of several that is being run until what it asks
    is determined, that finished its pieces so far and waits for the round
    its study decided after judging them: the study's runs going now, the
    one extended now or next where none is going, the production that round
    runs each run to, and this run's own. None otherwise: a run going or
    one that failed or stopped, a study before its first judgement, one
    whose rounds have ended, or one that ended without saying so.
    ``going`` names the study's runs going now and ``rounds`` is
    `rounds_of_a_study`, where the caller has read them; else they are read
    here. The runs behind, and each run's production, are worked out once
    for all the study's runs and kept in ``rounds``."""
    root = Path(project_root)
    if root.name == "simulation":
        root = root.parent
    if root.parent.name != "runs":
        return None
    study = root.parent.parent
    status = read_study_status(root) if status is None else status
    # Only a run that finished what it was asked so far waits: one that failed
    # or stopped is said as it is, one going as going.
    if str(status.get("status") or "").lower() not in ("completed", "complete", "ok"):
        return None
    rounds = rounds_of_a_study(study) if rounds is None else rounds
    if rounds is None or root.name not in rounds["runs"]:
        return None
    if rounds["process"] == "gone":
        return None
    if going is None:
        going = [name for name in rounds["runs"] if name != root.name
                 and _records.is_dir(study / "runs" / name / "simulation")
                 and _going(study / "runs" / name)]
    going = [name for name in going if name != root.name]
    # The study's own process says whether its rounds go on; where it cannot
    # be asked, a run of it going does.
    if rounds["process"] is None and not going:
        return None
    to_ns = rounds["to_ns"]
    # The runs not yet run to the round's length, in the order the study
    # extends them (a run going is read as going, its production not asked):
    # between one run's extension and the next (its analyses and report, a
    # worker starting) nothing of the study is going, and the first of them,
    # or as many as it runs side by side, are the ones extended next.
    # The same for every run of the study: worked out once for each set of
    # runs going, kept with the study's rounds.
    known = rounds.get("_behind")
    if known is not None and known[0] == tuple(going):
        behind, done_of = list(known[1]), known[2]
    else:
        behind, done_of = [], {}
        if isinstance(to_ns, (int, float)):
            for name in rounds["runs"]:
                if name in going:
                    continue
                done = done_of[name] = _production_done(study / "runs" / name)
                # A piece's length is rounded to whole steps: a run that did
                # its extension can fall short of the round's length by a few
                # femtoseconds, and is not behind.
                if isinstance(done, (int, float)) and done < to_ns - 1e-5:
                    behind.append(name)
        rounds["_behind"] = (tuple(going), tuple(behind), done_of)
    done = done_of[root.name] if root.name in done_of else _production_done(root)
    return {"study": str(study), "running": going, "behind": behind, "to_ns": to_ns,
            "done_ns": done, "run": root.name,
            "at_once": rounds.get("at_once", 1)}


def _waiting_for_the_round(status: dict[str, Any], waiting: dict[str, Any]) -> dict[str, Any]:
    """The run's record as the card shows it while it waits for the study's
    next round: waiting, its production to be carried on, its analyses and
    report to be made again, and its progress the production it has done of
    the length that round runs it to."""
    states = dict(status.get("stage_states") or {})
    for stage in ("production", *_PHASES_AFTER):
        if stage in states:
            states[stage] = "waiting"
    out = {**status, "status": "waiting", "recorded_status": status.get("status"),
           "stage": "production", "stage_states": states, "between_rounds": waiting}
    # Its steps so far are those of the pieces it was asked for; the round's
    # are not yet planned in steps, so none are said beside its progress.
    out["current_step"] = None
    out["total_planned_steps"] = None
    to_ns, done = waiting.get("to_ns"), waiting.get("done_ns")
    if isinstance(to_ns, (int, float)) and to_ns > 0 and isinstance(done, (int, float)):
        out["progress_percent"] = round(min(100.0, 100.0 * done / to_ns), 1)
    return out


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
    root = Path(project_root)

    # What ran, where that is recorded, beats any inference about what was
    # meant to run.
    try:
        manifest = json.loads(_records.read_text(root / "manifest.json", encoding="utf-8"))
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
    return phases_asked(root)


def phases_asked(project_root: str | Path) -> list[str]:
    """The phases the study's config asks for (`include_phase`, or every
    phase but `exclude_phase`), or empty where it says neither."""
    import yaml

    root = Path(project_root)
    for name in _CONFIG_NAMES:
        path = root / name
        if not _records.is_file(path):
            continue
        try:
            data = yaml.load(_records.read_text(path, encoding="utf-8"),
                             Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(data, dict):
            continue
        # Canonicalised here because the timeline reads the config file directly, not through the loader, so a study
        # written with the earlier `include` is read the same way.
        from fastmdxplora.config.loader import ConfigError, canonical_phase_keys

        try:
            canonical_phase_keys(data)
        except ConfigError:
            # Two spellings that disagree: the file says no one list of
            # phases, and the run refuses it, so none is read from it.
            continue
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


def _phases_recorded(project_root: str | Path) -> list[str]:
    """The phases the live record says have run or are running: a study
    carried on, or analysed or reported again, runs phases its Manifest
    does not list yet (a stopped first piece's Manifest ends at the
    simulation, and a rerun writes its own), and the sidebar said "Stage 5
    of 5" while the analysis ran."""
    try:
        status = read_study_status(project_root)
    except Exception:  # noqa: BLE001 - the Manifest's phases stand alone
        return []
    states = status.get("stage_states") if isinstance(status, dict) else None
    states = states if isinstance(states, dict) else {}
    stage = str((status or {}).get("stage") or "").lower()
    return [phase for phase, stages in PHASE_STAGES.items()
            if stage in stages or any(
                str(states.get(name) or "waiting").lower() not in ("waiting", "skipped")
                for name in stages)]


def run_stages(project_root: str | Path) -> list[str]:
    """The timeline stages this run can actually reach."""
    phases = run_phases(project_root)
    if phases:
        recorded = set(phases) | set(_phases_recorded(project_root))
        phases = [phase for phase in PHASE_STAGES if phase in recorded]
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
        with _records.open_text(path, newline="", encoding="utf-8") as fh:
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
    """The run's narration, the newest ``limit`` lines; a study carried on
    in pieces narrates each piece in its own folder, read in with the
    study's own by time (the carry-on was never shown)."""
    paths = [_simulation_dir(project_root) / EVENTS_FILE]
    paths += [piece / "simulation" / EVENTS_FILE for piece in _pieces_of(Path(project_root))]
    lines: list[str] = []
    for path in paths:
        try:
            lines += _records.read_text(path, encoding="utf-8").splitlines()
        except OSError:
            continue
    if len(paths) > 1:
        # Each event begins with its UTC time in ISO form, which sorts as
        # text; a line without one (a citation under its explanation) goes
        # with the event above it, where it sorted to the end on its own.
        blocks: list[list[str]] = []
        for line in lines:
            if blocks and "\t" not in line:
                blocks[-1].append(line)
            else:
                blocks.append([line])
        blocks.sort(key=lambda block: block[0].split("\t", 1)[0])
        lines = [line for block in blocks for line in block]
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


def _names(names: list[str]) -> str:
    """Names as a sentence says them: a, a and b, a, b and c."""
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _said_between_rounds(waiting: dict[str, Any]) -> dict[str, str]:
    """A run waiting for its study's next round, said as such."""
    from fastmdxplora.gui.simulated_time import say_length

    going = [str(name) for name in waiting.get("running") or []]
    behind = [str(name) for name in waiting.get("behind") or []]
    here = str(waiting.get("run") or "")
    to_ns = waiting.get("to_ns")
    length = (f" to {say_length(to_ns)} of production"
              if isinstance(to_ns, (int, float)) else "")
    reached = here not in behind and isinstance(to_ns, (int, float))
    # Where none is going, the first runs behind are the ones extended next:
    # one, or as many as the study runs side by side.
    try:
        at_once = max(1, int(waiting.get("at_once") or 1))
    except (TypeError, ValueError):
        at_once = 1
    now = going or behind[:at_once]
    if here in now and not going:
        beside = [name for name in now if name != here]
        message = (f"This run is extended next{',' if length else ''}{length}"
                   + (f", beside {_names(beside)}" if beside else "") + ".")
    elif reached and (going or behind):
        others = going + [name for name in behind if name != here]
        message = (f"This run has been extended{length}; its study still has "
                   f"{_names(others)} to extend, and judges every run once that is done.")
    elif now:
        # Before this run: those going now, then those behind ahead of it.
        ahead = [name for name in behind[:behind.index(here)] if name not in now] \
            if here in behind else []
        message = (f"Its study is extending {_names(now)}{length}; "
                   + (f"{_names(ahead)} {'comes' if len(ahead) == 1 else 'come'} next, then "
                      "this run." if ahead else
                      f"this run is extended after {'it' if len(now) == 1 else 'them'}."))
    else:
        message = ("Its study is judging its runs between rounds; this run is extended "
                   "next if what the study asks is not yet determined.")
    return {
        "state": "waiting",
        "headline": "Waiting for the study's next round",
        "message": message,
        "explanation": ("The study runs until what it asks is determined "
                        "(simulation.stop_when): it judges its runs, extends each by what "
                        "is needed, and judges them again. This run's analyses and report "
                        "are made again once it is extended."),
    }


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
    stage = str(status.get("stage") or "").lower()
    waiting = status.get("between_rounds")
    if isinstance(waiting, dict):
        return _said_between_rounds(waiting)
    if status.get("carrying_on"):
        return {
            "state": "ok",
            "headline": "Joining and analysing its pieces",
            "message": ("Its pieces are being joined, then analysed and reported over the "
                        "whole: any analyses and report shown until then are of less than "
                        "its whole production."),
            "explanation": "A resume or an extension of the study is running.",
        }
    owing = status.get("ended_by") == ANALYSIS_OWED
    # A run's own record (a study of runs keeps none, and is said from its
    # state below, `_a_study_of_runs`, which a run refused fails).
    ended = _its_round_ended(root) if root is not None and status else None
    if ended is not None:
        # Its rule's round failed, was stopped or was cut, or its rule was
        # never applied (`stopping.round_ended`, the one derivation
        # `workspace.study_state` reads): the round's sentence, as the
        # header, All studies and the card say it, with what the resume runs
        # first, whatever the live record of its last piece says.
        return _round_health(ended)
    if status.get("join_owed"):
        # Its last piece ran to its end and was never joined
        # (`_its_join_owed`): cut or stopped as its study is, in its own
        # words; "Stopped with an error" only where its join failed in its
        # study's round.
        said = str(status.get("status") or "interrupted").lower()
        return {
            "state": said,
            "headline": {"stopped": "Stopped", "failed": "Stopped with an error"}.get(
                said, "Interrupted"),
            "message": str(latest_error or ""),
            "explanation": ("Its piece's production is whole; joining it, then its analyses "
                            "and report, is what remains."),
            "carry_on": f"fastmdx resume {_study_of(Path(root))}" if root is not None
            else "fastmdx resume",
        }
    if owing and status.get("stopped_in") in _PHASES_AFTER:
        when = ("while its joined pieces were analysed: part of their analyses had "
                "run, not their report, so any analyses and report shown are"
                if status["stopped_in"] == "analysis" else
                "while the report of its joined pieces was written: their analyses had "
                "run, not their report, so any report shown is")
        return {
            "state": "interrupted",
            "headline": "Its analysis is owed",
            "message": ("The study was stopped, as asked, " + when + " of less than its "
                        "whole production. " + _resume_said(root) + " runs them."),
            "explanation": ANALYSIS_OWED_STOPPED_EXPLANATION,
        }
    if status.get("ended_by") == ANALYSIS_OWED:
        return {
            "state": "interrupted",
            "headline": "Its analysis is owed",
            "message": ("The study's process ended after its pieces were joined, before their "
                        "analyses and report had run: any shown are of less than its whole "
                        "production. " + _resume_said(root) + " runs them."),
            "explanation": ANALYSIS_OWED_EXPLANATION,
        }
    if str(status.get("status", "")).lower() in _GOING_STATUSES and stage in _PHASES_AFTER:
        # The trajectory analysed or the report written, again or for the
        # first time: nothing of the simulation is being sampled, so its
        # record is not stale, and a failed run analysed again is running.
        # Said "Telemetry is stale ... may have crashed" beside Stop.
        return {
            "state": "ok",
            "headline": "Analysing" if stage == "analysis" else "Writing the report",
            "message": ("The trajectory is being analysed." if stage == "analysis"
                        else "The report is being written."),
            "explanation": "The simulation has ended; its record is kept as it ended.",
        }
    if str(status.get("status", "")).lower() == "stopped":
        # Asked to stop, not failed: its message says where it stopped.
        return {
            "state": "stopped",
            "headline": "Stopped",
            "message": str(latest_error or "The run was stopped."),
            "explanation": (STOPPED_BEFORE_PRODUCTION_EXPLANATION
                            if stage in ("setup", "minimization", "nvt", "npt")
                            else STOPPED_IN_A_PHASE_AFTER_EXPLANATION
                            if stage in _PHASES_AFTER
                            else STOPPED_EXPLANATION),
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
        again = status.get("analysed_again_by")
        return {
            "state": "failed",
            "headline": "Stopped with an error",
            "message": said,
            "explanation": (f"Put right what its analyses said, then {again} analyses it "
                            "again." if again else NUMERIC_EXPLANATION if numeric else ""),
            **({"carry_on": again} if again else {}),
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

    said = str(status.get("status", "")).lower()
    if said == "interrupted":
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
    of_runs = _a_study_of_runs(root) if root is not None else None
    if of_runs is not None:
        return of_runs
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


def _round_health(ended: Any) -> dict[str, str]:
    """The health of a study of one run whose rule's round ended before it
    was judged again (`stopping.round_ended`)."""
    return {
        "state": ended.state,
        "headline": _STUDY_OF_RUNS_SAID[ended.state][0],
        "message": ended.said,
        "explanation": ended.carry_on_on_a_page,
        "carry_on": " ".join(ended.argv),
    }


def _its_round_ended(root: str | Path) -> Any:
    """How the rule's last round of the study at ``root`` ended, where it
    failed, was stopped or was cut (`stopping.round_ended`); None otherwise
    and where it cannot be read."""
    from fastmdxplora.simulation.stopping import round_ended

    try:
        return round_ended(root)
    except Exception:  # noqa: BLE001 - said from the run's own record
        return None


def not_read_health(said: str) -> dict[str, str]:
    """The health of a run whose live record a held read refused
    (`record_access`): not read, ``said`` naming it and why, and no reason
    given for its absence, since it is not absent."""
    return {
        "state": "not read",
        "headline": "Not read",
        "message": said,
        "explanation": ("The run's live record is how this page knows how the run went, "
                        "so how it went is not shown."),
    }


#: A study of several runs, said in the words its state is said in
#: everywhere else (`workspace.study_state`, read by All studies' cards).
_STUDY_OF_RUNS_SAID = {
    "completed": ("Completed", "Every run of this study completed."),
    "running": ("Running", "Runs of this study are still going."),
    "failed": ("Stopped with an error", "A run of this study ended with an error."),
    "stopped": ("Stopped", "A run of this study was stopped."),
    "interrupted": ("Interrupted", "A run of this study ended without recording why."),
    "incomplete": ("Incomplete", "Not every run of this study has finished."),
}


def _a_study_of_runs(root: str | Path) -> dict[str, str] | None:
    """A study of several runs, said from its runs' states as All studies
    says it. It keeps no live record of its own, so it was said "unknown"
    here (orange) while its card said "completed": two derivations of one
    state. Now one: `workspace.study_state`."""
    root = Path(root)
    batch = root / "batch_manifest.json"
    from fastmdxplora.simulation.pipeline import _refused

    if _refused(batch):
        # Its record of runs refused by a held read is not read: not a
        # folder that keeps none (`_not_a_run`).
        from fastmdxplora.gui.series import refused_said

        return _study_of_runs_not_read(refused_said(root, batch) + ".")
    before = _records.refused()
    try:
        if not _records.is_file(batch):
            return None
        from fastmdxplora.gui.workspace import study_state

        state = study_state(root)
    except Exception:  # noqa: BLE001 - a record that cannot be read is said as before
        return None
    # A record of a run the held read refused leaves the study's state
    # unknown: said not read, not derived from the records that were.
    refused = sorted(_records.refused() - before)
    if refused:
        return _study_of_runs_not_read(
            ", ".join(refused) + (" was" if len(refused) == 1 else " were") + " not read.")
    headline, message = _STUDY_OF_RUNS_SAID.get(state, (None, None))
    if headline is None:
        return None
    from fastmdxplora.simulation.stopping import round_ended

    # Its rule's round, not a run, ended it (`stopping.round_ended`, the
    # derivation `study_state` read): said by what ended it, and carried
    # on by the command, where "A run of this study ended with an error"
    # named no run that had.
    ended = round_ended(root) if state in ("failed", "stopped", "interrupted") else None
    if ended is not None and ended.state == state:
        return {
            "state": state,
            "headline": headline,
            "message": ended.said,
            "explanation": ended.carry_on_on_a_page,
            "of_runs": True,
            "carry_on": " ".join(ended.argv),
        }
    return {
        "state": state,
        "headline": headline,
        "message": message,
        "explanation": ("Each run keeps its own record of its simulation; "
                        "open one from Runs in the sidebar to see it."),
        "of_runs": True,
    }


def _study_of_runs_not_read(said: str) -> dict[str, Any]:
    """A study of several runs whose state a held read could not derive,
    since a record it is derived from was refused (`record_access`)."""
    return {
        "state": "not read",
        "headline": "Not read",
        "message": said,
        "explanation": ("The study's state is derived from its record of runs and each "
                        "run's own record, so how it went is not shown."),
        "of_runs": True,
    }


def _not_a_run(root: str | Path) -> dict[str, str] | None:
    """What a folder with no telemetry is, when it is not a run at all.

    A folder holding several runs was described as a run that had turned
    its telemetry off, and so was an empty one opened to start a study:
    both were told about a setting when the answer was where they were.
    """
    root = Path(root)
    if _records.is_file(root / "manifest.json") or _records.is_dir(root / "simulation"):
        return None
    try:
        runs = sorted(child.name for child in _records.iterdir(root)
                      if _records.is_dir(child) and _records.is_file(child / "manifest.json"))
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
