"""The Problems panel gathers recorded evidence without changing the study."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from fastmdxplora.gui.workspace import study_key
from fastmdxplora.remedies import remedy_for
from fastmdxplora.statistics import MEAN_RULES


def problems_payload(root, **kwargs):
    # Import per test so the parent-commit check runs every case, even
    # though that commit has no Problems reader yet.
    from fastmdxplora.gui.problems_view import problems_payload as read

    return read(root, **kwargs)


def _write(root: Path, relative: str, value: object) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _link(path: Path, target: Path, *, directory: bool = False) -> None:
    try:
        path.symlink_to(target, target_is_directory=directory)
    except OSError:
        pytest.skip("this system cannot create symbolic links")


def _events(root: Path, *lines: tuple[str, str], folder: str = "simulation") -> None:
    from fastmdxplora.gui.telemetry import TelemetryWriter

    writer = TelemetryWriter(root / folder)
    for level, message in lines:
        writer.event(message, level=level)


def _logged(root: Path, *records: tuple[int, str]) -> None:
    """Lines in the study's log, as the orchestrator's file log writes them."""
    from fastmdxplora.utils.logging import attach_file_logger, detach_file_logger, get_logger

    path = root / "fastmdxplora.log"
    root.mkdir(parents=True, exist_ok=True)
    attach_file_logger(path, level=logging.DEBUG)
    try:
        for level, message in records:
            get_logger("orchestrator").log(level, message)
    finally:
        detach_file_logger(path)


def _status(root: Path, state: str, **extra: object) -> None:
    _write(root, "simulation/live_status.json", {"status": state, "stage": "production",
                                                **extra})
    # Every run that keeps a live record narrates and logs beside it.
    _events(root, ("info", "Simulation phase started"))
    _logged(root, (logging.INFO, "Plan: setup -> simulation -> analysis -> report"))


def _mean(**extra: object) -> dict:
    return {"mean": 1.4, "standard_error": 0.1, "n_frames": 200,
            "effective_samples": 100, "rules": MEAN_RULES, **extra}


def test_every_quantity_is_checked_including_after_the_first_nine(tmp_path):
    results = {f"quantity_{index}": {"status": "ok"} for index in range(12)}
    _write(tmp_path, "analysis/analysis_manifest.json", {"results": results})
    for name in results:
        _write(tmp_path, f"analysis/{name}/options.json", {"findings": {
            "mean": _mean(), "secondary": _mean(not_a_measurement="Not equilibrated.")}})
    _status(tmp_path, "completed")
    payload = problems_payload(tmp_path)
    assert payload["available"] is True
    assert payload["study"] == study_key(tmp_path)
    assert len(payload["items"]) == 12
    assert all(item["reason"] == "Not equilibrated." for item in payload["items"])
    # Nothing in these records says what would fix them, and nothing is made up.
    assert all(item["fix"] == "No remedy is recorded." for item in payload["items"])


def test_old_rules_are_distinct_from_a_withheld_mean(tmp_path):
    _write(tmp_path, "analysis/rmsd/options.json", {"findings": {
        "mean": _mean(rules=0), "other": _mean(not_a_measurement="Periodic image.")}})
    by_id = {item["id"]: item for item in problems_payload(tmp_path)["items"]}
    assert "earlier rules" in by_id["analysis:rmsd:mean"]["title"]
    assert "Analyze again" in by_id["analysis:rmsd:mean"]["fix"]
    assert by_id["analysis:rmsd:other"]["reason"] == "Periodic image."
    assert "code" not in by_id["analysis:rmsd:other"]


def test_nested_thermodynamic_quantities_are_included(tmp_path):
    _write(tmp_path, "analysis/thermodynamics/options.json", {"findings": {
        "thermodynamics": {"samples": 40, "temperature": {
            "units": "K", "mean": 310, "not_a_measurement": "Temperature is drifting."}}}})
    items = problems_payload(tmp_path)["items"]
    assert len(items) == 1
    assert items[0]["id"] == "analysis:thermodynamics:thermodynamics.temperature"
    assert items[0]["reason"] == "Temperature is drifting."


def test_all_refusals_use_the_registry_without_exposing_commands(tmp_path):
    first = {"code": "config.option.unknown", "message": "Unknown setting.",
             "details": {"context": "simulation", "option": "temprature_K"},
             "permitted": ["temperature_K"]}
    second = {"code": "unclassified", "message": "A separate failure.",
              "details": {"install_command": "SECRET_COMMAND"}}
    _write(tmp_path, "manifest.json", {"phases": [
        {"name": "setup", "status": "error", "refusal": first},
        {"name": "analysis", "status": "error", "refusal": second}],
        "argv": ["PRIVATE_ARGV"], "config": {"private": "PRIVATE_CONFIG"}})
    _status(tmp_path, "failed", latest_error="Unknown setting.")
    payload = problems_payload(tmp_path)
    assert len(payload["items"]) == 2
    assert payload["items"][0]["fix"] == remedy_for(first).fix
    assert payload["items"][1]["reason"] == "A separate failure."
    assert set(payload["items"][0]) == {"id", "title", "reason", "fix", "code"}
    assert "PRIVATE" not in json.dumps(payload)
    assert "SECRET_COMMAND" not in json.dumps(payload)


@pytest.mark.parametrize("state", ["stopped", "interrupted"])
def test_stops_and_interruptions_are_not_called_failures(tmp_path, state):
    _status(tmp_path, state)
    items = problems_payload(tmp_path)["items"]
    assert len(items) == 1
    assert items[0]["id"] == f"health:{state}"
    assert items[0]["title"] == state.capitalize()


def test_health_warning_uses_the_existing_telemetry_interpretation(tmp_path):
    _status(tmp_path, "completed", target_temperature_K=300)
    (tmp_path / "simulation/live_metrics.csv").write_text(
        "stage,temperature\nproduction,500\n", encoding="utf-8")
    items = problems_payload(tmp_path)["items"]
    assert len(items) == 1
    assert items[0]["id"] == "health:warning"
    assert "500.0 K" in items[0]["reason"]


def test_a_manifest_stop_does_not_duplicate_or_promise_a_checkpoint(tmp_path):
    _write(tmp_path, "manifest.json", {"phases": [{"name": "simulation", "status": "error",
        "refusal": {"code": "simulation.run.stopped", "message": "Stopped during NVT."}}]})
    _write(tmp_path, "simulation/live_status.json", {"status": "stopped", "stage": "nvt"})
    items = problems_payload(tmp_path)["items"]
    assert len(items) == 1
    assert "no checkpoint" in items[0]["fix"]
    assert "Run it again" in items[0]["fix"]


def test_failed_analysis_keeps_its_reason_and_only_admitted_evidence_links(tmp_path):
    _write(tmp_path, "analysis/analysis_manifest.json", {"results": {
        "rmsd": {"status": "error", "message": "rmsd: Atoms do not match."}}})
    path = "analysis/analysis_manifest.json"
    no_links = problems_payload(tmp_path)["items"]
    assert len(no_links) == 1 and no_links[0]["reason"] == "Atoms do not match."
    assert "file" not in no_links[0]
    admitted = problems_payload(tmp_path, artifacts=[{"path": path}])["items"]
    assert admitted[0]["file"] == path
    denied = problems_payload(tmp_path, artifacts=[{"path": "unrelated.json"}])["items"]
    assert "file" not in denied[0]


@pytest.mark.parametrize("content", ["{", "[]", "{}"])
def test_malformed_and_empty_records_are_not_healthy(tmp_path, content):
    (tmp_path / "manifest.json").write_text(content, encoding="utf-8")
    payload = problems_payload(tmp_path)
    assert payload["available"] is False
    assert payload["items"] == []
    assert any("manifest.json" in note for note in payload["notes"])
    assert any("unknown" in note for note in payload["notes"])


def test_empty_folder_and_campaign_scope_are_stated(tmp_path):
    payload = problems_payload(tmp_path)
    assert payload["available"] is False and payload["notes"]
    _write(tmp_path, "batch_manifest.json", {"runs": []})
    campaign = problems_payload(tmp_path)
    assert any("campaign run" in note for note in campaign["notes"])
    assert campaign["available"] is False


def test_malformed_findings_cannot_be_reported_as_checked(tmp_path):
    _write(tmp_path, "analysis/rmsd/options.json", {"findings": "unreadable"})
    payload = problems_payload(tmp_path)
    assert payload["available"] is False
    assert any("no readable findings" in note for note in payload["notes"])


def test_successful_current_records_have_no_problems_and_are_unchanged(tmp_path):
    _write(tmp_path, "manifest.json", {"phases": [{"name": "simulation", "status": "ok"},
                                                  {"name": "analysis", "status": "ok"}]})
    _status(tmp_path, "completed")
    _write(tmp_path, "analysis/analysis_manifest.json", {"results": {"rmsd": {"status": "ok"}}})
    _write(tmp_path, "analysis/rmsd/options.json", {"findings": {"mean": _mean()}})
    before = {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.rglob("*")
              if p.is_file()}
    payload = problems_payload(tmp_path)
    assert payload["available"] is True and payload["items"] == []
    # An empty list names the records it read, and none was unavailable.
    assert payload["read"] == ["manifest.json", "simulation/live_status.json",
                               "simulation/live_events.log", "fastmdxplora.log",
                               "analysis/analysis_manifest.json", "analysis/rmsd/options.json"]
    assert payload["unavailable"] == []
    assert before == {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.rglob("*")
                      if p.is_file()}


def test_analysis_read_failure_does_not_erase_a_stopped_run(tmp_path, monkeypatch):
    from fastmdxplora.gui import problems_view

    _status(tmp_path, "stopped")

    def unreadable(root):
        raise ValueError("private exception context")

    monkeypatch.setattr(problems_view, "overview_of", unreadable)
    payload = problems_payload(tmp_path)
    assert payload["items"][0]["id"] == "health:stopped"
    assert any("could not be read" in note for note in payload["notes"])
    assert "private exception" not in json.dumps(payload)


@pytest.mark.parametrize("source", ["manifest.json", "simulation/live_status.json",
                                    "analysis/rmsd/options.json", "analysis"])
def test_linked_sources_outside_the_study_are_not_read(tmp_path, source):
    root = tmp_path / "study"
    root.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    secret = "PRIVATE_EXTERNAL_FINDING"
    record = {"phases": [{"name": "setup", "status": "error", "message": secret}],
              "status": "failed", "latest_error": secret,
              "findings": {"mean": _mean(not_a_measurement=secret)}}
    (external / "record.json").write_text(json.dumps(record), encoding="utf-8")
    link = root / source
    link.parent.mkdir(parents=True, exist_ok=True)
    if source == "analysis":
        (external / "rmsd").mkdir()
        (external / "rmsd" / "options.json").write_text(json.dumps(record), encoding="utf-8")
        _link(link, external, directory=True)
    else:
        _link(link, external / "record.json")
    payload = problems_payload(root, artifacts=[{"path": source}])
    assert payload["available"] is False
    assert payload["items"] == []
    assert any("link" in note for note in payload["notes"])
    assert secret not in json.dumps(payload)


def test_a_link_to_private_records_inside_the_study_is_not_read(tmp_path):
    _write(tmp_path, ".private/record.json", {"status": "failed", "latest_error": "SECRET"})
    (tmp_path / "simulation").mkdir()
    _link(tmp_path / "simulation/live_status.json", tmp_path / ".private/record.json")
    payload = problems_payload(tmp_path)
    assert payload["available"] is False and payload["items"] == []
    assert "SECRET" not in json.dumps(payload)


def test_a_malformed_mean_is_explicit_even_when_the_run_completed(tmp_path):
    _status(tmp_path, "completed")
    _write(tmp_path, "analysis/analysis_manifest.json", {"results": {"rmsd": {"status": "ok"}}})
    _write(tmp_path, "analysis/rmsd/options.json", {"findings": {"mean": "corrupt"}})
    payload = problems_payload(tmp_path)
    assert any("unreadable mean" in note for note in payload["notes"])
    assert any("analysis/rmsd/options.json" in note for note in payload["notes"])


def test_a_recorded_warning_survives_a_completed_health_state(tmp_path):
    _status(tmp_path, "completed")
    _events(tmp_path, ("warning", "warning: could not sample live metrics (NaN in state)"))
    _write(tmp_path, "analysis/analysis_manifest.json", {"results": {"rmsd": {"status": "ok"}}})
    _write(tmp_path, "analysis/rmsd/options.json", {"findings": {"mean": _mean()}})
    payload = problems_payload(tmp_path, admit_evidence=True)
    assert len(payload["items"]) == 1
    # Said as a person reads it, without the runner's own "warning:".
    assert payload["items"][0]["title"] == "Recorded run warning"
    assert payload["items"][0]["reason"] == "Could not sample live metrics (NaN in state)"
    assert payload["items"][0]["file"] == "simulation/live_events.log"


def test_each_recorded_run_warning_is_listed_once(tmp_path):
    _status(tmp_path, "completed")
    _events(tmp_path, ("info", "Production started"),
            ("warning", "warning: could not sample live metrics (first)"),
            ("warning", "warning: could not sample live metrics (second)"),
            ("warning", "warning: could not sample live metrics (first)"),
            ("info", "FastMDXplora exploration completed"))
    reasons = [item["reason"] for item in problems_payload(tmp_path)["items"]]
    assert reasons == ["Could not sample live metrics (first)",
                       "Could not sample live metrics (second)"]


def test_a_stop_and_its_warnings_are_one_problem(tmp_path):
    # As runner.py and orchestrator.py record a stop: the stop's words as a
    # warning, the status stopped with them, and the phase stopped.
    words = "Stopped as asked during production; carry it on from step 5000."
    _events(tmp_path, ("warning", words), ("warning", "Simulation phase stopped"))
    _status(tmp_path, "stopped", latest_error=words)
    items = problems_payload(tmp_path)["items"]
    assert [item["id"] for item in items] == ["health:stopped"]
    assert items[0]["reason"] == words


def _running(root: Path, age_seconds: float, **process: object) -> None:
    from datetime import datetime, timedelta, timezone

    stamp = (datetime.now(timezone.utc) - timedelta(seconds=age_seconds)).isoformat()
    _status(root, "running", last_update_timestamp=stamp)
    if process:
        _write(root, ".fastmdxplora_run.json", {"started_at": stamp, **process})


def _this_process() -> dict:
    import os

    from fastmdxplora.orchestrator import this_machine

    return {"pid": os.getpid(), **this_machine()}


def _a_finished_process() -> dict:
    import subprocess
    import sys

    from fastmdxplora.orchestrator import this_machine

    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return {"pid": child.pid, **this_machine()}


@pytest.mark.parametrize("process", [False, True])
def test_a_running_study_is_read_and_is_not_a_problem(tmp_path, process):
    _running(tmp_path, 5, **(_this_process() if process else {}))
    payload = problems_payload(tmp_path)
    assert payload["available"] is True
    assert payload["items"] == []
    assert "simulation/live_status.json" in payload["read"]
    assert not any("confined" in note for note in payload["notes"])


@pytest.mark.parametrize("ended", ["silence", "process"])
def test_a_run_the_overview_calls_interrupted_is_listed(tmp_path, ended):
    from fastmdxplora.gui.telemetry import analyze_health, status_as_it_stands

    if ended == "silence":
        _running(tmp_path, 3 * 86400)
    else:
        _running(tmp_path, 5, **_a_finished_process())
    shown = status_as_it_stands(tmp_path)
    assert shown["status"] == "interrupted" and shown["ended_by"] == ended
    items = problems_payload(tmp_path)["items"]
    assert len(items) == 1
    assert items[0]["id"] == "health:interrupted"
    assert items[0]["title"] == "Interrupted"
    assert items[0]["reason"] == analyze_health(shown, [])["message"]


def test_only_the_run_process_record_is_admitted_among_hidden_files(tmp_path):
    _write(tmp_path, ".private/record.json", {"pid": 1, "argv": ["PRIVATE_ARGV"]})
    _running(tmp_path, 5)
    _link(tmp_path / ".fastmdxplora_run.json", tmp_path / ".private/record.json")
    payload = problems_payload(tmp_path)
    assert payload["available"] is False and payload["items"] == []
    assert ".fastmdxplora_run.json" in payload["unavailable"]
    assert "PRIVATE_ARGV" not in json.dumps(payload)


@pytest.mark.parametrize("missing", ["manifest.json", "analysis/analysis_manifest.json"])
def test_a_missing_record_is_said_as_unavailable(tmp_path, missing):
    _write(tmp_path, "manifest.json", {"phases": [{"name": "simulation", "status": "ok"},
                                                  {"name": "analysis", "status": "ok"}]})
    _status(tmp_path, "completed")
    _write(tmp_path, "analysis/analysis_manifest.json", {"results": {"rmsd": {"status": "ok"}}})
    _write(tmp_path, "analysis/rmsd/options.json", {"findings": {"mean": _mean()}})
    (tmp_path / missing).unlink()
    payload = problems_payload(tmp_path)
    assert payload["items"] == []
    assert payload["unavailable"] == [missing]
    assert missing not in payload["read"]
    assert any(note.startswith(f"{missing} is not there") for note in payload["notes"])


def test_an_undetermined_mean_shows_the_sampling_its_record_asks_for(tmp_path):
    import numpy as np

    from fastmdxplora.statistics import Shortfall, mean_record

    # A slowly wandering series: its mean is withheld for want of
    # independent samples, and the record says how much more would do.
    rng = np.random.default_rng(7)
    series = np.cumsum(rng.normal(size=120)) * 0.01 + 1.0
    found = mean_record(series, frame_interval_ns=0.01)
    assert "shortfall" in found and found.get("not_a_measurement")
    _write(tmp_path, "analysis/rmsd/options.json", {"findings": {"mean": found}})
    items = problems_payload(tmp_path)["items"]
    assert len(items) == 1
    short = found["shortfall"]
    assert items[0]["reason"] == found["not_a_measurement"]
    # The record's own sentence (`Shortfall.__str__`), nothing more.
    assert items[0]["fix"] == str(Shortfall(
        target=short["target_independent"], have=short["independent"],
        inefficiency=short["statistical_inefficiency"], more_frames=short["more_frames"],
        more_ns=short["more_ns"], resolved=not short.get("lower_bound")))


@pytest.mark.parametrize("lower_bound, said", [
    (True, "The run is too short to resolve its own correlation time, so its estimate of "
           "fewer than one independent sample is an upper bound. At least 1640 further frames, about "
           "0.0656 ns more (as long again, or to 25 samples at one every 67 frames if "
           "that is longer), then estimate again."),
    (False, "fewer than one independent sample of the 25 needed. At one every 67 frames, that is "
            "1640 further frames, about 0.0656 ns more."),
])
def test_a_shortfall_is_said_in_its_records_own_words(tmp_path, lower_bound, said):
    # Radius of gyration in a real 4 ps run of tri-alanine: a floor asks for
    # at least that much and a new estimate, and never says it will do.
    shortfall = {"target_independent": 25.0, "independent": 0.5077832436553107,
                 "statistical_inefficiency": 66.95770375416248, "more_frames": 1640,
                 "more_ns": 0.06559993743896483}
    if lower_bound:
        shortfall["lower_bound"] = True
    _write(tmp_path, "analysis/rg/options.json", {"findings": {"mean": {
        "mean": 0.3355, "effective_samples": 0.5, "unit": "nm", "rules": MEAN_RULES,
        "not_a_measurement": "Too few independent samples.", "shortfall": shortfall}}})
    items = problems_payload(tmp_path)["items"]
    assert [item["fix"] for item in items] == [said]
    assert "should give" not in items[0]["fix"]


def test_a_retryable_refusal_keeps_its_registry_remedy(tmp_path):
    refusal = {"code": "environment.service.unreachable",
               "message": "The service did not answer."}
    _write(tmp_path, "manifest.json", {"phases": [
        {"name": "setup", "status": "error", "refusal": refusal}]})
    items = problems_payload(tmp_path)["items"]
    assert len(items) == 1
    assert items[0]["code"] == "environment.service.unreachable"
    assert items[0]["fix"] == remedy_for(refusal, study=tmp_path, price=False).fix
    assert items[0]["fix"] != "No remedy is recorded."


def test_where_no_remedy_is_recorded_it_says_so(tmp_path):
    _write(tmp_path, "manifest.json", {"phases": [
        {"name": "report", "status": "error", "message": "The report could not be written."}]})
    _write(tmp_path, "analysis/analysis_manifest.json", {"results": {
        "rmsd": {"status": "error", "message": "rmsd: Atoms do not match."}}})
    items = problems_payload(tmp_path)["items"]
    assert len(items) == 2
    assert all(item["fix"] == "No remedy is recorded." for item in items)


def test_a_quantity_constant_in_every_frame_is_titled_as_the_overview_says(tmp_path):
    import numpy as np

    from fastmdxplora.gui.analysis_overview import overview_of
    from fastmdxplora.statistics import mean_record

    _write(tmp_path, "analysis/dssp/options.json", {"findings": {
        "mean": _mean(), "helix_fraction": mean_record(np.zeros(200))}})
    said = next(q for q in overview_of(tmp_path)["rows"][0]["quantities"]
                if q["key"] == "helix_fraction")
    assert said["said"].endswith("in every frame")
    items = problems_payload(tmp_path)["items"]
    assert len(items) == 1
    assert "not determined" not in items[0]["title"]
    assert items[0]["title"].endswith(": " + said["said"])
    assert items[0]["reason"] == said["why"]


def test_no_study_and_a_gone_folder_are_answered_without_a_server_error(tmp_path):
    import shutil

    from fastmdxplora.gui.exploration import DashboardRuntime
    from tests.test_a_public_dashboard_answers_only_what_it_lists import _ask, _serving

    nothing = DashboardRuntime(workspace_root=tmp_path, exploration_root=tmp_path,
                               active_root=None)
    with _serving(nothing, allow_control=True) as url:
        status, body = _ask(url + "/api/problems")
    assert status == 200
    assert json.loads(body)["active_run"] is None

    root = tmp_path / "study"
    _status(root, "completed")
    _write(root, "manifest.json", {"phases": [{"name": "simulation", "status": "ok"}]})
    runtime = DashboardRuntime(workspace_root=root, exploration_root=tmp_path, active_root=root)
    shutil.rmtree(root)
    with _serving(runtime, allow_control=True) as url:
        status, body = _ask(url + "/api/problems")
    assert status == 200
    gone = json.loads(body)
    assert gone["state"] == "gone" and gone["active_run"] == str(root)
    assert gone["notes"] == ["The study's folder is no longer there, so its records "
                             "cannot be read."]


def test_a_folder_above_the_study_need_only_be_searchable(tmp_path, monkeypatch):
    import errno
    import os

    if not hasattr(os, "O_PATH") or os.open not in os.supports_dir_fd:
        pytest.skip("this checks the POSIX walk to the study")
    locked = tmp_path / "locked"
    root = locked / "study"
    _status(root, "completed")
    _write(root, "manifest.json", {"phases": [{"name": "simulation", "status": "ok"}]})
    opened = os.open

    def as_a_0711_folder(path, flags, *args, **kwargs):
        # What the system says to a process that may search the folder but
        # not read it, as root here is never told.
        if os.fspath(path) == "locked" and not flags & os.O_PATH:
            raise PermissionError(errno.EACCES, "Permission denied", "locked")
        return opened(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", as_a_0711_folder)
    monkeypatch.setattr(os, "supports_dir_fd", os.supports_dir_fd | {as_a_0711_folder})
    payload = problems_payload(root)
    assert payload["available"] is True
    assert payload["items"] == []


def test_a_stop_without_a_checkpoint_does_not_promise_continuation(tmp_path):
    _status(tmp_path, "stopped")
    item = problems_payload(tmp_path)["items"][0]
    assert "no checkpoint" in item["fix"]
    assert "Run it again" in item["fix"]
    assert "carries it on" not in item["fix"]


def test_the_public_endpoint_does_not_read_an_external_manifest(tmp_path):
    from fastmdxplora.gui.exploration import DashboardRuntime
    from tests.test_a_public_dashboard_answers_only_what_it_lists import _ask, _serving

    root = tmp_path / "study"
    root.mkdir()
    external = tmp_path / "outside.json"
    external.write_text(json.dumps({"phases": [{"name": "setup", "status": "failed",
                                               "message": "PRIVATE_EXTERNAL_REASON"}]}), encoding="utf-8")
    _link(root / "manifest.json", external)
    runtime = DashboardRuntime(workspace_root=root, exploration_root=tmp_path, active_root=root)
    with _serving(runtime, allow_control=False) as url:
        status, body = _ask(url + "/api/problems")
    assert status == 200
    payload = json.loads(body)
    assert payload["available"] is False and payload["items"] == []
    assert b"PRIVATE_EXTERNAL_REASON" not in body


def test_analysis_name_cannot_follow_a_path_outside_the_study(tmp_path):
    _write(tmp_path, "analysis/analysis_manifest.json", {"results": {
        "../../outside": {"status": "ok"}}})
    payload = problems_payload(tmp_path)
    assert payload["items"] == []
    assert any("unreadable result" in note for note in payload["notes"])
