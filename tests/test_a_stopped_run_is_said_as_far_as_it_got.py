"""A run that ended short is said as far as it got, and nothing more.

Stopped in production, a run's phase strip said "Simulation failed" beside a
health card saying "Stopped", and its fix said "no speed has been measured
here" beside the speed its live record measured. A run that ended before
production said "0 ps production after 10 ps of equilibration", the
equilibration it planned said as done. The sidebar's time left was read off
the run's whole time, setup included, and stayed on screen once the run had
stopped. Analysing or writing a study's report again wrote that run's
phases into the simulation's record: every stage before them skipped, its
time the hours since the simulation began.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_a_resumed_study_reads_as_one_run import _piece, _stopped_study, _write


def test_a_stopped_phase_is_said_as_stopped(tmp_path):
    from fastmdxplora.gui.overview_view import _phases

    root = tmp_path / "study"
    _write(root / "manifest.json", {"phases": [
        {"name": "setup", "status": "ok"},
        {"name": "simulation", "status": "error",
         "refusal": {"code": "simulation.run.stopped", "message": "Stopped."}},
        {"name": "analysis", "status": "error", "refusal": {"code": "analysis.failed"}}]})
    assert [p["status"] for p in _phases(root)] == ["ok", "stopped", "error"]


def test_a_stopped_run_s_fix_is_priced_at_the_speed_it_ran(tmp_path):
    from fastmdxplora.remedies import _speed

    root = _stopped_study(tmp_path / "study")
    path = root / "simulation" / "live_metrics.csv"
    lines = path.read_text().splitlines()
    lines[0] += ",speed"
    lines = [lines[0]] + [line + (",4.32" if "Production" in line else ",0") for line in lines[1:]]
    path.write_text("\n".join(lines) + "\n")
    _write(root / "simulation" / "live_status.json",
           {**json.loads((root / "simulation" / "live_status.json").read_text()),
            "platform": "CPU"})
    seconds, platform = _speed(root, root)
    assert seconds is not None and abs(seconds - 86400 / 4.32) < 1e-6
    assert platform == "CPU"


def test_the_price_is_the_production_s_own_rate(tmp_path):
    """The live record's speed is an average from the run's start, setup and
    minimisation in it: a stop's remainder was priced at about 39 minutes
    where the sidebar's time left said 13."""
    from datetime import datetime, timedelta, timezone

    from fastmdxplora.remedies import _speed

    root = _stopped_study(tmp_path / "study")
    start = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
    lines = ["timestamp,stage,step,simulation_time_ns,temperature,speed"]
    for k in range(6):
        when = (start + timedelta(seconds=10 * k)).isoformat()
        lines.append(f"{when},Production,{5000 + 300 * k},{(5000 + 300 * k) * 2e-6},300,1.0")
    (root / "simulation" / "live_metrics.csv").write_text("\n".join(lines) + "\n")
    seconds, _ = _speed(root, root)
    # 300 steps of 2 fs every 10 s: 0.0006 ns in 10 s.
    assert seconds == pytest.approx(10 / 0.0006)


def test_a_run_that_ended_before_production_says_so(tmp_path):
    from fastmdxplora.gui.simulated_time import equilibration_said, simulated_times

    status = {"status": "failed", "stage": "nvt", "current_step": 1000, "timestep_fs": 2.0,
              "nvt_steps_planned": 2500, "npt_steps_planned": 2500,
              "production_steps_planned": 10000}
    times = simulated_times(tmp_path, status)
    assert times["ended_in"] == "nvt"
    assert equilibration_said(times) == (
        "ended in NVT equilibration, 2 ps of the 10 ps of equilibration planned")
    running = simulated_times(tmp_path, {**status, "status": "running"})
    assert running["ended_in"] is None


def test_analysing_again_leaves_the_simulation_s_record(tmp_path, monkeypatch):
    from fastmdxplora.orchestrator import FastMDXplora

    root = _stopped_study(tmp_path / "study")
    before = (root / "simulation" / "live_status.json").read_text()
    run = object.__new__(FastMDXplora)
    run.output_dir = root
    run.options = {}
    monkeypatch.setenv("FASTMDX_DASHBOARD_ACTIVE", "1")
    monkeypatch.setenv("FASTMDX_DASHBOARD_OUTPUT", str(root))
    again = run._dashboard_writer({}, ["analysis", "report"])
    assert again is not None and again.phases_only
    assert not run._dashboard_writer({}, ["simulation"]).phases_only
    assert (root / "simulation" / "live_status.json").read_text() == before
    # Its phases marked as they run, and nothing else of the record touched;
    # once they end, the run is said as it ended (here, stopped).
    import json

    from fastmdxplora.orchestrator import PhaseResult

    record = json.loads(before)
    FastMDXplora._mark_dashboard_phase_start(again, "analysis", {})
    during = json.loads((root / "simulation" / "live_status.json").read_text())
    assert during["status"] == "running" and during["stage"] == "analysis"
    assert during["stage_states"]["analysis"] == "current"
    FastMDXplora._mark_dashboard_phase_end(again, "analysis", PhaseResult(
        name="analysis", status="ok"))
    again.write_status(status="completed", latest_error=None)
    after = json.loads((root / "simulation" / "live_status.json").read_text())
    assert after["stage_states"]["analysis"] == "completed"
    for key in ("status", "stage", "latest_error", "run_started_at", "elapsed_wall_time_s",
                "last_update_timestamp"):
        assert after.get(key) == record.get(key), key
    assert {k: v for k, v in after["stage_states"].items() if k != "analysis"} == {
        k: v for k, v in record["stage_states"].items() if k != "analysis"}
    # A trajectory analysed, with no simulation recorded, still has one.
    fresh = tmp_path / "trajectory"
    fresh.mkdir()
    run.output_dir = fresh
    monkeypatch.setenv("FASTMDX_DASHBOARD_OUTPUT", str(fresh))
    assert run._dashboard_writer({}, ["analysis"]) is not None


def test_a_resumed_study_whose_whole_was_analysed_ends_in_its_report(tmp_path):
    from fastmdxplora.gui.telemetry import status_as_it_stands

    root = _piece(_stopped_study(tmp_path / "study"), status="completed", step=7200,
                  minutes_ago=5, finished=True)
    _write(root / "manifest.json", {"phases": [
        {"name": "setup", "status": "ok"}, {"name": "simulation", "status": "ok"},
        {"name": "analysis", "status": "ok"}, {"name": "report", "status": "ok"}]})
    status = status_as_it_stands(root)
    assert status["status"] == "completed" and status["stage"] == "report"
    assert status["stage_states"]["analysis"] == status["stage_states"]["report"] == "completed"


def test_the_time_left_is_said_only_while_it_runs():
    script = (Path(__file__).parents[1] / "src" / "fastmdxplora" / "gui" / "static"
              / "dashboard.js").read_text(encoding="utf-8")
    assert 'setText("topbar-eta", run === "running" ? computeETA(status) : "\\u2014")' in script
    # From the speed of its recent samples, not its whole time so far.
    assert "function stepsPerSecond()" in script
    assert "(total - step) / rate" in script


@pytest.mark.parametrize("stage, carried", [("minimization", False), ("nvt", False),
                                            ("production", True)])
def test_a_stop_is_explained_by_where_it_stopped(stage, carried):
    """A stop in minimisation was said to have ended "where it can be
    carried on" under a message saying it wrote no checkpoint."""
    from fastmdxplora.gui.telemetry import analyze_health

    said = analyze_health({"status": "stopped", "stage": stage,
                           "latest_error": "It stopped."}, [])["explanation"]
    assert ("where it can be carried on" in said) is carried
    assert ("runs it again from the start" in said) is not carried


def test_a_stop_says_what_asked_for_it():
    from fastmdxplora.simulation.runner import _who_asked

    assert _who_asked("SIGINT") == "Ctrl+C"
    assert "SIGTERM" in _who_asked("SIGTERM") and _who_asked("SIGTERM") != "SIGTERM"


def test_time_left_is_read_from_the_steps_taken_since_the_last_pause():
    """At step 1,000 of 7,000 it said 39 minutes (the whole run's time, a
    5-minute minimisation in it) where 5 were left; after a resume it went
    13, 24, 38 and 27 minutes, the time between the pieces in its rate."""
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("no node")
    script = (Path(__file__).parents[1] / "src" / "fastmdxplora" / "gui" / "static"
              / "dashboard.js").read_text(encoding="utf-8")

    def function(name):
        start = script.index(f"  function {name}(")
        return script[start:script.index("\n  }\n", start) + 4]

    def at(seconds):
        return f"new Date(Date.UTC(2026, 9, 8, 12, 0, {seconds})).toISOString()"

    # Minimisation 5 minutes long, then a step every 10 s: one stepped
    # sample, and one before the steps began.
    early = f"[{{step: 0, timestamp: {at(0)}}}, {{step: 0, timestamp: {at(300)}}}, " \
            f"{{step: 1000, timestamp: {at(310)}}}]"
    # Two pieces with ten minutes between them, 100 steps a second in each.
    pieces = ", ".join([f"{{step: {s * 100}, timestamp: {at(s)}}}" for s in range(1, 6)]
                       + [f"{{step: {500 + s * 100}, timestamp: {at(600 + s)}}}"
                          for s in range(1, 6)])
    probe = "\n".join([
        "const state = {metrics: []};", function("fmtDuration"), function("computeETA"),
        function("stepsPerSecond"),
        f"state.metrics = {early};",
        "const first = computeETA({current_step: 1000, total_planned_steps: 7000,"
        " elapsed_wall_time_s: 330});",
        f"state.metrics = [{pieces}];",
        "const second = stepsPerSecond();",
        "console.log(JSON.stringify([first, second]));"])
    said = subprocess.run([node, "-e", probe], capture_output=True, text=True, check=True)
    first, rate = json.loads(said.stdout)
    assert first == "1m 0s", first
    assert rate == pytest.approx(100.0)


def _completed(root: Path) -> Path:
    _stopped_study(root)
    record = json.loads((root / "simulation" / "live_status.json").read_text())
    record.update(status="completed", latest_error=None, current_step=15000,
                  run_started_at="2026-10-05T16:40:00+00:00",
                  last_update_timestamp="2026-10-05T16:59:00+00:00",
                  elapsed_wall_time_s=1101.092)
    record["stage_states"].update(production="completed", analysis="completed",
                                  report="completed")
    _write(root / "simulation" / "live_status.json", record)
    return root


def test_a_rerun_stopped_is_not_carried_on_and_keeps_the_run_s_record(tmp_path, monkeypatch):
    """Analyze again stopped from the GUI was offered "Carry it on ...
    fastmdx resume", which made an empty piece and failed; the stop wrote the
    record's time as 78 hours and its date as today's."""
    from fastmdxplora.gui.exploration import _record_the_stop
    from fastmdxplora.gui.telemetry import (
        STOPPED_IN_A_PHASE_AFTER_EXPLANATION, TelemetryWriter, analyze_health, read_status)
    from fastmdxplora.remedies import remedies_of
    from fastmdxplora.simulation import resume

    monkeypatch.setattr(resume, "_still_running", lambda where: False)
    root = _completed(tmp_path / "study")
    before = read_status(root)
    TelemetryWriter(root / "simulation", phases_only=True).mark_stage(
        "analysis", "current", status="running")
    during = read_status(root)
    health = analyze_health(during, [])
    assert health["state"] == "ok" and health["headline"] == "Analysing"
    _record_the_stop(root)
    stopped = read_status(root)
    assert stopped["status"] == "stopped" and stopped["stage"] == "analysis"
    for key in ("run_started_at", "last_update_timestamp", "elapsed_wall_time_s"):
        assert stopped[key] == before[key], key
    assert remedies_of(root) == []
    assert analyze_health(stopped, [])["explanation"] == STOPPED_IN_A_PHASE_AFTER_EXPLANATION
    # Analysed again to its end, it reads as the run ended.
    again = TelemetryWriter(root / "simulation", phases_only=True)
    again.mark_stage("analysis", "current", status="running")
    again.mark_stage("analysis", "completed", status="running")
    again.write_status(status="completed", latest_error=None)
    after = read_status(root)
    assert after["status"] == "completed" and after.get("latest_error") is None
    assert "ended_as" not in after


def test_a_run_stopped_in_its_own_analysis_reads_completed_once_analysed_again(tmp_path):
    """Stopped while its own analysis ran, then analysed again to the end,
    the study read "Running / Analysing" for good: the stop kept the record
    as it stood, running in the analysis, as how the run had ended."""
    from fastmdxplora.gui.exploration import _record_the_stop
    from fastmdxplora.gui.telemetry import TelemetryWriter, read_status

    root = _completed(tmp_path / "study")
    record = json.loads((root / "simulation" / "live_status.json").read_text())
    record.update(status="running", stage="analysis")
    record["stage_states"].update(analysis="current", report="waiting")
    _write(root / "simulation" / "live_status.json", record)
    _record_the_stop(root)
    stopped = read_status(root)
    assert stopped["status"] == "stopped" and stopped["stage"] == "analysis"
    again = TelemetryWriter(root / "simulation", phases_only=True)
    again.mark_stage("analysis", "current", status="running")
    again.mark_stage("analysis", "completed", status="running")
    again.write_status(status="completed", latest_error=None)
    after = read_status(root)
    assert after["status"] == "completed" and after.get("latest_error") is None
    assert "ended_as" not in after


def test_analysing_again_from_the_command_line_keeps_the_phases_too(tmp_path, monkeypatch):
    """Only the GUI's run kept them: `fastmdx explore --include-phase
    analysis report` from a terminal wrote `[analysis, report]` for the
    whole run, and a run killed in its analysis, or one from Python, left
    it so."""
    import yaml

    from fastmdxplora.orchestrator import FastMDXplora, PhaseResult

    monkeypatch.delenv("FASTMDX_DASHBOARD_ACTIVE", raising=False)
    root = _completed(tmp_path / "study")
    (root / "resolved_config.yml").write_text(
        "system: 1L2Y\ninclude_phase: [setup, simulation, analysis, report]\n",
        encoding="utf-8")
    seen = []

    def phase(self, name, options):
        # What the record says while the phase runs, as a run killed here
        # would leave it.
        seen.append(yaml.safe_load((root / "resolved_config.yml").read_text(
            encoding="utf-8"))["include_phase"])
        return PhaseResult(name=name, status="ok", output_dir=root / name)

    monkeypatch.setattr(FastMDXplora, "_run_phase", phase)
    monkeypatch.setattr(FastMDXplora, "_refuse_to_overwrite", lambda *a, **k: None)
    FastMDXplora("1L2Y", output_dir=str(root)).explore(include_phase=["analysis", "report"])
    assert seen and all(kept == ["setup", "simulation", "analysis", "report"] for kept in seen)


def test_a_study_of_several_runs_analysed_again_keeps_its_phases(tmp_path):
    """The root record of a sweep analysed again said `[analysis, report]`
    while it ran, and after a run from Python too."""
    import yaml

    from fastmdxplora.batch.explorer import BatchExplorer

    config = tmp_path / "sweep.yml"
    config.write_text("systems:\n  - system: 1L2Y\nsweep:\n  simulation.random_seed: [11, 12]\n",
                      encoding="utf-8")
    out = tmp_path / "out"
    first = BatchExplorer(config=str(config), output_dir=str(out))
    (out / "runs").mkdir(parents=True)
    first._write_study_config()
    assert yaml.safe_load((out / "resolved_config.yml").read_text()).get("include_phase") in (
        None, ["setup", "simulation", "analysis", "report"])
    again = BatchExplorer(config=str(config), output_dir=str(out))
    again._raw["include_phase"] = ["analysis", "report"]
    # Before any run simulated, the study is what it now asks for.
    again._write_study_config()
    assert yaml.safe_load((out / "resolved_config.yml").read_text()).get("include_phase") == [
        "analysis", "report"]
    first._write_study_config()
    for run in ("seed-11", "seed-12"):
        (out / "runs" / run / "simulation").mkdir(parents=True)
        (out / "runs" / run / "simulation" / "live_status.json").write_text(
            '{"status": "completed"}', encoding="utf-8")
    again._write_study_config()
    kept = yaml.safe_load((out / "resolved_config.yml").read_text()).get("include_phase")
    assert kept != ["analysis", "report"] and (kept is None or "simulation" in kept)


def test_analysing_again_keeps_the_phases_the_study_ran(tmp_path):
    """Analyze again wrote `include_phase: [analysis, report]` into the
    study's resolved config: All studies then called the run a trajectory
    analysed, Compare listed it as a difference, and the config that says
    how to reproduce the study reproduced only its analysis."""
    import yaml

    from fastmdxplora.gui.workspace import card_of
    from fastmdxplora.orchestrator import FastMDXplora

    root = _completed(tmp_path / "study")
    (root / "resolved_config.yml").write_text(
        "system: 1L2Y\ninclude_phase: [setup, simulation, analysis, report]\n",
        encoding="utf-8")
    run = object.__new__(FastMDXplora)
    run.output_dir = root
    assert run._recorded_phase_selection() == (["setup", "simulation", "analysis", "report"], None)
    # A study recorded as its setup and simulation, then analysed: the
    # analysis joins its record, not left out of it.
    (root / "resolved_config.yml").write_text(
        "system: 1L2Y\ninclude_phase: [setup, simulation]\n", encoding="utf-8")
    assert run._recorded_phase_selection(["analysis"]) == (
        ["setup", "simulation", "analysis"], None)
    (root / "resolved_config.yml").write_text(
        "system: 1L2Y\ninclude_phase: [setup, simulation, analysis, report]\n",
        encoding="utf-8")
    run.system, run.verbose, run.options = "1L2Y", False, {}
    run._config_include, run._config_exclude = ["analysis", "report"], None
    run._resolved_include, run._resolved_exclude = ["analysis", "report"], None
    run._recorded_phases = run._recorded_phase_selection()
    run._write_resolved_config()
    kept = yaml.safe_load((root / "resolved_config.yml").read_text(encoding="utf-8"))
    assert kept["include_phase"] == ["setup", "simulation", "analysis", "report"]
    # A study rewritten so before this is still read as the run it was.
    (root / "resolved_config.yml").write_text(
        "system: 1L2Y\ninclude_phase: [analysis, report]\n", encoding="utf-8")
    assert card_of(root)["kind"] != "a trajectory analysed"
