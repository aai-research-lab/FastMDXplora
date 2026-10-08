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

import pytest

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


def test_a_stopped_study_in_one_piece_is_counted_to_its_checkpoint(tmp_path):
    """Asked to stop, it stepped on to frame 28 and wrote its checkpoint
    there after its record's last word (26 frames): the page said 5.2 ps of
    a trajectory holding 5.6 ps."""
    from fastmdxplora.gui.telemetry import read_status, read_study_status

    root = _stopped_study(tmp_path / "study")
    read, raw = read_study_status(root), read_status(root)
    counted = {"current_step", "current_frame_count", "simulation_time_completed_ns",
               "current_checkpoint_path"}
    assert {k: v for k, v in read.items() if k not in counted} == \
        {k: v for k, v in raw.items() if k not in counted}
    assert (read["current_step"], read["current_frame_count"]) == (5000 + 2800, 28)
    assert abs(read["simulation_time_completed_ns"] - 0.0156) < 1e-12


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


def test_while_a_piece_runs_nothing_is_offered_to_carry_it_on(tmp_path, monkeypatch):
    """The Overview offered "Carry it on ... Run it" beside "Running": the
    resume keeps the record of its process in the piece, not the study."""
    from fastmdxplora.remedies import remedies_of
    from fastmdxplora.simulation import resume

    root = _piece(_stopped_study(tmp_path / "study"), status="running", step=3600,
                  minutes_ago=0)
    (root / "manifest.json").write_text(json.dumps({"phases": [
        {"name": "simulation", "status": "error", "message": STOP,
         "refusal": {"code": "simulation.run.stopped"}}]}), encoding="utf-8")
    monkeypatch.setattr(resume, "_still_running", lambda where: False)
    assert remedies_of(root), "a stopped study is offered its fix"
    monkeypatch.setattr(resume, "_still_running",
                        lambda where: Path(where).name == "segment-001")
    assert remedies_of(root) == []


def test_the_joined_analysis_runs_as_the_study_s_own(tmp_path):
    """Once the piece ended, the joined trajectory's analysis and report
    ran with the study reading "Completed", and "nothing is being recorded
    any more" beside Stop."""
    from fastmdxplora.gui.telemetry import TelemetryWriter, read_study_status

    root = _piece(_stopped_study(tmp_path / "study"), status="completed", step=7200,
                  minutes_ago=1, finished=True)
    writer = TelemetryWriter(root / "simulation", phases_only=True)
    writer.mark_stage("analysis", "current", status="running")
    during = read_study_status(root)
    assert during["status"] == "running" and during["stage"] == "analysis"
    assert during["stage_states"]["analysis"] == "current"
    assert during["stage_states"]["production"] == "completed"
    writer.mark_stage("analysis", "completed", status="running")
    writer.mark_stage("report", "completed", status="running")
    writer.write_status(status="completed", latest_error=None)
    after = read_study_status(root)
    assert after["status"] == "completed" and after["stage"] == "report"
    assert after["stage_states"]["report"] == "completed"


def test_the_phase_strip_says_the_simulation_carried_on(tmp_path):
    """It said "Simulation stopped" the whole carry-on through."""
    from fastmdxplora.gui import overview_view

    root = _piece(_stopped_study(tmp_path / "study"), status="running", step=3600,
                  minutes_ago=0)
    (root / "manifest.json").write_text(json.dumps({"phases": [
        {"name": "setup", "status": "ok"},
        {"name": "simulation", "status": "error", "message": STOP,
         "refusal": {"code": "simulation.run.stopped"}}]}), encoding="utf-8")
    said = {p["name"]: p["status"] for p in overview_view._phases(root)}
    assert said == {"setup": "ok", "simulation": "running"}


def test_the_narration_follows_the_piece(tmp_path):
    from fastmdxplora.gui.telemetry import read_events

    root = _piece(_stopped_study(tmp_path / "study"), status="running", step=3600,
                  minutes_ago=0)
    (root / "simulation" / "live_events.log").write_text(
        f"{_ago(20)}\tinfo\tProduction started\n\u2192 Aqvist et al. (doi:10)\n"
        f"{_ago(10)}\twarning\tStopped\n",
        encoding="utf-8")
    (root / "segment-001" / "simulation" / "live_events.log").write_text(
        f"{_ago(1)}\tinfo\tCarried on from step 2,800\n", encoding="utf-8")
    said = [event["message"] for event in read_events(root)]
    # A citation stays under the event it belongs to, not after every other.
    assert said == ["Production started", "\u2192 Aqvist et al. (doi:10)", "Stopped",
                    "Carried on from step 2,800"]


def test_a_piece_stopped_is_said_as_the_study(tmp_path, monkeypatch):
    """Stopped again while carried on: the fix card said the first stop's
    step, and the health card the piece's own steps with `fastmdx resume
    <study>/segment-001`, which carries the piece on as a study of its own."""
    from fastmdxplora.gui.telemetry import read_study_status
    from fastmdxplora.remedies import remedies_of
    from fastmdxplora.simulation import resume

    monkeypatch.setattr(resume, "_still_running", lambda where: False)
    root = _piece(_stopped_study(tmp_path / "study"), status="stopped", step=3800,
                  minutes_ago=0)
    record = json.loads((root / "segment-001" / "simulation" / "live_status.json").read_text())
    record["latest_error"] = (f"Production was stopped at step 3,800. `fastmdx resume "
                              f"{root / 'segment-001'}` carries it on.")
    _write(root / "segment-001" / "simulation" / "live_status.json", record)
    (root / "manifest.json").write_text(json.dumps({"phases": [
        {"name": "simulation", "status": "error", "message": STOP,
         "refusal": {"code": "simulation.run.stopped"}}]}), encoding="utf-8")
    said = read_study_status(root)["latest_error"]
    assert "segment-001" not in said and f"fastmdx resume {root}`" in said
    [fix] = remedies_of(root)
    assert fix.why == said and "segment-001" not in " ".join(fix.argv)


def test_the_viewer_s_last_frame_is_the_newest_piece_s(tmp_path):
    """The Viewer said "LAST FRAME production step 5,230" (the first piece's
    stop) beside an Overview saying Completed at step 17,000."""
    from fastmdxplora.gui.live_frames import LIVE_FRAME_FILE, LIVE_FRAME_INDEX_FILE
    from fastmdxplora.gui.server import _live_dir, _live_frame_index

    root = _piece(_stopped_study(tmp_path / "study"), status="completed", step=7200,
                  minutes_ago=1, finished=True)
    for folder, step in ((root / "simulation", 7622), (root / "segment-001" / "simulation", 7200)):
        (folder / LIVE_FRAME_FILE).write_text("ATOM\n", encoding="utf-8")
        _write(folder / LIVE_FRAME_INDEX_FILE, {
            "live_frame_available": True, "live_frame_index": step,
            "simulation_stage": "production", "simulation_time_ns": step * 2e-6})
    assert _live_dir(root) == root / "segment-001" / "simulation"
    said = _live_frame_index(root)
    # 5,000 steps of equilibration and 2,800 of production kept before it.
    assert said["live_frame_index"] == 5000 + 2800 + 7200
    assert said["simulation_time_ns"] == pytest.approx(15000 * 2e-6)


def test_the_joined_analysis_is_counted_and_the_simulation_done(tmp_path):
    """While the carried-on study's joined trajectory was analysed, the
    strip read "Simulation stopped" and the sidebar "Stage 5 of 5": the first
    piece's record had the analysis and report skipped."""
    from fastmdxplora.gui import overview_view
    from fastmdxplora.gui.telemetry import TelemetryWriter, read_study_status

    root = _piece(_stopped_study(tmp_path / "study"), status="completed", step=7200,
                  minutes_ago=1, finished=True)
    record = json.loads((root / "simulation" / "live_status.json").read_text())
    record["stage_states"].update(analysis="skipped", report="skipped")
    _write(root / "simulation" / "live_status.json", record)
    (root / "resolved_config.yml").write_text(
        "include_phase: [setup, simulation, analysis, report]\n", encoding="utf-8")
    (root / "manifest.json").write_text(json.dumps({"phases": [
        {"name": "simulation", "status": "error", "message": STOP,
         "refusal": {"code": "simulation.run.stopped"}}]}), encoding="utf-8")
    TelemetryWriter(root / "simulation", phases_only=True).mark_stage(
        "analysis", "current", status="running")
    states = read_study_status(root)["stage_states"]
    assert states["analysis"] == "current" and states["report"] == "waiting"
    said = {p["name"]: p["status"] for p in overview_view._phases(root)}
    assert said == {"simulation": "ok"}


def test_the_sidebar_counts_the_phases_the_record_says_are_running(tmp_path):
    """The stages came from the Manifest alone, which a stopped first piece
    left at the simulation: "Stage 5 of 5" through the joined trajectory's
    analysis, and after Analyze again on a run stopped in its own analysis
    "Stage 1 of 1"."""
    from fastmdxplora.gui.telemetry import TelemetryWriter, run_stages

    root = _piece(_stopped_study(tmp_path / "study"), status="completed", step=7200,
                  minutes_ago=1, finished=True)
    (root / "manifest.json").write_text(json.dumps({"phases": [
        {"name": "setup", "status": "ok"},
        {"name": "simulation", "status": "error", "message": STOP,
         "refusal": {"code": "simulation.run.stopped"}}]}), encoding="utf-8")
    assert run_stages(root)[-1] == "production"
    TelemetryWriter(root / "simulation", phases_only=True).mark_stage(
        "analysis", "current", status="running")
    stages = run_stages(root)
    assert stages[:5] == ["setup", "minimization", "nvt", "npt", "production"]
    assert stages[5:] == ["analysis"]
    # A rerun's own Manifest names only the phase it ran.
    (root / "manifest.json").write_text(json.dumps({"phases": [
        {"name": "analysis", "status": "ok"}]}), encoding="utf-8")
    assert run_stages(root)[:5] == ["setup", "minimization", "nvt", "npt", "production"]


def test_a_carried_on_study_stopped_while_analysed_again_reads_stopped(tmp_path):
    """Stopped in Analyze again after it had been carried on to its end, the
    study read "Completed" with half its analysis, its health card hidden."""
    from fastmdxplora.gui.exploration import _record_the_stop
    from fastmdxplora.gui.telemetry import TelemetryWriter, read_study_status

    root = _piece(_stopped_study(tmp_path / "study"), status="completed", step=7200,
                  minutes_ago=1, finished=True)
    writer = TelemetryWriter(root / "simulation", phases_only=True)
    writer.mark_stage("analysis", "current", status="running")
    assert read_study_status(root)["status"] == "running"
    _record_the_stop(root)
    stopped = read_study_status(root)
    assert stopped["status"] == "stopped" and stopped["stage"] == "analysis"
    assert "in the analysis" in stopped["latest_error"]
    assert "Production was stopped" not in stopped["latest_error"]


def test_a_study_stopped_again_while_carried_on_is_said_to_its_checkpoint(tmp_path):
    """Stopped a second time, it said the record's lagging step on the whole
    run's clock as production ("step 17,541 of 20,000 (0.035 of 0.040 ns)")
    beside 25.4 ps in 2 pieces and a price from step 17,700."""
    from fastmdxplora.gui.telemetry import read_study_status

    root = _piece(_stopped_study(tmp_path / "study"), status="stopped", step=3741,
                  minutes_ago=0)
    side = root / "segment-001" / "simulation" / "checkpoint.chk.json"
    _write(side, {"stage": "production", "step": 3800, "finished": False,
                  "trajectory_interval_steps": 100})
    status = read_study_status(root)
    assert status["current_step"] == 5000 + 2800 + 3800
    assert status["current_frame_count"] == 28 + 38
    said = status["latest_error"]
    assert "step 6,600 of 10,000 (13.2 ps of 20 ps)" in said, said
    assert " ns" not in said
