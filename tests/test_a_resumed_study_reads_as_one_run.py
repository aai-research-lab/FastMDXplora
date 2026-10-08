"""A study carried on from its checkpoint reads as one run.

**What would fix it**, **Run it** (`fastmdx resume`) carries a stopped
production on in the study's next piece, `segment-001/`, with a live record
of its own, then joins the pieces and analyses the whole in the study's own
folder. The GUI read only the study's first record: while the resume ran,
the sidebar, the card and `/api/status` said "Production stopped"; once
every phase had finished, the health card said "Stopped with an error"
(the first piece's stop, still its `latest_error`), with 26 of 100 frames
written. The piece's own record also ended in "NVT", a stage it never took.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

STOP = ("Production was stopped by SIGTERM at step 2,800 (0.006 of 0.020 ns). A "
        "checkpoint was written on frame 28, so `fastmdx resume` carries it on.")


def _ago(minutes: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _metrics(path: Path, rows: list[tuple[str, int]]) -> None:
    lines = ["timestamp,stage,step,simulation_time_ns,temperature,current_frame_count"]
    for stage, step in rows:
        lines.append(f"{_ago(1)},{stage},{step},{step * 2e-6},300.0,")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _stopped_study(root: Path) -> Path:
    """Stopped in production at step 2,800 of 10,000, after 5,000 steps of
    equilibration, a frame every 100 steps."""
    simulation = root / "simulation"
    _write(simulation / "live_status.json", {
        "stage": "production", "status": "stopped", "current_step": 7622,
        "total_planned_steps": 15000, "current_frame_count": 26, "planned_frame_count": 100,
        "timestep_fs": 2.0, "latest_error": STOP, "nvt_steps_planned": 2500,
        "npt_steps_planned": 2500, "production_steps_planned": 10000,
        "elapsed_wall_time_s": 300.0, "last_update_timestamp": _ago(10),
        "stage_states": {"setup": "completed", "minimization": "completed",
                         "nvt": "completed", "npt": "completed", "production": "current",
                         "analysis": "waiting", "report": "waiting"}})
    _write(simulation / "checkpoint.chk.json", {
        "stage": "production", "step": 2800, "finished": False,
        "trajectory_interval_steps": 100})
    (simulation / "checkpoint.chk").write_bytes(b"")
    _metrics(simulation / "live_metrics.csv",
             [("minimization", 0), ("NVT equilibration", 1000), ("NPT equilibration", 4000)]
             + [("Production", 5000 + 300 * k) for k in range(10)])
    return root


def _piece(root: Path, *, status: str, step: int, minutes_ago: float,
           finished: bool = False) -> Path:
    simulation = root / "segment-001" / "simulation"
    _write(simulation / "live_status.json", {
        "stage": "production", "status": status, "current_step": step,
        "total_planned_steps": 7200, "current_frame_count": step // 100,
        "planned_frame_count": 72, "timestep_fs": 2.0, "latest_error": None,
        "nvt_steps_planned": 0, "npt_steps_planned": 0, "production_steps_planned": 7200,
        "elapsed_wall_time_s": 60.0, "last_update_timestamp": _ago(minutes_ago),
        "stage_states": {"minimization": "skipped", "nvt": "skipped", "npt": "skipped",
                         "production": "current" if status == "running" else "completed",
                         "analysis": "skipped", "report": "skipped"}})
    _write(simulation / "checkpoint.chk.json", {
        "stage": "production", "step": step, "finished": finished,
        "trajectory_interval_steps": 100})
    _metrics(simulation / "live_metrics.csv",
             [("NVT", 0)] + [("Production", s) for s in range(1000, step + 1, 1000)])
    return root


def test_while_the_resume_runs_the_study_is_running_in_production(tmp_path):
    from fastmdxplora.gui.telemetry import analyze_health, read_metrics, status_as_it_stands

    root = _piece(_stopped_study(tmp_path / "study"), status="running", step=3600,
                  minutes_ago=0)
    status = status_as_it_stands(root)
    assert status["status"] == "running" and status["stage"] == "production"
    assert status["piece"] == "segment-001"
    assert status["latest_error"] is None
    # 5,000 of equilibration, 28 frames kept before the checkpoint, 36 since.
    assert status["current_step"] == 5000 + 2800 + 3600
    assert status["total_planned_steps"] == 15000
    assert (status["current_frame_count"], status["planned_frame_count"]) == (64, 100)
    assert status["stage_states"]["production"] == "current"
    health = analyze_health(status, read_metrics(root))
    assert health["state"] not in {"failed", "stopped"}, health
    steps = [int(row["step"]) for row in read_metrics(root, limit=None)]
    assert steps == sorted(steps) and steps[-1] == 5000 + 2800 + 3000
    assert max(step for step in steps if step <= 7800) <= 7800


def test_once_joined_and_analysed_the_study_completed_whole(tmp_path):
    from fastmdxplora.gui.telemetry import analyze_health, read_metrics, status_as_it_stands

    root = _piece(_stopped_study(tmp_path / "study"), status="completed", step=7200,
                  minutes_ago=5, finished=True)
    first = json.loads((root / "simulation" / "live_status.json").read_text())
    # The analyses of the whole ran after, in the study's own folder, and
    # left the first piece's stop in its record.
    first.update(status="completed", stage="report", last_update_timestamp=_ago(1))
    _write(root / "simulation" / "live_status.json", first)
    status = status_as_it_stands(root)
    assert status["status"] == "completed" and status["stage"] == "report"
    assert status["latest_error"] is None
    assert (status["current_frame_count"], status["planned_frame_count"]) == (100, 100)
    assert status["current_step"] == status["total_planned_steps"] == 15000
    assert abs(status["simulation_time_completed_ns"] - 0.03) < 1e-9
    health = analyze_health(status, read_metrics(root))
    assert health["state"] == "ok" and health["message"] == "Completed", health


def test_a_resume_that_failed_is_said_to_have(tmp_path):
    from fastmdxplora.gui.telemetry import analyze_health, read_metrics, status_as_it_stands

    root = _piece(_stopped_study(tmp_path / "study"), status="failed", step=1000,
                  minutes_ago=1)
    record = json.loads((root / "segment-001/simulation/live_status.json").read_text())
    record["latest_error"] = "OpenMMException: Particle coordinate is NaN."
    _write(root / "segment-001/simulation/live_status.json", record)
    status = status_as_it_stands(root)
    assert status["status"] == "failed"
    assert "NaN" in analyze_health(status, read_metrics(root))["message"]


def test_a_study_in_one_piece_reads_as_before(tmp_path):
    from fastmdxplora.gui.telemetry import read_status, read_study_status

    root = _stopped_study(tmp_path / "study")
    assert read_study_status(root) == read_status(root)


def _ended(tmp_path, options, states, result_status):
    from fastmdxplora.gui.telemetry import TelemetryWriter, read_status
    from fastmdxplora.orchestrator import FastMDXplora, PhaseResult

    simulation = tmp_path / "simulation"
    writer = TelemetryWriter(simulation)
    writer.write_status(status="running", stage="setup", stage_states={
        "setup": "completed", "minimization": "waiting", "nvt": "waiting",
        "npt": "waiting", "production": "waiting", "analysis": "waiting",
        "report": "waiting"})
    FastMDXplora._mark_dashboard_phase_start(writer, "simulation", options)
    for name, state in states:
        writer.mark_stage(name, state, status="running" if state != "failed" else "failed")
    now = datetime.now(timezone.utc).isoformat()
    result = PhaseResult(name="simulation", status=result_status, output_dir=str(simulation),
                         started_at=now, finished_at=now,
                         message="Simulation failed." if result_status == "error" else "")
    FastMDXplora._mark_dashboard_phase_end(writer, "simulation", result, options)
    return read_status(tmp_path)


def test_a_piece_ends_in_production_having_taken_no_other_stage(tmp_path):
    options = {"minimize": False, "nvt_steps": 0, "npt_steps": 0}
    status = _ended(tmp_path, options, [("production", "current"),
                                        ("production", "completed")], "ok")
    assert status["stage"] == "production"
    states = status["stage_states"]
    assert states["production"] == "completed"
    assert {states[name] for name in ("minimization", "nvt", "npt")} == {"skipped"}


def test_a_run_that_failed_in_nvt_says_nvt(tmp_path):
    status = _ended(tmp_path, {}, [("minimization", "completed"), ("nvt", "current"),
                                   ("nvt", "failed")], "error")
    assert status["stage"] == "nvt"
    assert status["stage_states"]["nvt"] == "failed"
    assert status["stage_states"]["production"] == "waiting"


def test_a_run_without_live_telemetry_took_every_stage_it_planned(tmp_path):
    status = _ended(tmp_path, {}, [], "ok")
    assert {status["stage_states"][name]
            for name in ("minimization", "nvt", "npt", "production")} == {"completed"}
