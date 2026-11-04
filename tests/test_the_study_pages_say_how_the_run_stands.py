"""The study pages say how the run stands while they read only the study.

Holding the Overview's and Results' reads to the study's own results
(`record_access`) refused two kinds of record they need to say how the run
stands. The run's own process record is a hidden file: refused, a run whose
process was gone read as running ("Latest status: running", the phase strip
"Simulation running") until a day of silence. And a run of a study of several
reads its study's `stopping.json` and its other runs' records to know it waits
for the study's next round: refused, it read as completed, every phase done.

Both are read again, as Problems and the status card read them, and the pages
say what they said before they were confined (`1094e1c9`). A record linked out
of the study is still not read, and is named as not read.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_a_resumed_study_reads_as_one_run import STOP, _piece, _stopped_study, _write
from tests.test_a_run_between_rounds_is_said_waiting import FIRST, SECOND, _study
from tests.test_a_run_that_ended_unsaid_is_interrupted import (
    _a_gone_process,
    _a_run_said_it_was_going,
    _ago,
    _its_process,
)

#: What a record outside the study says, which no page may show.
TEXT = "OUTSIDE-TEXT-not-for-any-page"

PUBLIC = pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])


def _link_out(root: Path, relative: str, tmp_path: Path) -> None:
    """``relative`` in the study, a link to a record outside it."""
    outside = tmp_path / "elsewhere" / Path(relative).name
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text(json.dumps({"analysis": TEXT, "findings": {"mean": {
        "mean": 4242.0, "standard_error": 1.0, "unit": TEXT}}}), encoding="utf-8")
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.symlink_to(outside)
    except OSError:
        pytest.skip("this system cannot create symbolic links")


def _pages(root: Path, workspace: Path, *, public: bool) -> dict:
    """What the Overview and Results are given of the run."""
    from fastmdxplora.gui.exploration import DashboardRuntime
    from tests.test_a_public_dashboard_answers_only_what_it_lists import _ask, _serving

    runtime = DashboardRuntime(workspace_root=root, exploration_root=workspace,
                               active_root=root)
    said = {}
    with _serving(runtime, allow_control=not public) as url:
        for route in ("/api/overview", "/api/results", "/api/status"):
            status, body = _ask(url + route)
            assert status == 200
            assert TEXT not in body.decode("utf-8")
            said[route] = json.loads(body)
    overview, results, run = said["/api/overview"], said["/api/results"], said["/api/status"]
    summary = {row["label"]: row["value"] for row in results.get("summary") or []}
    return {"phases": {phase["name"]: phase["status"] for phase in overview["phases"]},
            "project": summary.get("Project status"), "latest": summary.get("Latest status"),
            "overview_unavailable": overview.get("unavailable"),
            "results_unavailable": results.get("unavailable"),
            # The status card's, the phase strip's and the headline's.
            "status": (run["status"].get("status"), run["health"].get("state"),
                       run["phases"], run["times"].get("production_ns")),
            "status_unavailable": run.get("unavailable")}


@PUBLIC
def test_a_run_whose_process_is_gone_reads_interrupted(tmp_path, public):
    root = _a_run_said_it_was_going(tmp_path / "study", last=_ago(5))
    _write(root / "manifest.json", {"phases": [{"name": "setup", "status": "ok"}]})
    _its_process(root, _a_gone_process())
    _link_out(root, "analysis/rmsd/options.json", tmp_path)

    said = _pages(root, tmp_path, public=public)

    # Its process record read; the link not followed. Both rows are one
    # reading, the run's state (`study_state`): its Manifest's one phase said
    # "completed" beside "interrupted" (1094e1c9), two states on one page.
    assert (said["project"], said["latest"]) == ("interrupted", "interrupted")
    assert said["phases"] == {"setup": "ok"}
    assert said["status"] == ("interrupted", "interrupted", ["setup"], None)
    assert said["overview_unavailable"] == ["analysis/rmsd/options.json"]
    assert said["results_unavailable"] == ["analysis/rmsd/options.json"]
    assert said["status_unavailable"] is None


@PUBLIC
def test_a_carry_on_killed_in_its_piece_reads_stopped_not_running(tmp_path, public):
    root = _piece(_stopped_study(tmp_path / "study"), status="running", step=3600,
                  minutes_ago=1)
    _write(root / "manifest.json", {"phases": [
        {"name": "setup", "status": "ok"},
        {"name": "simulation", "status": "error", "message": STOP,
         "refusal": {"code": "simulation.run.stopped"}}]})
    _its_process(root / "segment-001", _a_gone_process())
    _link_out(root, "analysis/rmsd/options.json", tmp_path)

    said = _pages(root, tmp_path, public=public)

    # The phase strip said "Simulation running" beside the card's
    # "Interrupted"; 1094e1c9 said it stopped.
    assert said["phases"] == {"setup": "ok", "simulation": "stopped"}
    # One reading on both rows: the carry-on was killed, not stopped (its
    # Manifest's stop is the first piece's, which was carried on).
    assert (said["project"], said["latest"]) == ("interrupted", "interrupted")
    assert said["status"][:3] == ("interrupted", "interrupted", ["setup", "simulation"])
    assert said["status"][3] == pytest.approx(0.0128)
    assert said["overview_unavailable"] == ["analysis/rmsd/options.json"]
    assert said["results_unavailable"] == ["analysis/rmsd/options.json"]


@pytest.fixture
def done_so_far(monkeypatch):
    """Each run's production as its pieces count it, as the rounds tests
    take it: the second run at the third round's 44.87 ns."""
    from fastmdxplora.simulation import resume

    monkeypatch.setattr(resume, "production_done_ns", lambda run: 44.87)


@PUBLIC
def test_a_run_waiting_for_its_study_s_next_round_reads_waiting(tmp_path, done_so_far, public):
    study = _study(tmp_path / "study")
    run = study / "runs" / SECOND
    _link_out(run, "analysis/rmsd/options.json", tmp_path)

    said = _pages(run, study, public=public)

    # As 1094e1c9 said it, from the study's own record of its rounds.
    assert said["phases"] == {"setup": "ok", "simulation": "waiting", "analysis": "waiting",
                              "report": "waiting"}
    # One reading on both rows: the run waits for its study's next round,
    # as its status card says (its Manifest's "completed" was its pieces so far).
    assert (said["project"], said["latest"]) == ("waiting", "waiting")
    assert said["status"] == ("waiting", "waiting",
                              ["setup", "simulation", "analysis", "report"], None)
    assert said["overview_unavailable"] == ["analysis/rmsd/options.json"]
    assert said["results_unavailable"] == ["analysis/rmsd/options.json"]


def test_a_run_s_record_linked_to_another_run_of_its_study_is_not_read(tmp_path, done_so_far):
    """The study's runs are read as the study records them, each where it
    is: a link from the open run into another run is a link out of it."""
    study = _study(tmp_path / "study")
    run = study / "runs" / SECOND
    _link_out(study / "runs" / FIRST, "analysis/rmsd/options.json", tmp_path)
    (run / "analysis" / "rmsd").mkdir(parents=True)
    (run / "analysis" / "rmsd" / "options.json").symlink_to(
        study / "runs" / FIRST / "analysis" / "rmsd" / "options.json")

    said = _pages(run, study, public=True)

    assert said["latest"] == "waiting"
    assert said["overview_unavailable"] == ["analysis/rmsd/options.json"]


def test_a_study_s_record_of_its_rounds_linked_out_is_not_read(tmp_path, done_so_far):
    """Its `stopping.json` linked out of the study is refused and named as
    the run names it; nothing then says the run waits."""
    study = _study(tmp_path / "study")
    outside = tmp_path / "elsewhere" / "stopping.json"
    outside.parent.mkdir()
    (study / "stopping.json").rename(outside)
    (study / "stopping.json").symlink_to(outside)

    said = _pages(study / "runs" / SECOND, study, public=True)

    assert said["latest"] == "completed"
    assert said["phases"] == {"setup": "ok", "simulation": "ok", "analysis": "ok",
                              "report": "ok"}
    assert said["overview_unavailable"] == ["../../stopping.json"]
    # The status card neither reads it nor says the run waits.
    assert said["status"][0] != "waiting"
    assert said["status_unavailable"] == ["../../stopping.json"]


@PUBLIC
def test_a_result_linked_to_the_run_s_process_record_is_not_read(tmp_path, public):
    """The process record is read by its own name only: a result linked to
    it is a record that is not one of the study's results, and not read,
    while the run is still told from its process (interrupted)."""
    root = _a_run_said_it_was_going(tmp_path / "study", last=_ago(5))
    _write(root / "manifest.json", {"phases": [{"name": "setup", "status": "ok"}]})
    pid = _a_gone_process()
    _its_process(root, pid)
    (root / "analysis" / "rmsd").mkdir(parents=True)
    (root / "analysis" / "rmsd" / "rmsd.dat").symlink_to(root / ".fastmdxplora_run.json")

    from tests.test_a_public_dashboard_answers_only_what_it_lists import _ask, _serving
    from fastmdxplora.gui.exploration import DashboardRuntime

    runtime = DashboardRuntime(workspace_root=root, exploration_root=tmp_path,
                               active_root=root)
    with _serving(runtime, allow_control=not public) as url:
        status, body = _ask(url + "/api/series?analysis=rmsd")
    assert status == 200
    answer = json.loads(body)
    assert answer["ok"] is False and pid not in (answer.get("y") or [])
    assert answer["unavailable"] == ["analysis/rmsd/rmsd.dat"]
    assert "my_study.py" not in body.decode("utf-8")

    said = _pages(root, tmp_path, public=public)

    assert said["status"] == ("interrupted", "interrupted", ["setup"], None)
    assert said["latest"] == "interrupted"
