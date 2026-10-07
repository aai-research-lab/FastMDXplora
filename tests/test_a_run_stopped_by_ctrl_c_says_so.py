"""A run stopped by Ctrl+C says it stopped, and an interrupted one is offered
its fix.

A second Ctrl+C (or one outside production) arrives as a KeyboardInterrupt,
which went past every handler that turns a failure into a record of one: no
end was written, the record of the run's process was taken away as it
exited, and the study read Running until a day had passed without a word.
It is the stop the first Ctrl+C asks for, recorded as one, so the page says
where it stopped and `fastmdx resume` carries it on.

A study that ended without saying so (its machine restarted, or a scheduler
ended its job) read Interrupted with nothing to do about it: What would fix
it offered nothing, though `fastmdx resume` is its fix.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from fastmdxplora.gui.telemetry import STATUS_FILE, TelemetryWriter, analyze_health, read_status
from fastmdxplora.orchestrator import RUN_PROCESS_FILE, FastMDXplora, PhaseResult, this_machine
from fastmdxplora.simulation.runner import STOPPED_CODE
from tests.test_every_refusal_says_its_fix_and_price import _config, _speed, _stopped_at


def _study(tmp_path: Path) -> FastMDXplora:
    pdb = tmp_path / "p.pdb"
    pdb.write_text("END\n", encoding="utf-8")
    return FastMDXplora(system=str(pdb), output_dir=str(tmp_path / "run"))


class TestCtrlC:
    def test_the_phase_records_it_as_a_stop(self, tmp_path, monkeypatch) -> None:
        study = _study(tmp_path)

        def interrupted(**_kwargs):
            raise KeyboardInterrupt

        monkeypatch.setattr(FastMDXplora, "_resolve_phase_runner",
                            staticmethod(lambda phase: interrupted))
        result = study._run_phase("simulation", {})
        assert result.status == "error"
        assert result.refusal["code"] == STOPPED_CODE and result.refusal["retryable"]
        assert result.message.startswith("The run was stopped by Ctrl+C in its simulation phase")
        assert "fastmdx resume" in result.message
        assert result.refusal["details"]["signal"] == "SIGINT"

    def test_the_command_line_says_it_was_interrupted(self) -> None:
        from fastmdxplora.cli.main import _stopped_by_ctrl_c
        from fastmdxplora.orchestrator import RunResult

        def run(refusal):
            return RunResult(run_id="s1", system="x", status="error", phases=[
                PhaseResult(name="simulation", status="error", refusal=refusal)])

        assert _stopped_by_ctrl_c([run({"code": STOPPED_CODE, "details": {"signal": "SIGINT"}})])
        # The first Ctrl+C in production, recorded by the runner.
        assert _stopped_by_ctrl_c([run({"code": STOPPED_CODE,
                                        "details": {"details": {"signal": "SIGINT"}}})])
        assert not _stopped_by_ctrl_c([run({"code": STOPPED_CODE,
                                            "details": {"details": {"signal": "SIGTERM"}}})])
        assert not _stopped_by_ctrl_c([run({"code": "setup.structure.unreadable"})])

    def test_the_live_record_says_stopped_where_it_was(self, tmp_path) -> None:
        root = tmp_path / "run"
        writer = TelemetryWriter(root / "simulation")
        writer.mark_stage("production", "current", status="running")
        stopped = PhaseResult(name="simulation", status="error",
                              message="The run was stopped by Ctrl+C.",
                              refusal={"code": STOPPED_CODE, "retryable": True})
        FastMDXplora._mark_dashboard_phase_end(writer, "simulation", stopped)
        status = read_status(root)
        assert status["status"] == "stopped"
        # The stage it stopped in stays the one it was in, not a failure.
        assert status["stage_states"]["production"] == "current"
        assert status["stage"] == "production"

    def test_a_study_stopped_so_writes_its_end(self, tmp_path, monkeypatch) -> None:
        """The whole of it: the study returns, its Manifest says where it
        stopped, and its live record says stopped."""
        study = _study(tmp_path)
        ran: list[str] = []

        def runner_for(phase):
            def run(**_kwargs):
                ran.append(phase)
                if phase == "simulation":
                    raise KeyboardInterrupt
                return []
            return run

        monkeypatch.setattr(FastMDXplora, "_resolve_phase_runner", staticmethod(runner_for))
        (result,) = study.explore()
        root = tmp_path / "run"
        assert ran == ["setup", "simulation"]
        assert result.status == "error"
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        simulation = next(p for p in manifest["phases"] if p["name"] == "simulation")
        assert simulation["refusal"]["code"] == STOPPED_CODE
        assert read_status(root)["status"] == "stopped"

    def test_a_failure_is_still_a_failure(self, tmp_path) -> None:
        root = tmp_path / "run"
        writer = TelemetryWriter(root / "simulation")
        writer.mark_stage("setup", "current", status="running")
        failed = PhaseResult(name="setup", status="error", message="No structure.",
                             refusal={"code": "setup.structure.unreadable"})
        FastMDXplora._mark_dashboard_phase_end(writer, "setup", failed)
        assert read_status(root)["status"] == "failed"

    def test_the_page_is_told_it_stopped_not_that_it_failed(self) -> None:
        health = analyze_health({"status": "stopped",
                                 "latest_error": "The run was stopped by Ctrl+C."}, [])
        assert health["state"] == "stopped" and health["headline"] == "Stopped"
        assert "fastmdx resume" in health["explanation"]


def _going(root: Path, *, written: datetime, process: dict | None) -> None:
    simulation = root / "simulation"
    simulation.mkdir(parents=True, exist_ok=True)
    (simulation / STATUS_FILE).write_text(json.dumps({
        "status": "running", "stage": "production",
        "last_update_timestamp": written.isoformat()}), encoding="utf-8")
    if process is not None:
        (root / RUN_PROCESS_FILE).write_text(json.dumps(process), encoding="utf-8")


def _a_gone_process() -> int:
    ended = subprocess.Popen([sys.executable, "-c", "pass"])
    ended.wait()
    return ended.pid


class TestAnInterruptedStudy:
    def test_its_process_gone_it_is_carried_on_from_here(self, tmp_path) -> None:
        from fastmdxplora.gui.fixes_view import runnable
        from fastmdxplora.remedies import remedies_of

        root = tmp_path / "study"
        _config(root)
        _speed(root)
        _stopped_at(root, 100_000)
        _going(root, written=datetime.now(timezone.utc), process={
            "pid": _a_gone_process(), "argv": ["fastmdx", "explore"],
            "started_at": datetime.now(timezone.utc).isoformat(), **this_machine()})
        (remedy,) = remedies_of(root)
        assert remedy.code == "simulation.run.interrupted"
        assert remedy.why.startswith("The run ended without recording its end")
        assert remedy.command.startswith("fastmdx resume ")
        assert remedy.price.production_ns == pytest.approx(0.3)
        assert runnable(remedy)

    def test_a_day_of_silence_is_the_person_s_to_decide(self, tmp_path) -> None:
        from fastmdxplora.gui.fixes_view import runnable
        from fastmdxplora.remedies import remedies_of

        root = tmp_path / "study"
        _config(root)
        _going(root, written=datetime.now(timezone.utc) - timedelta(days=2), process=None)
        (remedy,) = remedies_of(root)
        assert remedy.code == "simulation.run.interrupted"
        assert "look there first" in remedy.why
        assert remedy.command.startswith("fastmdx resume ")
        assert remedy.decision and not runnable(remedy)

    def test_a_run_still_writing_needs_nothing(self, tmp_path) -> None:
        from fastmdxplora.remedies import remedies_of

        root = tmp_path / "study"
        _config(root)
        _going(root, written=datetime.now(timezone.utc), process=None)
        assert remedies_of(root) == []

    def test_the_sidebar_offers_it(self) -> None:
        css = (Path(__file__).resolve().parents[1] / "src" / "fastmdxplora" / "gui"
               / "static" / "dashboard.css").read_text(encoding="utf-8")
        assert '.sidebar-progress[data-run="interrupted"] .sidebar-fix' in css
