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


def exploration_defaults() -> dict[str, Any]:
    """Return UI defaults from the same Python sources used by the CLI."""
    from fastmdxplora.setup.pipeline import DEFAULTS as setup_defaults
    from fastmdxplora.simulation.pipeline import DEFAULTS as sim_defaults
    from fastmdxplora.simulation.runner import (
        DEFAULT_NPT_STEPS,
        DEFAULT_NVT_STEPS,
        DEFAULT_PRODUCTION_STEPS,
        DEFAULT_TRAJECTORY_INTERVAL_STEPS,
    )

    return {
        "system": "1L2Y",
        "run_name": "",
        "setup": {
            "ph": float(setup_defaults.get("ph", 7.0)),
            "forcefield": str(setup_defaults.get("forcefield", "auto")),
            "water_model": setup_defaults.get("water_model") or "auto",
            "ion_concentration_M": float(setup_defaults.get("ion_concentration_M", 0.15)),
            "solvent_padding_nm": float(setup_defaults.get("solvent_padding_nm", 1.0)),
            "heterogens": str(setup_defaults.get("heterogens", "drop")),
            "keep_heterogens": bool(setup_defaults.get("keep_heterogens", False)),
            "keep_water": bool(setup_defaults.get("keep_water", False)),
        },
        "simulation": {
            "minimize": bool(sim_defaults.get("minimize", True)),
            "nvt_steps": int(DEFAULT_NVT_STEPS),
            "npt_steps": int(DEFAULT_NPT_STEPS),
            "production_steps": int(DEFAULT_PRODUCTION_STEPS),
            "timestep_fs": float(sim_defaults.get("timestep_fs", 2.0)),
            "temperature_K": float(sim_defaults.get("temperature_K", 300.0)),
            "friction_per_ps": float(sim_defaults.get("friction_per_ps", 1.0)),
            "integrator": str(sim_defaults.get("integrator", "langevin_middle")),
            "platform": str(sim_defaults.get("platform", "auto")),
            "precision": str(sim_defaults.get("precision", "mixed")),
            "trajectory_interval_steps": int(DEFAULT_TRAJECTORY_INTERVAL_STEPS),
            "checkpoint_interval_steps": int(sim_defaults.get("checkpoint_interval_steps", 10000)),
            "telemetry_interval": int(sim_defaults.get("telemetry_interval", 1000)),
        },
        "workflow": {
            "run_analysis": True,
            "run_report": True,
            "analyses": list(_ANALYSES),
            "report_document": True,
            "report_slides": True,
            "report_bundle": True,
        },
        "choices": {
            "forcefields": list(_FORCEFIELDS),
            "platforms": list(_PLATFORMS),
            "precisions": list(_PRECISIONS),
            "integrators": list(_INTEGRATORS),
            "analyses": list(_ANALYSES),
        },
    }


def validate_exploration_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize one dashboard launch request.

    The returned mapping is safe to translate into a list-form subprocess
    command.  Validation is intentionally stricter than argparse so bad form
    values are reported next to their fields instead of failing after launch.
    """
    errors: dict[str, str] = {}
    warnings: list[str] = []

    system = str(payload.get("system") or "").strip()
    if not system:
        errors["system"] = "Enter a PDB ID, sequence, or local PDB/CIF path."
    elif len(system) > 4096:
        errors["system"] = "System input is too long."

    run_name_raw = str(payload.get("run_name") or "").strip()
    default_name = f"{_slug(system, 'system')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    run_name = _slug(run_name_raw, default_name)

    setup_in = payload.get("setup") if isinstance(payload.get("setup"), Mapping) else {}
    sim_in = payload.get("simulation") if isinstance(payload.get("simulation"), Mapping) else {}
    workflow_in = payload.get("workflow") if isinstance(payload.get("workflow"), Mapping) else {}

    setup: dict[str, Any] = {}
    simulation: dict[str, Any] = {}
    workflow: dict[str, Any] = {}

    try:
        setup["ph"] = _number(setup_in, "ph", default=7.0, minimum=0.0, maximum=14.0)
    except ValueError as exc:
        errors["setup.ph"] = str(exc)
    forcefield = str(setup_in.get("forcefield") or "auto")
    if forcefield not in _FORCEFIELDS:
        errors["setup.forcefield"] = "Choose a supported force field."
    else:
        setup["forcefield"] = forcefield
    water_model = str(setup_in.get("water_model") or "auto").strip()
    setup["water_model"] = water_model
    try:
        setup["ion_concentration_M"] = _number(
            setup_in,
            "ion_concentration_M",
            default=0.15,
            minimum=0.0,
            maximum=5.0,
        )
    except ValueError as exc:
        errors["setup.ion_concentration_M"] = str(exc)
    try:
        setup["solvent_padding_nm"] = _number(
            setup_in,
            "solvent_padding_nm",
            default=1.0,
            minimum=0.1,
            maximum=10.0,
        )
    except ValueError as exc:
        errors["setup.solvent_padding_nm"] = str(exc)
    heterogens = str(setup_in.get("heterogens", "drop")).strip().lower()
    if heterogens not in {"drop", "keep", "auto"}:
        errors["setup.heterogens"] = (
            f"expected drop, keep, or auto; got {heterogens!r}"
        )
    else:
        setup["heterogens"] = heterogens
    setup["keep_heterogens"] = bool(setup_in.get("keep_heterogens", False))
    setup["keep_water"] = bool(setup_in.get("keep_water", False))

    numeric_fields = (
        ("nvt_steps", 250000, 0, 10_000_000_000, True),
        ("npt_steps", 500000, 0, 10_000_000_000, True),
        ("production_steps", 1000000, 1, 100_000_000_000, True),
        ("timestep_fs", 2.0, 0.01, 20.0, False),
        ("temperature_K", 300.0, 1.0, 5000.0, False),
        ("friction_per_ps", 1.0, 0.0, 1000.0, False),
        ("trajectory_interval_steps", 1000, 1, 10_000_000_000, True),
        ("checkpoint_interval_steps", 10000, 0, 10_000_000_000, True),
        ("telemetry_interval", 1000, 1, 10_000_000_000, True),
    )
    for key, default, low, high, integer in numeric_fields:
        try:
            simulation[key] = _number(
                sim_in,
                key,
                default=default,
                minimum=low,
                maximum=high,
                integer=integer,
            )
        except ValueError as exc:
            errors[f"simulation.{key}"] = str(exc)

    simulation["minimize"] = bool(sim_in.get("minimize", True))
    integrator = str(sim_in.get("integrator") or "langevin_middle")
    platform = str(sim_in.get("platform") or "auto")
    precision = str(sim_in.get("precision") or "mixed")
    if integrator not in _INTEGRATORS:
        errors["simulation.integrator"] = "Choose a supported integrator."
    else:
        simulation["integrator"] = integrator
    if platform not in _PLATFORMS:
        errors["simulation.platform"] = "Choose a supported OpenMM platform."
    else:
        simulation["platform"] = platform
    if precision not in _PRECISIONS:
        errors["simulation.precision"] = "Choose a supported precision mode."
    else:
        simulation["precision"] = precision

    workflow["run_analysis"] = bool(workflow_in.get("run_analysis", True))
    workflow["run_report"] = bool(workflow_in.get("run_report", True))
    requested_analyses = workflow_in.get("analyses", list(_ANALYSES))
    if not isinstance(requested_analyses, list):
        requested_analyses = list(_ANALYSES)
    analyses = [str(item) for item in requested_analyses if str(item) in _ANALYSES]
    workflow["analyses"] = analyses
    workflow["report_document"] = bool(workflow_in.get("report_document", True))
    workflow["report_slides"] = bool(workflow_in.get("report_slides", True))
    workflow["report_bundle"] = bool(workflow_in.get("report_bundle", True))

    if workflow["run_report"] and not workflow["run_analysis"]:
        warnings.append("Reports can be generated without analysis, but they will contain fewer scientific results.")

    timestep = float(simulation.get("timestep_fs", 2.0))
    nvt_steps = int(simulation.get("nvt_steps", 0))
    npt_steps = int(simulation.get("npt_steps", 0))
    production_steps = int(simulation.get("production_steps", 0))
    trajectory_interval = int(simulation.get("trajectory_interval_steps", 1))
    telemetry_interval = int(simulation.get("telemetry_interval", 1))

    durations = {
        "nvt_ns": nvt_steps * timestep / 1_000_000.0,
        "npt_ns": npt_steps * timestep / 1_000_000.0,
        "production_ns": production_steps * timestep / 1_000_000.0,
        "total_ns": (nvt_steps + npt_steps + production_steps) * timestep / 1_000_000.0,
        "trajectory_frames": (production_steps + trajectory_interval - 1) // trajectory_interval,
        "dashboard_frames": (
            nvt_steps + npt_steps + production_steps + telemetry_interval - 1
        ) // telemetry_interval,
    }
    if durations["production_ns"] < 1.0:
        warnings.append(
            f"Production time is {durations['production_ns']:.4g} ns; this is suitable for testing, not strong scientific conclusions."
        )

    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "config": {
            "system": system,
            "run_name": run_name,
            "setup": setup,
            "simulation": simulation,
            "workflow": workflow,
        },
        "summary": durations,
    }



def build_config_yaml(config: Mapping[str, Any], output_dir: Path | str) -> str:
    """Render an exploration config as a FastMDXplora YAML study file.

    The GUI builder collects the same options the CLI accepts. This turns
    that selection into the canonical config format so a study designed in
    the GUI can be saved, reviewed, version-controlled, and submitted
    anywhere, including on a cluster where the GUI cannot run.

    The output is accepted by ``fastmdx explore --config``.
    """
    import yaml

    setup = dict(config["setup"])
    sim = dict(config["simulation"])
    workflow = dict(config["workflow"])
    run_name = str(config.get("run_name") or "fastmdxplora_run")

    phases = ["setup", "simulation"]
    if workflow.get("run_analysis"):
        phases.append("analysis")
    if workflow.get("run_report"):
        phases.append("report")

    setup_block: dict[str, Any] = {
        "ph": setup["ph"],
        "forcefield": setup["forcefield"],
        "solvent_padding_nm": setup["solvent_padding_nm"],
        "ion_concentration_M": setup["ion_concentration_M"],
    }
    if setup.get("water_model") and setup["water_model"] != "auto":
        setup_block["water_model"] = setup["water_model"]
    if setup.get("heterogens") and setup["heterogens"] != "drop":
        setup_block["heterogens"] = setup["heterogens"]
    if setup.get("keep_heterogens"):
        setup_block["keep_heterogens"] = True
    if setup.get("keep_water"):
        setup_block["keep_water"] = True

    simulation_block: dict[str, Any] = {
        "nvt_steps": sim["nvt_steps"],
        "npt_steps": sim["npt_steps"],
        "production_steps": sim["production_steps"],
        "timestep_fs": sim["timestep_fs"],
        "temperature_K": sim["temperature_K"],
        "friction_per_ps": sim["friction_per_ps"],
        "integrator": sim["integrator"],
        "platform": sim["platform"],
        "precision": sim["precision"],
        "trajectory_interval_steps": sim["trajectory_interval_steps"],
        "checkpoint_interval_steps": sim["checkpoint_interval_steps"],
    }
    if not sim.get("minimize", True):
        simulation_block["minimize"] = False

    document: dict[str, Any] = {
        "systems": [{"id": run_name, "system": str(config["system"])}],
        "output": str(output_dir),
        "include": phases,
        "setup": setup_block,
        "simulation": simulation_block,
    }

    if workflow.get("run_analysis"):
        analyses = list(workflow.get("analyses") or [])
        analysis_block: dict[str, Any] = {}
        if analyses and set(analyses) != set(_ANALYSES):
            analysis_block["include"] = analyses
        if analysis_block:
            document["analysis"] = analysis_block

    if workflow.get("run_report"):
        report_block: dict[str, Any] = {"title": run_name}
        for key, flag in (
            ("document", "report_document"),
            ("slides", "report_slides"),
            ("bundle", "report_bundle"),
        ):
            if not workflow.get(flag, True):
                report_block[key] = False
        document["report"] = report_block

    body = yaml.safe_dump(document, sort_keys=False, default_flow_style=False)
    header = (
        "# FastMDXplora study configuration\n"
        "#\n"
        "# Generated by the FastMDXplora GUI. Run it with:\n"
        "#\n"
        "#     fastmdx explore --config <this file>\n"
        "#\n"
        "# Command-line flags override values set here, so the same file can\n"
        "# be reused with per-run overrides. Every option is documented at\n"
        "# https://fastmdxplora.readthedocs.io/en/latest/configuration.html\n"
        "\n"
    )
    return header + body

def build_exploration_command(config: Mapping[str, Any], output_dir: Path) -> list[str]:
    """Translate a normalized exploration config into the canonical CLI."""
    setup = config["setup"]
    sim = config["simulation"]
    workflow = config["workflow"]
    command = [
        sys.executable,
        "-m",
        "fastmdxplora.cli.main",
        "explore",
        "--system",
        str(config["system"]),
        "--output",
        str(output_dir),
        "--setup-ph",
        str(setup["ph"]),
        "--setup-forcefield",
        str(setup["forcefield"]),
        "--setup-ion-concentration-M",
        str(setup["ion_concentration_M"]),
        "--setup-solvent-padding-nm",
        str(setup["solvent_padding_nm"]),
        "--simulate-nvt-steps",
        str(sim["nvt_steps"]),
        "--simulate-npt-steps",
        str(sim["npt_steps"]),
        "--simulate-production-steps",
        str(sim["production_steps"]),
        "--simulate-timestep-fs",
        str(sim["timestep_fs"]),
        "--simulate-temperature-K",
        str(sim["temperature_K"]),
        "--simulate-friction-per-ps",
        str(sim["friction_per_ps"]),
        "--simulate-integrator",
        str(sim["integrator"]),
        "--simulate-platform",
        str(sim["platform"]),
        "--simulate-precision",
        str(sim["precision"]),
        "--simulate-trajectory-interval-steps",
        str(sim["trajectory_interval_steps"]),
        "--simulate-checkpoint-interval-steps",
        str(sim["checkpoint_interval_steps"]),
        "--simulate-live-telemetry",
        "--simulate-telemetry-interval",
        str(sim["telemetry_interval"]),
    ]
    if setup.get("water_model") and setup["water_model"] != "auto":
        command.extend(["--setup-water-model", str(setup["water_model"])])
    if setup.get("heterogens") and setup["heterogens"] != "drop":
        command.extend(["--setup-heterogens", str(setup["heterogens"])])
    if setup.get("keep_heterogens"):
        command.append("--setup-keep-heterogens")
    if setup.get("keep_water"):
        command.append("--setup-keep-water")
    if not sim.get("minimize", True):
        command.append("--simulate-no-minimize")

    phases = ["setup", "simulation"]
    if workflow.get("run_analysis"):
        phases.append("analysis")
    if workflow.get("run_report"):
        phases.append("report")
    if phases != ["setup", "simulation", "analysis", "report"]:
        command.extend(["--include", *phases])

    analyses = list(workflow.get("analyses") or [])
    if workflow.get("run_analysis") and analyses and set(analyses) != set(_ANALYSES):
        command.extend(["--analyze-analyses", *analyses])
    if workflow.get("run_report"):
        if not workflow.get("report_document", True):
            command.append("--report-no-document")
        if not workflow.get("report_slides", True):
            command.append("--report-no-slides")
        if not workflow.get("report_bundle", True):
            command.append("--report-no-bundle")
        command.extend(["--report-title", str(config.get("run_name") or "FastMDXplora Run")])
    return command


def exploration_environment_error(config: Mapping[str, Any]) -> str | None:
    """Return an actionable error when the dashboard cannot run the workflow.

    The normal CLI deliberately imports the chemistry stack lazily and lets
    phase pipelines write a warning manifest when optional packages are
    missing.  That behavior is useful for inspecting configuration on light
    installations, but it is misleading for the dashboard's *Run Simulation*
    action: the child exits successfully without producing a simulation.
    """
    workflow = config.get("workflow")
    missing = missing_dependencies(
        include_analysis=(
            isinstance(workflow, Mapping) and bool(workflow.get("run_analysis"))
        )
    )

    if not missing:
        return None
    return dependency_error_message(missing)


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
    process: subprocess.Popen[Any] | None = None
    process_started_at: str | None = None
    process_finished_at: str | None = None
    process_returncode: int | None = None
    completion_error: str | None = None
    data_stale: bool = False
    log_path: Path | None = None
    command: list[str] = field(default_factory=list)
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def __post_init__(self) -> None:
        self.workspace_root = self.workspace_root.expanduser().resolve()
        self.exploration_root = self.exploration_root.expanduser().resolve()
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.exploration_root.mkdir(parents=True, exist_ok=True)
        if self.active_root is not None:
            self.active_root = self.active_root.expanduser().resolve()

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

    def _telemetry_predates_process(self) -> bool:
        if self.active_root is None or not self.process_started_at:
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
            if not detail and lines:
                detail = lines[-1]
        suffix = f" {detail}" if detail else ""
        return (
            f"The workflow exited with code {self.process_returncode}.{suffix} "
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
            status = "idle"
            if running:
                status = "running"
            elif self.completion_error:
                status = "failed"
            elif self.process is not None and self.process_returncode == 0:
                status = "completed"
            elif self.process is not None and self.process_returncode is not None:
                status = "failed"
            return {
                "mode": "home" if self.active_root is None or self.data_stale else "run",
                "status": status,
                "active_run": str(self.active_root) if self.active_root and not self.data_stale else None,
                "workspace": str(self.workspace_root),
                "exploration_root": str(self.exploration_root),
                "process_running": running,
                "returncode": self.process_returncode,
                "error": self.completion_error,
                "started_at": self.process_started_at,
                "finished_at": self.process_finished_at,
                "log_path": str(self.log_path) if self.log_path else None,
                "command": list(self.command),
                "can_launch": not running,
            }

    def launch(self, payload: Mapping[str, Any], *, dashboard_url: str | None = None) -> dict[str, Any]:
        result = validate_exploration_payload(payload)
        if not result["valid"]:
            return result
        config = result["config"]
        with self.lock:
            self._refresh_process()
            if self.process is not None and self.process.poll() is None:
                return {
                    **result,
                    "valid": False,
                    "errors": {"run": "A FastMDXplora workflow is already running."},
                }

            environment_error = exploration_environment_error(config)
            if environment_error:
                return {
                    **result,
                    "valid": False,
                    "error": environment_error,
                    "errors": {"run": environment_error},
                }

            run_name = _slug(config["run_name"])
            output_dir = (self.exploration_root / run_name).resolve()
            try:
                output_dir.relative_to(self.exploration_root)
            except ValueError:
                return {
                    **result,
                    "valid": False,
                    "errors": {"run_name": "Run name resolves outside the launch directory."},
                }
            if output_dir.exists() and any(output_dir.iterdir()):
                detail = f"Output folder already exists and is not empty: {output_dir}. Choose a new output folder to start a new simulation."
                self.data_stale = True
                self.completion_error = detail
                return {
                    **result,
                    "valid": False,
                    "error": detail,
                    "errors": {"run_name": detail},
                    "next_action": "Choose a new output folder; the previous run was preserved.",
                }
            output_dir.mkdir(parents=True, exist_ok=True)
            self.data_stale = False
            command = build_exploration_command(config, output_dir)
            started = self._spawn(command, output_dir, dashboard_url)
            return {**result, **started}

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
            process = subprocess.Popen(
                command,
                cwd=str(self.exploration_root),
                env=env,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                shell=False,
            )
        except Exception:
            log_handle.close()
            raise
        # Popen owns an inherited OS handle. Closing this copy avoids a
        # long-lived Python file object while the child continues writing.
        log_handle.close()
        self.active_root = output_dir
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
        state: Mapping[str, Any],
        *,
        dashboard_url: str | None = None,
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
            requested = str(dict(state).get("output") or "").strip()
            if not requested:
                requested = "analysis_output"
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

            prepared = prepare_run(dict(state), output_dir)
            if not prepared["ok"]:
                return prepared

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
