"""A run of a study run in rounds is said waiting while the study goes on.

A study of replicas run until what it asks is determined
(`simulation.stop_when`) judges its runs, extends each by what is needed and
judges them again, one run after another. Replica 2's card, opened while the
study extended replica 1 in `segment-004/`, said Completed, Report at 100%,
stage 7 of 7: every piece of replica 2 so far, its analyses and its report,
were done. The study's list of runs counted both replicas completed, from
each run's first record and its result. A run waiting for the study's next
round is now said waiting, with how far its production is towards the length
that round runs it to; a run going on in a later piece is said running.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_a_resumed_study_reads_as_one_run import _ago, _write

STAGES = ("setup", "minimization", "nvt", "npt", "production", "analysis", "report")
FIRST, SECOND = "cln025__random-seed-1", "cln025__random-seed-2"


def _done(minutes_ago: float) -> dict:
    return {"stage": "report", "status": "completed", "current_step": 100,
            "total_planned_steps": 100, "last_update_timestamp": _ago(minutes_ago),
            "stage_states": {stage: "completed" for stage in STAGES}}


def _study(root: Path, *, outcome: str = "running") -> Path:
    """Two replicas judged three times; the third judgement extends each to
    55.13 ns, the first now (`segment-004/`, going), the second after it."""
    for name in (FIRST, SECOND):
        run = root / "runs" / name
        _write(run / "simulation" / "live_status.json", _done(60 * 24 * 5))
        _write(run / "manifest.json", {"phases": [
            {"name": phase, "status": "ok"} for phase in ("setup", "simulation", "analysis",
                                                          "report")]})
        for k in (1, 2, 3):
            _write(run / f"segment-00{k}" / "simulation" / "live_status.json",
                   {**_done(60 * 24 * (4 - k)), "stage": "production"})
    going = {"stage": "production", "status": "running", "current_step": 52678,
             "total_planned_steps": 55130, "last_update_timestamp": _ago(1),
             "stage_states": {"production": "current"}}
    _write(root / "runs" / FIRST / "segment-004" / "simulation" / "live_status.json", going)
    _write(root / "batch_manifest.json", {
        "planned": [{"run_id": name} for name in (FIRST, SECOND)],
        "runs": [{"run_id": name, "status": "ok"} for name in (FIRST, SECOND)]})
    _write(root / "stopping.json", {
        "runs": [FIRST, SECOND], "max_duration_ns": 200.0, "outcome": outcome,
        "rounds": [{"production_ns": 10.0, "decision": "extend", "more_ns": 10.0},
                   {"production_ns": 20.0, "decision": "extend", "more_ns": 24.87},
                   {"production_ns": 44.87, "decision": "extend", "more_ns": 10.26}]})
    return root


@pytest.fixture
def done_so_far(monkeypatch):
    """Each run's production as its pieces count it: the second at the
    third round's 44.87 ns."""
    from fastmdxplora.simulation import resume

    monkeypatch.setattr(resume, "production_done_ns", lambda run: 44.87)


def test_the_run_waiting_for_its_turn_is_said_waiting(tmp_path, done_so_far):
    from fastmdxplora.gui.telemetry import analyze_health, status_as_it_stands

    study = _study(tmp_path / "study")
    status = status_as_it_stands(study / "runs" / SECOND)
    health = analyze_health(status, [])
    assert status["status"] == "waiting" and status["recorded_status"] == "completed"
    assert status["stage"] == "production"
    assert [status["stage_states"][stage] for stage in STAGES] == [
        "completed", "completed", "completed", "completed", "waiting", "waiting", "waiting"]
    # 44.87 of the 55.13 ns the third judgement extends it to; no steps said
    # beside it (the round's are not planned in steps yet).
    assert status["progress_percent"] == pytest.approx(81.4)
    assert (status["current_step"], status["total_planned_steps"]) == (None, None)
    assert health["state"] == "waiting"
    assert health["headline"] == "Waiting for the study's next round"
    assert health["message"] == (f"Its study is extending {FIRST} to 55.13 ns of "
                                 "production; this run is extended after it.")


def test_the_study_s_runs_are_said_as_they_stand(tmp_path, done_so_far):
    from fastmdxplora.gui.exploration import runs_of_a_study

    runs = {run["run_id"]: run for run in runs_of_a_study(_study(tmp_path / "study"))}
    assert (runs[FIRST]["state"], runs[FIRST]["stage"]) == ("running", "production")
    assert (runs[SECOND]["state"], runs[SECOND]["so_far"]) == ("waiting", True)
    assert runs[SECOND]["fraction"] == pytest.approx(44.87 / 55.13)
    assert runs[FIRST]["so_far"] is True


def test_a_run_that_reached_the_round_waits_for_the_others(tmp_path, monkeypatch):
    """Side by side: the second run has done the round's length, and
    waits for the first before the study judges both."""
    from fastmdxplora.gui.telemetry import analyze_health, status_as_it_stands
    from fastmdxplora.simulation import resume

    monkeypatch.setattr(resume, "production_done_ns", lambda run: 55.13)
    status = status_as_it_stands(_study(tmp_path / "study") / "runs" / SECOND)
    assert status["progress_percent"] == pytest.approx(100.0)
    assert analyze_health(status, [])["message"] == (
        f"This run has been extended to 55.13 ns of production; its study still has "
        f"{FIRST} to extend, and judges every run once that is done.")


def test_the_run_going_is_said_running(tmp_path, done_so_far):
    from fastmdxplora.gui.telemetry import status_as_it_stands

    status = status_as_it_stands(_study(tmp_path / "study") / "runs" / FIRST)
    assert (status["status"], status["piece"]) == ("running", "segment-004")
    assert "between_rounds" not in status


@pytest.mark.parametrize("outcome", ["met", "ceiling", "stopped"])
def test_a_study_done_with_its_rounds_says_its_runs_completed(tmp_path, done_so_far, outcome):
    from fastmdxplora.gui.telemetry import status_as_it_stands

    study = _study(tmp_path / "study", outcome=outcome)
    status = status_as_it_stands(study / "runs" / SECOND)
    assert status["status"] == "completed" and "between_rounds" not in status


def test_a_study_whose_rounds_ended_unsaid_is_not_said_waiting(tmp_path, done_so_far):
    """Its record still says it is running, and nothing of it is going: the
    study was killed between rounds. Its runs are as they recorded."""
    from fastmdxplora.gui.telemetry import status_as_it_stands

    study = _study(tmp_path / "study")
    (study / "runs" / FIRST / "segment-004").rename(study / "runs" / FIRST / "set-aside")
    assert status_as_it_stands(study / "runs" / SECOND)["status"] == "completed"


def test_a_run_of_a_study_of_one_is_as_it_recorded(tmp_path, done_so_far):
    from fastmdxplora.gui.telemetry import status_as_it_stands

    study = tmp_path / "study"
    _write(study / "simulation" / "live_status.json", _done(10))
    _write(study / "stopping.json", {"outcome": "running", "rounds": []})
    assert status_as_it_stands(study)["status"] == "completed"


@pytest.fixture(scope="module")
def browser():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        launched = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        yield launched
        launched.close()


def test_the_card_says_waiting_not_completed(browser, tmp_path, done_so_far):
    """Its phases all recorded done, the card read Completed from them."""
    from fastmdxplora.gui.server import start_dashboard_session

    study = _study(tmp_path / "study")
    session = start_dashboard_session(output=str(study / "runs" / SECOND),
                                      host="127.0.0.1", port=0)
    page = browser.new_context(viewport={"width": 1400, "height": 900}).new_page()
    page.set_default_timeout(60000)
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.goto(session.url + "#overview", wait_until="domcontentloaded")
        page.wait_for_function("() => document.body.classList.contains('state-ready')"
                               " && document.getElementById('health-headline').textContent"
                               " !== '\\u2014'")
        said = page.evaluate("""() => ['topbar-status-text', 'health-headline', 'topbar-progress']
            .map((id) => document.getElementById(id).textContent.trim())""")
    finally:
        page.context.close()
        session.server.shutdown()
    assert said[0] == "Waiting", said
    assert said[1] == "Waiting for the study's next round", said
    assert said[2].startswith("81.4"), said
    assert errors == []
    assert json.loads((study / "stopping.json").read_text())["outcome"] == "running"


def test_a_run_that_failed_is_said_failed(tmp_path, done_so_far):
    """Its extension failed beside one still going (a study run side by side
    writes its record once every run of the round has answered): it was said
    waiting, and to be extended after the other."""
    from fastmdxplora.gui.exploration import runs_of_a_study
    from fastmdxplora.gui.telemetry import status_as_it_stands

    study = _study(tmp_path / "study")
    _write(study / "runs" / SECOND / "segment-004" / "simulation" / "live_status.json",
           {"stage": "production", "status": "failed", "current_step": 10,
            "total_planned_steps": 100, "latest_error": "NaN in the coordinates",
            "last_update_timestamp": _ago(2), "stage_states": {"production": "failed"}})
    status = status_as_it_stands(study / "runs" / SECOND)
    runs = {run["run_id"]: run for run in runs_of_a_study(study)}
    assert status["status"] == "failed" and "between_rounds" not in status
    assert runs[SECOND]["so_far"] is False


def test_before_the_first_judgement_a_run_is_as_it_recorded(tmp_path, done_so_far):
    """The study had decided nothing yet: the run finished first was said to
    be extended after the other, at 100%."""
    from fastmdxplora.gui.telemetry import status_as_it_stands

    study = _study(tmp_path / "study")
    record = json.loads((study / "stopping.json").read_text())
    record["rounds"] = []
    _write(study / "stopping.json", record)
    assert status_as_it_stands(study / "runs" / SECOND)["status"] == "completed"


def test_a_run_whose_result_failed_is_not_read_running(tmp_path, done_so_far):
    """Its first record left saying running, its result said it failed."""
    from fastmdxplora.gui.exploration import runs_of_a_study

    study = _study(tmp_path / "study", outcome="met")
    run = study / "runs" / SECOND
    for piece in sorted(run.glob("segment-*")):
        piece.rename(run.parent / f"{SECOND}-{piece.name}")
    _write(run / "simulation" / "live_status.json", {
        "stage": "production", "status": "running", "current_step": 5,
        "total_planned_steps": 100, "last_update_timestamp": _ago(1)})
    manifest = json.loads((study / "batch_manifest.json").read_text())
    manifest["runs"][1]["status"] = "error"
    _write(study / "batch_manifest.json", manifest)
    runs = {r["run_id"]: r for r in runs_of_a_study(study)}
    assert runs[SECOND]["state"] == "failed"


def test_the_study_s_pages_read_the_runs_done_so_far(tmp_path, done_so_far):
    """Its runs counted only completed: between rounds none were, and the
    comparison the rule judges, and the study's Report, had no run."""
    from fastmdxplora.gui.report_page import report_payload
    from fastmdxplora.gui.runs_compared import runs_compared

    study = _study(tmp_path / "study")
    # As a study in rounds has it: no run's result is written until its
    # rounds are over.
    manifest = json.loads((study / "batch_manifest.json").read_text())
    manifest["runs"] = []
    _write(study / "batch_manifest.json", manifest)
    report = report_payload(study)
    compared = runs_compared(study)
    # Read, both; completed, neither: the page said 2 of 2 runs completed.
    assert (compared["completed"], compared["so_far"]) == (0, 2)
    assert report["ok"] is True and report["pending"] == 2, report
    assert "0 of 2 runs completed" in report["html"], report["html"]
    assert "2 are read as far as their finished pieces go" in report["html"], report["html"]
    # Both have run, and wait for a round: neither is still to run.
    assert "still to run" not in report["html"], report["html"]


def test_a_study_s_runs_are_read_once_each(tmp_path, done_so_far, monkeypatch):
    """Each run read for every other run: a study of 16 runs took a second
    at every poll of the page."""
    from fastmdxplora.gui import telemetry
    from fastmdxplora.gui.exploration import runs_of_a_study

    study = _study(tmp_path / "study")
    record = json.loads((study / "stopping.json").read_text())
    manifest = json.loads((study / "batch_manifest.json").read_text())
    for n in range(3, 17):
        name = f"cln025__random-seed-{n}"
        _write(study / "runs" / name / "simulation" / "live_status.json", _done(60))
        record["runs"].append(name)
        manifest["planned"].append({"run_id": name})
        manifest["runs"].append({"run_id": name, "status": "ok"})
    _write(study / "stopping.json", record)
    _write(study / "batch_manifest.json", manifest)
    real = telemetry.read_study_status
    read = []
    # Only this study's: a GUI an earlier test left polling in the process
    # reads its own (17 on the ubuntu job).
    monkeypatch.setattr(telemetry, "read_study_status",
                        lambda root: (read.append(Path(root)) if Path(root).is_relative_to(study)
                                      else None) or real(root))
    runs = runs_of_a_study(study)
    assert sum(run["state"] == "waiting" for run in runs) == 15
    assert len(read) == len(set(read)) == 16


def test_between_two_runs_extensions_the_run_behind_is_named(tmp_path, monkeypatch):
    """The first run's extension done and analysed, the second's not begun:
    nothing of the study was going, and both cards said it was judging,
    the second to be extended only if what it asks was not yet determined."""
    from fastmdxplora.gui import telemetry
    from fastmdxplora.gui.telemetry import analyze_health, status_as_it_stands
    from fastmdxplora.orchestrator import RUN_PROCESS_FILE
    from fastmdxplora.simulation import resume

    study = _study(tmp_path / "study")
    going = study / "runs" / FIRST / "segment-004" / "simulation" / "live_status.json"
    _write(going, {**_done(1), "stage": "production"})
    _write(study / RUN_PROCESS_FILE, {"pid": 4242})
    monkeypatch.setattr(telemetry, "_what_its_process_says", lambda root, record: "running")
    monkeypatch.setattr(resume, "production_done_ns",
                        lambda run: 55.13 if Path(run).name == FIRST else 44.87)
    first = analyze_health(status_as_it_stands(study / "runs" / FIRST), [])["message"]
    second = analyze_health(status_as_it_stands(study / "runs" / SECOND), [])["message"]
    assert first == (f"This run has been extended to 55.13 ns of production; its study "
                     f"still has {SECOND} to extend, and judges every run once that is done.")
    assert second == "This run is extended next, to 55.13 ns of production."


def test_a_run_killed_in_a_later_piece_is_listed_interrupted(tmp_path, done_so_far):
    """The study killed as it extended the first run: its card said
    Interrupted, and the study's list said it was running."""
    from fastmdxplora.gui.exploration import runs_of_a_study

    study = _study(tmp_path / "study", outcome="stopped")
    _write(study / "runs" / FIRST / "segment-004" / "simulation" / "live_status.json", {
        "stage": "production", "status": "running", "current_step": 52678,
        "total_planned_steps": 55130, "last_update_timestamp": _ago(60 * 48),
        "stage_states": {"production": "current"}})
    runs = {run["run_id"]: run for run in runs_of_a_study(study)}
    # As its card says it.
    assert runs[FIRST]["state"] == "interrupted"
    assert runs[SECOND]["state"] == "completed"


def test_a_run_whose_later_piece_failed_is_listed_failed(tmp_path, done_so_far):
    """Its extension failed and the study stopped: its first result said it
    completed, and the list said so, beside a card saying it failed."""
    from fastmdxplora.gui.exploration import runs_of_a_study

    study = _study(tmp_path / "study", outcome="stopped")
    _write(study / "runs" / FIRST / "segment-004" / "simulation" / "live_status.json", {
        "stage": "production", "status": "failed", "current_step": 10,
        "total_planned_steps": 55130, "latest_error": "NaN in the coordinates",
        "last_update_timestamp": _ago(5), "stage_states": {"production": "failed"}})
    runs = {run["run_id"]: run for run in runs_of_a_study(study)}
    assert runs[FIRST]["state"] == "failed"


def test_a_piece_finished_in_place_is_counted_again(tmp_path, monkeypatch):
    """A run's production was kept by the names of its pieces: read before
    its newest piece wrote its first record, it stayed at the length before
    once the piece had finished, for the rest of the round."""
    import os

    from fastmdxplora.gui.telemetry import _production_done
    from fastmdxplora.simulation import resume

    run = _study(tmp_path / "study") / "runs" / SECOND
    sidecar = run / "segment-003" / "simulation" / "checkpoint.chk.json"
    _write(sidecar, {"finished": False})
    os.utime(sidecar, ns=(1_000_000_000, 1_000_000_000))
    monkeypatch.setattr(resume, "production_done_ns", lambda root: 44.87)
    before = _production_done(run)
    _write(sidecar, {"finished": True})
    monkeypatch.setattr(resume, "production_done_ns", lambda root: 55.13)
    assert (before, _production_done(run)) == (44.87, 55.13)


def test_at_the_start_of_a_round_the_run_extended_first_is_named(tmp_path, monkeypatch):
    """The study had decided to extend, and nothing was going yet: each run
    said the other was being extended, and itself after it."""
    from fastmdxplora.gui import telemetry
    from fastmdxplora.gui.telemetry import analyze_health, status_as_it_stands
    from fastmdxplora.orchestrator import RUN_PROCESS_FILE
    from fastmdxplora.simulation import resume

    study = _study(tmp_path / "study")
    (study / "runs" / FIRST / "segment-004").rename(study / "runs" / FIRST / "not-yet")
    _write(study / RUN_PROCESS_FILE, {"pid": 4242})
    monkeypatch.setattr(telemetry, "_what_its_process_says", lambda root, record: "running")
    monkeypatch.setattr(resume, "production_done_ns", lambda run: 44.87)
    first = analyze_health(status_as_it_stands(study / "runs" / FIRST), [])["message"]
    second = analyze_health(status_as_it_stands(study / "runs" / SECOND), [])["message"]
    assert first == "This run is extended next, to 55.13 ns of production."
    assert second == (f"Its study is extending {FIRST} to 55.13 ns of production; this run "
                      "is extended after it.")


def test_a_study_s_rounds_are_read_once_for_all_its_runs(tmp_path, done_so_far, monkeypatch):
    """Its record, its process and every run's production were read again
    for each run: 32 runs took 141 ms at every poll of the page."""
    from fastmdxplora.gui import telemetry
    from fastmdxplora.gui.exploration import runs_of_a_study

    study = _study(tmp_path / "study")
    record = json.loads((study / "stopping.json").read_text())
    manifest = json.loads((study / "batch_manifest.json").read_text())
    for n in range(3, 9):
        name = f"cln025__random-seed-{n}"
        _write(study / "runs" / name / "simulation" / "live_status.json", _done(60))
        record["runs"].append(name)
        manifest["planned"].append({"run_id": name})
    _write(study / "stopping.json", record)
    _write(study / "batch_manifest.json", manifest)
    real = telemetry._rounds_record
    read = []
    # Only this study's: a GUI an earlier test opened answers requests its
    # page left going, and reads its own (`test_a_study_s_runs_are_read_once_each`).
    monkeypatch.setattr(telemetry, "_rounds_record",
                        lambda where: (read.append(where)
                                       if Path(where).is_relative_to(study) else None)
                        or real(where))
    runs = runs_of_a_study(study)
    assert sum(run["state"] == "waiting" for run in runs) == 7
    assert len(read) == 1


def test_a_study_whose_run_was_stopped_in_its_rounds_reads_stopped(tmp_path, done_so_far):
    """Stop pressed as a run was extended: its card read stopped, and the
    study's, incomplete."""
    from fastmdxplora.gui.workspace import _state_of

    study = _study(tmp_path / "study", outcome="stopped")
    _write(study / "runs" / FIRST / "segment-004" / "simulation" / "live_status.json", {
        "stage": "production", "status": "stopped", "current_step": 10,
        "total_planned_steps": 55130, "last_update_timestamp": _ago(5),
        "stage_states": {"production": "stopped"}})
    batch = json.loads((study / "batch_manifest.json").read_text())
    assert _state_of(study, batch, None) == "stopped"


def test_a_round_not_decided_says_nothing_waiting(tmp_path, done_so_far):
    """The study's last judgement had no decision to extend: nothing waits."""
    from fastmdxplora.gui.telemetry import status_as_it_stands

    study = _study(tmp_path / "study")
    record = json.loads((study / "stopping.json").read_text())
    record["rounds"][-1].pop("decision")
    _write(study / "stopping.json", record)
    assert status_as_it_stands(study / "runs" / SECOND)["status"] == "completed"


def test_side_by_side_the_runs_of_a_round_start_together(tmp_path, monkeypatch):
    """A study that extends its runs side by side, at the start of a round:
    one was said to be extended after the other."""
    from fastmdxplora.gui import telemetry
    from fastmdxplora.gui.telemetry import analyze_health, status_as_it_stands
    from fastmdxplora.orchestrator import RUN_PROCESS_FILE
    from fastmdxplora.simulation import resume

    study = _study(tmp_path / "study")
    (study / "runs" / FIRST / "segment-004").rename(study / "runs" / FIRST / "not-yet")
    record = json.loads((study / "stopping.json").read_text())
    record["at_once"] = 2
    _write(study / "stopping.json", record)
    _write(study / RUN_PROCESS_FILE, {"pid": 4242})
    monkeypatch.setattr(telemetry, "_what_its_process_says", lambda root, record: "running")
    monkeypatch.setattr(resume, "production_done_ns", lambda run: 44.87)
    first = analyze_health(status_as_it_stands(study / "runs" / FIRST), [])["message"]
    second = analyze_health(status_as_it_stands(study / "runs" / SECOND), [])["message"]
    assert first == f"This run is extended next, to 55.13 ns of production, beside {SECOND}."
    assert second == f"This run is extended next, to 55.13 ns of production, beside {FIRST}."


def test_the_run_going_is_not_counted(tmp_path, monkeypatch):
    """The production of the run being extended was read at every poll, and
    never used: its record changes as it goes."""
    from fastmdxplora.gui import telemetry
    from fastmdxplora.gui.exploration import runs_of_a_study
    from fastmdxplora.simulation import resume

    study = _study(tmp_path / "study")
    counted = []
    monkeypatch.setattr(resume, "production_done_ns",
                        lambda run: counted.append(Path(run).name) or 44.87)
    telemetry._DONE.clear()
    runs_of_a_study(study)
    status = telemetry.status_as_it_stands(study / "runs" / FIRST)
    assert status["status"] == "running"
    assert FIRST not in counted


THIRD = "cln025__random-seed-3"


def _three(root: Path) -> Path:
    """The study of two with a third run, extended last."""
    study = _study(root)
    _write(study / "runs" / THIRD / "simulation" / "live_status.json", _done(60))
    record = json.loads((study / "stopping.json").read_text())
    record["runs"].append(THIRD)
    _write(study / "stopping.json", record)
    return study


def test_three_runs_each_say_the_runs_before_it(tmp_path, monkeypatch):
    """Three runs, the first extended and the second going: the first said
    only the second was left, and the third that it came after the second
    alone."""
    from fastmdxplora.gui import telemetry
    from fastmdxplora.gui.telemetry import analyze_health, status_as_it_stands
    from fastmdxplora.simulation import resume

    study = _three(tmp_path / "study")
    (study / "runs" / FIRST / "segment-004").rename(study / "runs" / SECOND / "segment-004")
    monkeypatch.setattr(resume, "production_done_ns",
                        lambda run: 55.13 if Path(run).name == FIRST else 44.87)
    telemetry._DONE.clear()

    def said(name):
        return analyze_health(status_as_it_stands(study / "runs" / name), [])["message"]

    assert said(FIRST) == (f"This run has been extended to 55.13 ns of production; its study "
                           f"still has {SECOND} and {THIRD} to extend, and judges every run "
                           "once that is done.")
    assert said(THIRD) == (f"Its study is extending {SECOND} to 55.13 ns of production; this "
                           "run is extended after it.")


def test_the_third_run_says_the_one_before_it(tmp_path, monkeypatch):
    """The first run going, the second and third behind: the third said it
    came after the first, the second left unsaid."""
    from fastmdxplora.gui import telemetry
    from fastmdxplora.gui.telemetry import analyze_health, status_as_it_stands
    from fastmdxplora.simulation import resume

    study = _three(tmp_path / "study")
    monkeypatch.setattr(resume, "production_done_ns", lambda run: 44.87)
    telemetry._DONE.clear()
    third = analyze_health(status_as_it_stands(study / "runs" / THIRD), [])["message"]
    assert third == (f"Its study is extending {FIRST} to 55.13 ns of production; {SECOND} "
                     "comes next, then this run.")


def test_the_runs_behind_are_worked_out_once_for_the_study(tmp_path, done_so_far,
                                                           monkeypatch):
    """Each waiting run read every run's production again: work that grew as
    the square of the runs, at every poll of the page."""
    from fastmdxplora.gui import telemetry
    from fastmdxplora.gui.exploration import runs_of_a_study

    study = _study(tmp_path / "study")
    record = json.loads((study / "stopping.json").read_text())
    manifest = json.loads((study / "batch_manifest.json").read_text())
    for n in range(3, 9):
        name = f"cln025__random-seed-{n}"
        _write(study / "runs" / name / "simulation" / "live_status.json", _done(60))
        record["runs"].append(name)
        manifest["planned"].append({"run_id": name})
    _write(study / "stopping.json", record)
    _write(study / "batch_manifest.json", manifest)
    real = telemetry._production_done
    asked = []
    # Only this study's, as above.
    monkeypatch.setattr(telemetry, "_production_done",
                        lambda where: (asked.append(Path(where).name)
                                       if Path(where).is_relative_to(study) else None)
                        or real(where))
    runs = runs_of_a_study(study)
    assert sum(run["state"] == "waiting" for run in runs) == 7
    assert len(asked) == len(set(asked)) == 7


def test_the_files_page_says_why_a_run_between_rounds_is_kept(tmp_path, done_so_far):
    """Its snapshots were said kept as "the run failed or ended without
    saying so" for a run whose pieces so far all finished."""
    from fastmdxplora.gui.files_page import _why_kept

    study = _study(tmp_path / "study")
    assert _why_kept(study / "runs" / SECOND) == "its study is still extending it in rounds"


def test_what_a_run_between_rounds_asks_for_names_no_command(tmp_path, done_so_far,
                                                             monkeypatch):
    """The Records answer gave the command that extends the run by hand,
    beside the study that extends it in its next round."""
    from fastmdxplora.gui import records_answer
    from fastmdxplora.simulation import sampling_ask

    class Ask:
        more_ns = 10.26

        def as_text(self):
            return "The means ask for 10.26 ns more."

    monkeypatch.setattr(sampling_ask, "sampling_asked_for", lambda base: Ask())
    said, command = records_answer._ask(_study(tmp_path / "study") / "runs" / SECOND)
    assert command == ""
    assert said == ("The means ask for 10.26 ns more. Its study extends this run itself, "
                    "in its next round (simulation.stop_when); nothing needs running by hand.")


def test_the_overview_says_a_run_between_rounds_waiting(tmp_path, done_so_far):
    """The Overview's phases read ok for a run its study extends, analyses and
    reports again in its next round."""
    from fastmdxplora.gui.overview_view import _phases

    study = _study(tmp_path / "study")
    phases = {phase["name"]: phase["status"] for phase in _phases(study / "runs" / SECOND)}
    assert phases == {"setup": "ok", "simulation": "waiting", "analysis": "waiting",
                      "report": "waiting"}


def test_a_run_carried_on_by_hand_is_not_said_in_rounds(tmp_path):
    """A study with no rounds, one run carried on in a piece by `fastmdx
    resume`: its pages said the study extended it in rounds."""
    from fastmdxplora.gui.exploration import runs_of_a_study

    study = _study(tmp_path / "study")
    (study / "stopping.json").unlink()
    runs = {run["run_id"]: run for run in runs_of_a_study(study)}
    assert runs[FIRST]["state"] == "running"
    assert runs[FIRST]["so_far"] is False


def test_a_run_between_rounds_is_carded_running(tmp_path, done_so_far):
    """The card of a run waiting for its study's next round said completed,
    from its Manifest, to an AI app reading the study."""
    from fastmdxplora.gui.workspace import card_of

    study = _study(tmp_path / "study")
    assert card_of(study / "runs" / SECOND)["state"] == "running"


def test_a_run_failed_in_its_first_piece_is_listed_failed(tmp_path, done_so_far):
    """The study's results are written once its rounds end: a run that failed
    before them was listed running the rounds through."""
    from fastmdxplora.gui.exploration import runs_of_a_study

    study = _study(tmp_path / "study")
    manifest = json.loads((study / "batch_manifest.json").read_text())
    manifest["runs"] = []
    manifest["planned"].append({"run_id": THIRD})
    _write(study / "batch_manifest.json", manifest)
    _write(study / "runs" / THIRD / "simulation" / "live_status.json", {
        "stage": "production", "status": "failed", "current_step": 10,
        "total_planned_steps": 100, "last_update_timestamp": _ago(60),
        "stage_states": {"production": "failed"}})
    runs = {run["run_id"]: run for run in runs_of_a_study(study)}
    assert runs[THIRD]["state"] == "failed"


def test_a_run_a_step_short_of_the_round_has_reached_it(tmp_path, monkeypatch):
    """A piece's length is whole steps: a run that did its extension, a few
    femtoseconds short of the round's length, was said extended next."""
    from fastmdxplora.gui.telemetry import analyze_health, status_as_it_stands
    from fastmdxplora.simulation import resume

    monkeypatch.setattr(resume, "production_done_ns", lambda run: 55.13 - 2e-6)
    status = status_as_it_stands(_study(tmp_path / "study") / "runs" / SECOND)
    assert analyze_health(status, [])["message"].startswith("This run has been extended")
