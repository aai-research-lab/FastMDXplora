"""The Problems panel says what the study's records say of it as it stands.

A stop carried on is not a problem; the report's refusals and its verdict
that an observable has not equilibrated are; each warning the run or its
log recorded is listed once, and a record a run writes that is missing is
said as unavailable.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import pytest


def problems_payload(root, **kwargs):
    # Imported per test, so each one runs (and fails) on a commit without
    # the Problems reader.
    from fastmdxplora.gui.problems_view import problems_payload as read

    return read(root, **kwargs)


def _write(root: Path, relative: str, value: object) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _events(folder: Path, *lines: tuple[str, str]) -> None:
    from fastmdxplora.gui.telemetry import TelemetryWriter

    writer = TelemetryWriter(folder)
    for level, message in lines:
        writer.event(message, level=level)


def _logged(root: Path, *records: tuple[int, str]) -> None:
    """Lines in a study's log, as the orchestrator's file log writes them."""
    from fastmdxplora.utils.logging import attach_file_logger, detach_file_logger, get_logger

    path = root / "fastmdxplora.log"
    root.mkdir(parents=True, exist_ok=True)
    attach_file_logger(path, level=logging.DEBUG)
    try:
        for level, message in records:
            get_logger("orchestrator").log(level, message)
    finally:
        detach_file_logger(path)


def _phase(name: str, status: str, message: str = "", code: str = "") -> dict:
    """A phase as the orchestrator records it (`PhaseResult.to_dict`)."""
    from fastmdxplora.orchestrator import PhaseResult
    from fastmdxplora.refusals import Refusal

    refusal = Refusal(code=code, message=message).as_dict() if code else {}
    return PhaseResult(name=name, status=status, message=message, refusal=refusal).to_dict()


PADDING = ("1.2 nm of padding on a solute 0.6 nm across makes a dodecahedron about "
           "1.70 nm at its narrowest. Setup will grow the padding to about 1.40 nm by "
           "itself, and say so; set that here to have the config say what runs.")
HEARD = ("SIGTERM received: production will stop at the next frame, where it can be "
         "carried on, if that can be reached within 20 s. Send it again to stop now.")


def _stopped(root: Path, code: str = "simulation.run.stopped", heard: str = HEARD) -> Path:
    """Stopped in production, as the runner, the orchestrator and the log
    record it: the stop's words as a warning, the phase stopped, the
    manifest's refusal; then the study's records of a resume."""
    from tests.test_a_resumed_study_reads_as_one_run import STOP, _stopped_study

    _stopped_study(root)
    _logged(root, (logging.WARNING, PADDING), (logging.WARNING, heard))
    _events(root / "simulation", ("info", "Production started"), ("warning", STOP),
            ("warning", "Simulation phase stopped"))
    _write(root, "manifest.json", {"phases": [
        _phase("setup", "ok", "Phase 'setup' completed."),
        _phase("simulation", "error", STOP, code)]})
    return root


def _carrying_it_on(root: Path, *, status: str, step: int, finished: bool = False) -> None:
    from fastmdxplora.orchestrator import RUN_PROCESS_FILE, this_machine
    from tests.test_a_resumed_study_reads_as_one_run import _piece

    _piece(root, status=status, step=step, minutes_ago=0.1, finished=finished)
    _events(root / "segment-001" / "simulation", ("info", "Production started"))
    _logged(root / "segment-001", (logging.INFO, "Plan: simulation"))
    if status == "running":
        # The resume's own process, alive.
        _write(root, f"segment-001/{RUN_PROCESS_FILE}", {"pid": os.getpid(), **this_machine()})


@pytest.mark.parametrize("code", ["simulation.run.stopped", "simulation.run.interrupted"])
def test_a_stop_being_carried_on_is_not_a_problem(tmp_path, code):
    from fastmdxplora.gui.telemetry import analyze_health, read_metrics, status_as_it_stands

    root = _stopped(tmp_path / "study", code)
    _carrying_it_on(root, status="running", step=3000)
    shown = status_as_it_stands(root)
    assert shown["status"] == "running"
    assert analyze_health(shown, read_metrics(root))["state"] == "ok"
    payload = problems_payload(root, admit_evidence=True)
    # Only the setup's own warning: no stop, no interruption, and none of
    # the stop's words, which are what is being acted on.
    assert [(item["title"], item["reason"]) for item in payload["items"]] == [
        ("Warning in the study's log", PADDING)]
    assert payload["unavailable"] == []
    assert "segment-001/simulation/live_status.json" in payload["read"]


@pytest.mark.parametrize("heard", [
    "Stop asked from the GUI: production will stop at the next frame, where it can be "
    "carried on, if that can be reached within 20 s.",
    "Stop asked from the GUI: each run in progress ends where it can be carried on (in "
    "production, at its next frame if that is within 20 s), and no further run starts."])
def test_a_stop_asked_from_the_gui_is_not_a_problem_of_its_own(tmp_path, heard):
    """The GUI's own process asks its run to stop without a signal, and
    says so as a signal's stop does: those words were listed beside the
    stop as a warning of their own."""
    root = _stopped(tmp_path / "study", heard=heard)
    _carrying_it_on(root, status="running", step=3000)
    payload = problems_payload(root, admit_evidence=True)
    assert [(item["title"], item["reason"]) for item in payload["items"]] == [
        ("Warning in the study's log", PADDING)]


def test_a_stop_carried_on_to_the_end_leaves_no_problem_behind(tmp_path):
    from fastmdxplora.gui.telemetry import analyze_health, read_metrics, status_as_it_stands
    from fastmdxplora.simulation.resume import _production_carried_on

    root = _stopped(tmp_path / "study")
    _carrying_it_on(root, status="completed", step=7200, finished=True)
    # The joined trajectory analysed and reported in the study's own
    # folder, and its manifest rewritten as resume.py rewrites it.
    first = json.loads((root / "simulation/live_status.json").read_text(encoding="utf-8"))
    first.update(status="completed", stage="report")
    _write(root, "simulation/live_status.json", first)
    _production_carried_on(root, {"segments": [0, 1], "frames": 100})
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["phases"][1]["status"] == "ok" and manifest["phases"][1]["carried_on_from"]
    shown = status_as_it_stands(root)
    assert shown["status"] == "completed"
    assert analyze_health(shown, read_metrics(root))["message"] == "Completed"
    payload = problems_payload(root, admit_evidence=True)
    assert [item["reason"] for item in payload["items"]] == [PADDING]
    said = json.dumps(payload)
    assert "SIGTERM" not in said and "phase stopped" not in said


@pytest.mark.parametrize("again", ["stopped", "interrupted"])
def test_a_piece_that_ended_again_is_one_problem_said_as_the_study_stands(tmp_path, again):
    from fastmdxplora.gui.telemetry import analyze_health, read_metrics, status_as_it_stands

    root = _stopped(tmp_path / "study")
    _carrying_it_on(root, status="stopped" if again == "stopped" else "running", step=1000)
    if again == "interrupted":
        import subprocess
        import sys

        from fastmdxplora.orchestrator import RUN_PROCESS_FILE, this_machine

        # The resume's process, gone without a word.
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()
        _write(root, f"segment-001/{RUN_PROCESS_FILE}", {"pid": child.pid, **this_machine()})
    shown = status_as_it_stands(root)
    assert shown["status"] == again
    health = analyze_health(shown, read_metrics(root))
    items = problems_payload(root, admit_evidence=True)["items"]
    # The first piece's stop was carried on; the study stands as its piece
    # ended, said once, as the Overview says it, with the piece's own
    # record as its evidence, not the first stop's.
    assert [(item["id"], item["title"], item["reason"], item["file"]) for item in items
            if item["title"] != "Warning in the study's log"] == [
        (f"health:{again}", health["headline"], health["message"],
         "segment-001/simulation/live_status.json")]


def test_a_stop_as_it_stands_folds_in_its_own_warnings(tmp_path):
    from tests.test_a_resumed_study_reads_as_one_run import STOP

    root = _stopped(tmp_path / "study")
    payload = problems_payload(root, admit_evidence=True)
    assert [(item["title"], item["reason"]) for item in payload["items"]] == [
        ("Simulation stopped", STOP), ("Warning in the study's log", PADDING)]
    assert payload["items"][0]["code"] == "simulation.run.stopped"


def test_a_report_format_not_produced_is_listed_with_its_code_and_remedy(tmp_path):
    from fastmdxplora.remedies import remedy_for

    reason = ("Rendering the report as a PDF needs WeasyPrint, which is not installed. "
              "Install the report extra (pip install 'fastmdxplora[pdf]').")
    # As report/run.py writes it.
    (tmp_path / "report").mkdir()
    (tmp_path / "report" / "not_produced.json").write_text(json.dumps(
        [{"artifact": "report.pdf", "code": "report.format.unavailable", "reason": reason}],
        indent=2), encoding="utf-8")
    payload = problems_payload(tmp_path, admit_evidence=True)
    assert payload["items"] == [{
        "id": "report:not-produced:0", "title": "Report: report.pdf not produced",
        "reason": reason, "code": "report.format.unavailable",
        "fix": remedy_for({"code": "report.format.unavailable", "message": reason}).fix,
        "file": "report/not_produced.json"}]
    assert "report/not_produced.json" in payload["read"]


def _convergence_report(root: Path, series) -> None:
    """The report's convergence section, as report/document.py writes it,
    over a radius of gyration recorded as the rg analysis writes it."""
    from fastmdxplora.report.document import _convergence_section

    folder = root / "analysis" / "rg"
    folder.mkdir(parents=True)
    (folder / "rg.dat").write_text("\n".join(f"{value:.6f}" for value in series) + "\n",
                                   encoding="utf-8")
    _write(root, "analysis/analysis_manifest.json", {"results": {"rg": {"status": "ok"}}})
    _write(root, "analysis/rg/options.json", {"findings": {}})
    section = _convergence_section(root)
    (root / "report").mkdir(exist_ok=True)
    (root / "report" / "report.md").write_text("# Report\n\n" + section + "\n",
                                               encoding="utf-8")


def test_an_observable_the_report_says_is_still_moving_is_not_equilibrated(tmp_path):
    import numpy as np

    rng = np.random.default_rng(3)
    _convergence_report(tmp_path, np.linspace(0.30, 0.40, 300) + rng.normal(0, 0.002, 300))
    text = (tmp_path / "report/report.md").read_text(encoding="utf-8")
    assert "| Each observable equilibrates before it is averaged | **failed** | still moving: Rg |" in text
    payload = problems_payload(tmp_path, admit_evidence=True)
    found = [item for item in payload["items"] if item["id"] == "report:not-equilibrated"]
    assert found == [{
        "id": "report:not-equilibrated", "title": "Not equilibrated: Rg",
        "reason": "Still moving in one direction: Rg. The run has not finished "
                  "equilibrating, so averages over it describe the approach rather than "
                  "the state.",
        "fix": "No remedy is recorded.", "file": "report/report.md"}]
    assert "report/report.md" in payload["read"]


def test_an_equilibrated_observable_is_not_listed(tmp_path):
    import numpy as np

    _convergence_report(tmp_path, 0.35 + np.random.default_rng(3).normal(0, 0.002, 300))
    assert "| Each observable equilibrates before it is averaged | passed |" in (
        tmp_path / "report/report.md").read_text(encoding="utf-8")
    payload = problems_payload(tmp_path)
    assert not any("equilibrat" in item["title"].lower() for item in payload["items"])
    assert "report/report.md" in payload["read"]


def test_a_report_older_than_the_analyses_is_not_read_as_current(tmp_path):
    import numpy as np

    rng = np.random.default_rng(3)
    _convergence_report(tmp_path, np.linspace(0.30, 0.40, 300) + rng.normal(0, 0.002, 300))
    later = (tmp_path / "report/report.md").stat().st_mtime + 60
    os.utime(tmp_path / "analysis/analysis_manifest.json", (later, later))
    payload = problems_payload(tmp_path)
    assert not any(item["id"] == "report:not-equilibrated" for item in payload["items"])
    assert any(note.startswith("report/report.md was written before the analyses")
               for note in payload["notes"])


def _completed(root: Path) -> None:
    from fastmdxplora.gui.telemetry import TelemetryWriter

    TelemetryWriter(root / "simulation").write_status(status="completed", stage="report")
    _write(root, "manifest.json", {"phases": [_phase("setup", "ok"),
                                              _phase("simulation", "ok")]})


def test_a_missing_narration_is_said_as_unavailable(tmp_path):
    _completed(tmp_path)
    _logged(tmp_path, (logging.INFO, "Plan: setup -> simulation"))
    payload = problems_payload(tmp_path)
    assert payload["items"] == []
    assert payload["unavailable"] == ["simulation/live_events.log"]
    assert "simulation/live_events.log is not there; the run's warnings are unknown." \
        in payload["notes"]


def test_each_warning_in_the_study_log_is_listed_once_with_the_log(tmp_path):
    _completed(tmp_path)
    _events(tmp_path / "simulation", ("warning", "warning: could not sample live metrics (x)"))
    _logged(tmp_path, (logging.INFO, "Plan: setup -> simulation"),
            (logging.WARNING, PADDING), (logging.WARNING, PADDING),
            (logging.WARNING, "warning: could not sample live metrics (x)"),
            (logging.ERROR, "Phase 'analysis' failed: an error, listed from the manifest"))
    payload = problems_payload(tmp_path, admit_evidence=True)
    assert [(item["title"], item["reason"], item["file"]) for item in payload["items"]] == [
        ("Recorded run warning", "Could not sample live metrics (x)",
         "simulation/live_events.log"),
        ("Warning in the study's log", PADDING, "fastmdxplora.log")]
    assert payload["unavailable"] == []


def test_a_warning_of_a_run_the_study_replaced_is_not_listed(tmp_path):
    """Set up for real with 1.2 nm of padding, which setup warns of, then
    set up again with ``--force-overwrite`` and 1.5 nm, which it does not:
    the log keeps both runs, the study's records only the second."""
    pytest.importorskip("openmm")
    pytest.importorskip("pdbfixer")
    from fastmdxplora import FastMDXplora
    from tests.test_a_real_study_runs_end_to_end import TRI_ALANINE

    (tmp_path / "tri.pdb").write_text(TRI_ALANINE, encoding="utf-8")
    root = tmp_path / "study"

    def set_up(padding: float, **kwargs) -> list[str]:
        config = {"systems": [{"id": "tri", "system": str(tmp_path / "tri.pdb")}],
                  "setup": {"ph": 7.0, "solvent_padding_nm": padding,
                            "nonbonded_cutoff_nm": 0.9}}
        FastMDXplora(config_data=config, output_dir=str(root)).explore(
            include=["setup"], **kwargs)
        return [item["reason"] for item in problems_payload(root)["items"]
                if item["title"] == "Warning in the study's log"]

    first = set_up(1.2)
    assert len(first) == 1 and first[0].startswith("1.2 nm of padding")
    assert set_up(1.5, force=True) == []
    log = (root / "fastmdxplora.log").read_text(encoding="utf-8")
    assert "1.2 nm of padding" in log and "=== new run" in log


def test_a_later_run_replaces_the_warnings_of_the_phases_it_did_again(tmp_path):
    """Analyze again replaces the analysis and the report (`replaced.make_way`),
    not the setup: setup's warning stays, the earlier analysis's goes."""
    _completed(tmp_path)
    _events(tmp_path / "simulation", ("info", "Production started"))
    starts = "--> Phase '%s' starting (output=%s)"  # as `_run_phase` logs it
    _logged(tmp_path, (logging.DEBUG, "Plan: setup -> simulation -> analysis"),
            (logging.WARNING, PADDING), (logging.DEBUG, starts % ("setup", "setup")),
            (logging.DEBUG, starts % ("simulation", "simulation")),
            (logging.DEBUG, starts % ("analysis", "analysis")),
            (logging.WARNING, "The earlier analysis's own warning."))
    _logged(tmp_path, (logging.DEBUG, "Plan: analysis -> report"),
            (logging.DEBUG, starts % ("analysis", "analysis")),
            (logging.WARNING, "This analysis's own warning."),
            (logging.DEBUG, starts % ("report", "report")))
    assert "=== new run" in (tmp_path / "fastmdxplora.log").read_text(encoding="utf-8")
    assert [item["reason"] for item in problems_payload(tmp_path)["items"]] == [
        PADDING, "This analysis's own warning."]


def test_a_warning_from_a_piece_names_its_own_log(tmp_path):
    root = _stopped(tmp_path / "study")
    _carrying_it_on(root, status="running", step=3000)
    _events(root / "segment-001" / "simulation",
            ("warning", "warning: could not sample live metrics (y)"))
    _logged(root / "segment-001", (logging.WARNING, "A piece's own warning."))
    payload = problems_payload(root, admit_evidence=True)
    by_reason = {item["reason"]: item for item in payload["items"]}
    assert by_reason["Could not sample live metrics (y)"]["file"] == \
        "segment-001/simulation/live_events.log"
    assert by_reason["A piece's own warning."]["file"] == "segment-001/fastmdxplora.log"
    for record in ("segment-001/simulation/live_events.log", "segment-001/fastmdxplora.log",
                   "segment-001/simulation/live_metrics.csv"):
        assert record in payload["read"]


def test_a_healthy_running_study_has_no_record_unavailable(tmp_path):
    from datetime import datetime, timezone

    from fastmdxplora.gui.telemetry import TelemetryWriter
    from fastmdxplora.orchestrator import RUN_PROCESS_FILE, this_machine

    TelemetryWriter(tmp_path / "simulation").write_status(
        status="running", stage="production",
        last_update_timestamp=datetime.now(timezone.utc).isoformat())
    _write(tmp_path, RUN_PROCESS_FILE, {"pid": os.getpid(), **this_machine()})
    _events(tmp_path / "simulation", ("info", "Production started"))
    _logged(tmp_path, (logging.INFO, "Plan: setup -> simulation"))
    payload = problems_payload(tmp_path)
    assert payload["items"] == [] and payload["available"] is True
    assert payload["unavailable"] == []
    assert payload["notes"][0] == ("manifest.json is written when the run ends; its "
                                   "refusals and phase results are read from it then.")


@pytest.mark.parametrize("begun", [False, True])
def test_a_folder_with_no_records_yet_says_so(tmp_path, begun):
    if begun:
        (tmp_path / "setup").mkdir()
        (tmp_path / "exploration.log").write_text("Starting.\n", encoding="utf-8")
    payload = problems_payload(tmp_path)
    assert payload["state"] == "empty"
    assert payload["items"] == [] and payload["unavailable"] == []
    assert payload["notes"] == ["No records yet. A study's problems are read from the "
                                "records its run writes, and this folder holds none so far."]


def test_telemetry_descriptions_are_reasons_and_only_remedies_are_fixes(tmp_path):
    from datetime import datetime, timedelta, timezone

    from fastmdxplora.gui.telemetry import (STALE_EXPLANATION, TEMPERATURE_REMEDY,
                                            TelemetryWriter)
    from fastmdxplora.orchestrator import RUN_PROCESS_FILE, this_machine

    stale = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    TelemetryWriter(tmp_path / "simulation").write_status(status="running", stage="production")
    record = json.loads((tmp_path / "simulation/live_status.json").read_text(encoding="utf-8"))
    # Its last word ten minutes ago, from a process still there.
    _write(tmp_path, "simulation/live_status.json", {**record, "last_update_timestamp": stale})
    _write(tmp_path, RUN_PROCESS_FILE, {"pid": os.getpid(), **this_machine()})
    items = problems_payload(tmp_path)["items"]
    assert [(item["reason"], item["fix"]) for item in items] == [
        ("Telemetry is stale. " + STALE_EXPLANATION, "No remedy is recorded.")]

    hot = tmp_path / "hot"
    TelemetryWriter(hot / "simulation").write_status(
        status="completed", stage="production", target_temperature_K=300)
    (hot / "simulation/live_metrics.csv").write_text("stage,temperature\nproduction,500\n",
                                                     encoding="utf-8")
    items = problems_payload(hot)["items"]
    assert [(item["reason"], item["fix"]) for item in items] == [
        ("Temperature 500.0 K is far from target 300.0 K.", TEMPERATURE_REMEDY)]


def test_phase_titles_say_the_status_as_the_overview_does(tmp_path):
    _write(tmp_path, "manifest.json", {"phases": [
        _phase("setup", "error", "No file at missing.pdb.", "environment.path.not_found")]})
    _write(tmp_path, "analysis/analysis_manifest.json", {"results": {
        "rmsd": {"status": "error", "message": "rmsd: Atoms do not match."}}})
    titles = [item["title"] for item in problems_payload(tmp_path)["items"]]
    assert titles == ["Setup failed", "RMSD failed"]


def test_a_stop_of_the_study_s_own_analysis_after_its_piece_opens_the_study_s_record(tmp_path):
    from datetime import datetime, timedelta, timezone

    from fastmdxplora.gui.telemetry import status_as_it_stands

    root = _stopped(tmp_path / "study")
    _carrying_it_on(root, status="completed", step=7200, finished=True)
    # The piece ended; the study's own analysis of the joined trajectory
    # then stopped, as the study's record says.
    first = json.loads((root / "simulation/live_status.json").read_text(encoding="utf-8"))
    states = dict(first.get("stage_states") or {}, analysis="stopped", report="waiting")
    first.update(status="stopped", stage="analysis", latest_error="Analysis was stopped by Ctrl+C.",
                 stage_states=states, phases_updated_at=(
                     datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat())
    _write(root, "simulation/live_status.json", first)
    shown = status_as_it_stands(root)
    assert shown["status"] == "stopped" and shown.get("piece") == "segment-001"
    items = problems_payload(root, admit_evidence=True)["items"]
    health = [item for item in items if item["id"].startswith("health:")]
    assert [item["file"] for item in health] == ["simulation/live_status.json"]
