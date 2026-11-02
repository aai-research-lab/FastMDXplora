"""A study whose stopping rule's round failed is said failed everywhere.

A chignolin study on `aailab01` ran until what it asked was determined
(`simulation.stop_when`), and the kernel killed a worker while its runs were
extended (`BrokenProcessPool`). `stopping.json` said the round failed, and
the CLI printed "Batch complete: 3 ok, 0 error(s)" after it and exited 0;
the GUI's status, All studies and the Agent read the study completed, each
from its runs, every one of which had ended well. The study's state is now
one derivation (`stopping.round_ended`), read by the CLI's last line and exit
code, `batch_manifest.json`, the GUI and the Agent, with what `fastmdx
resume` would run.

Here a real study of two tri-alanine seeds, run one after another, is
extended once, and the second seed's extension is refused in its planning,
before any piece of it is made, as the killed worker's was: its checkpoint
is not there while the extension plans (moved aside, and put back after),
so the first seed is extended and the second is not, and each run's own
record says it completed.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import shutil
from pathlib import Path

import pytest
import yaml

try:
    import mdtraj  # noqa: F401
    import openmm  # noqa: F401
    import pdbfixer  # noqa: F401
    HAS_BACKENDS = True
except ImportError:  # pragma: no cover - the backends are optional
    HAS_BACKENDS = False

pytestmark = pytest.mark.skipif(not HAS_BACKENDS, reason="needs OpenMM, MDTraj and PDBFixer")

from tests.test_a_real_study_runs_end_to_end import TRI_ALANINE  # noqa: E402

#: 300 production steps a piece; the rule cannot be met (±1e-06 nm), so the
#: study extends its runs after the first piece.
_CONFIG = {
    "sweep": {"simulation.random_seed": [1, 2]},
    "execution": {"mode": "sequential"},
    "setup": {"ph": 7.0, "solvent_padding_nm": 1.2, "nonbonded_cutoff_nm": 0.9},
    "simulation": {"platform": "CPU", "production_steps": 300,
                   "nvt_steps": 100, "npt_steps": 100, "timestep_fs": 2,
                   "trajectory_interval_steps": 50, "state_interval_steps": 25,
                   "checkpoint_interval_steps": 100,
                   "stop_when": {"measures": [{"analysis": "rmsd", "standard_error": 1e-6}],
                                 "max_duration_ns": 0.006}},
    "analysis": {"include": ["rmsd"]},
    "report": {"document": True, "slides": False, "pdf": False, "bundle": False},
}

SECOND = "tri__random-seed-2"


def _refusing_the_second(seen: dict):
    """`extend_study`, with the second seed's checkpoint moved aside while
    its extension plans, which refuses it with no checkpoint to go on from.
    Each call notes the runs the study's batch manifest lists."""
    from fastmdxplora.simulation import resume

    real = resume.extend_study

    def extend_study(run, *args, **kwargs):
        run = Path(run)
        manifest = json.loads((run.parent.parent / "batch_manifest.json").read_text())
        seen.setdefault("listed", []).append(len(manifest.get("runs") or []))
        if run.name != SECOND:
            return real(run, *args, **kwargs)
        aside = run / "aside"
        aside.mkdir()
        moved = list((run / "simulation").glob("checkpoint.chk*"))
        for checkpoint in moved:
            checkpoint.rename(aside / checkpoint.name)
        try:
            return real(run, *args, **kwargs)
        finally:
            for checkpoint in moved:
                (aside / checkpoint.name).rename(checkpoint)
            aside.rmdir()

    return extend_study


@pytest.fixture(scope="module")
def failed(tmp_path_factory):
    from fastmdxplora.cli.main import main

    root = tmp_path_factory.mktemp("round_failed")
    pdb = root / "tri-ala.pdb"
    pdb.write_text(TRI_ALANINE)
    config = json.loads(json.dumps(_CONFIG))
    config["systems"] = [{"id": "tri", "system": str(pdb)}]
    config["output"] = str(root / "study")
    (root / "study.yml").write_text(yaml.safe_dump(config))
    seen: dict = {}
    out = io.StringIO()
    with pytest.MonkeyPatch.context() as patch:
        from fastmdxplora.simulation import resume

        patch.setattr(resume, "extend_study", _refusing_the_second(seen))
        with contextlib.redirect_stdout(out):
            code = main(["explore", "-c", str(root / "study.yml")])
    return {"study": root / "study", "code": code, "printed": out.getvalue(), "seen": seen,
            "root": root}


def test_the_round_failed_as_recorded(failed):
    record = json.loads((failed["study"] / "stopping.json").read_text())
    assert record["outcome"] == "stopped"
    assert record["rounds"][-1]["stopped_by"] == "error"
    assert "has no checkpoint" in record["said"]
    # Each run's own record says it completed: the extension never began.
    assert not (failed["study"] / "runs" / SECOND / "segment-001").exists()


def test_the_cli_says_it_failed_last_and_exits_non_zero(failed):
    printed = [line for line in failed["printed"].splitlines() if line.strip()]
    assert failed["code"] == 1
    assert not any(line.startswith("Batch complete") for line in printed)
    assert any(line.startswith("Batch failed: 2 ok") for line in printed)
    assert printed[-2].startswith("Its stopping rule's round 1 failed: extending "
                                  f"{SECOND} failed at planning")
    assert re.match(rf"`fastmdx resume {re.escape(str(failed['study']))}` first extends "
                    rf"{SECOND} to [\d.]+ ps, the length of tri__random-seed-1, then judges "
                    "its 2 runs again and extends them until what it asks is determined",
                    printed[-1])
    assert "at most 6 ps of production in each run" in printed[-1]


def test_the_batch_manifest_says_it(failed):
    manifest = json.loads((failed["study"] / "batch_manifest.json").read_text())
    assert [run["status"] for run in manifest["runs"]] == ["ok", "ok"]
    ended = manifest.get("round_ended") or {}
    assert ended.get("state") == "failed"
    assert ended.get("command") == f"fastmdx resume {failed['study']}"


def test_every_page_of_the_gui_reads_it_failed(failed):
    from fastmdxplora.gui.telemetry import analyze_health
    from fastmdxplora.gui.workspace import study_state

    # All studies' cards, the sidebar and the Overview read this state.
    assert study_state(failed["study"]) == "failed"
    health = analyze_health({}, [], root=failed["study"])
    assert health["state"] == "failed"
    assert health["message"].startswith("Its stopping rule's round 1 failed")
    # Which runs the round had extended, and how far the other got: its
    # extension did not finish, and may have run part of the way.
    assert re.search(r"tri__random-seed-1 was extended to [\d.]+ ps; the extension of "
                     r"tri__random-seed-2 did not finish \(tri__random-seed-2 has 0\.6 ps of "
                     r"production\), so the runs are not at one length\.", health["message"])
    assert f"fastmdx resume {failed['study']}" in health["explanation"]


def test_the_agent_reads_it_failed(failed):
    from fastmdxplora.gui.agent_panel import _stopping_summary
    from fastmdxplora.gui.records_answer import answer_from_the_records

    answer = answer_from_the_records(failed["study"], "found")
    assert "round 1 failed" in answer
    assert f"fastmdx resume {failed['study']}" in answer
    context = _stopping_summary(failed["study"])
    assert "the study failed (not completed)" in context


def test_the_report_says_it_first(failed):
    report = (failed["study"] / "comparison" / "comparison_report.md").read_text()
    lead = report.split("\n\n")[1]
    assert lead.startswith("**Failed.** Its stopping rule's round 1 failed")
    assert f"`fastmdx resume {failed['study']}`" in lead
    assert "**Stopped early.**" not in report


def test_the_runs_are_listed_while_they_are_extended(failed):
    """The manifest listed only the plan while the rounds ran, so each
    extension built the comparison across the runs from none ("Comparison
    report needs >=2 successful runs (found 0)")."""
    assert failed["seen"]["listed"] and set(failed["seen"]["listed"]) == {2}


def _with_record(failed, tmp_path, change) -> Path:
    study = tmp_path / "study"
    shutil.copytree(failed["study"], study, symlinks=True)
    record = json.loads((study / "stopping.json").read_text())
    change(record)
    (study / "stopping.json").write_text(json.dumps(record))
    return study


def test_a_round_stopped_when_asked_is_said_stopped(failed, tmp_path):
    from fastmdxplora.gui.telemetry import analyze_health
    from fastmdxplora.gui.workspace import study_state

    def asked(record):
        record["rounds"][-1]["stopped_by"] = "asked"
        record["said"] = record["rounds"][-1]["said"] = (
            f"Stopped when asked, while extending {SECOND}. Resuming the study judges "
            "the runs again and carries on from there.")

    study = _with_record(failed, tmp_path, asked)
    assert study_state(study) == "stopped"
    health = analyze_health({}, [], root=study)
    assert health["message"].startswith(
        f"Its stopping rule's round 1 was stopped when asked, while extending {SECOND}.")
    assert f"fastmdx resume {study}" in health["explanation"]


def test_a_study_whose_rounds_ended_well_is_unchanged(failed, tmp_path):
    from fastmdxplora.gui.telemetry import analyze_health
    from fastmdxplora.gui.workspace import study_state

    def ceiling(record):
        record["outcome"] = "ceiling"
        record["rounds"][-1].update({"decision": "ceiling", "said": "Stopped at the ceiling."})
        record["rounds"][-1].pop("stopped_by")
        record["said"] = "Stopped at the ceiling."

    study = _with_record(failed, tmp_path, ceiling)
    assert study_state(study) == "completed"
    assert analyze_health({}, [], root=study)["message"] == "Every run of this study completed."


def test_a_resume_whose_round_fails_again_is_not_done(failed, tmp_path):
    from fastmdxplora.simulation import resume

    study = tmp_path / "study"
    shutil.copytree(failed["study"], study, symlinks=True)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(resume, "extend_study", _refusing_the_second({}))
        answer = resume.resume_study(study)
    assert answer["ok"] is False
    assert answer["error"].startswith("Its stopping rule's round")
    assert "failed: extending " + SECOND in answer["error"]


def _production(run: Path) -> float:
    from fastmdxplora.simulation.resume import production_done_ns

    return production_done_ns(run)


def test_the_comparison_report_says_it_once(failed):
    report = (failed["study"] / "comparison" / "comparison_report.md").read_text()
    assert report.count("Its stopping rule's round 1 failed") == 1
    assert report.count(f"`fastmdx resume {failed['study']}`") == 1


def test_the_overview_s_card_says_it_with_the_command(failed):
    """The card said "Stopped early" and the record's own sentence beside a
    header saying Failed, with no command, and the shortest run's length as
    each run's."""
    from fastmdxplora.gui.stopping_view import stopping_payload

    card = stopping_payload(failed["study"])
    assert card["outcome_label"] == "Failed"
    assert card["said"].startswith("Its stopping rule's round 1 failed")
    assert f"`fastmdx resume {failed['study']}`" in card["said"]
    assert card["carry_on"] == f"fastmdx resume {failed['study']}"
    assert re.fullmatch(r"0\.6 ps to [\d.]+ ps", card["production_said"])


@pytest.mark.parametrize("ended", ["met", "ceiling", "rounds", "no mean"])
def test_rounds_that_ended_as_the_rule_decides_are_unchanged(failed, tmp_path, ended):
    from fastmdxplora.gui.workspace import study_state
    from fastmdxplora.simulation.stopping import round_ended

    def change(record):
        last = record["rounds"][-1]
        for key in ("stopped_by", "extended", "more_ns"):
            last.pop(key, None)
        record["outcome"] = "stopped" if ended == "no mean" else ended
        last["decision"] = {"met": "met", "ceiling": "ceiling", "rounds": "extend",
                            "no mean": "stopped"}[ended]
        if ended == "rounds":
            last["more_ns"] = 0.0018
        if ended == "no mean":
            last["stopped_by"] = "no mean"
        record["said"] = last["said"] = "Stopped as the rule decides."

    study = _with_record(failed, tmp_path, change)
    assert round_ended(study) is None
    assert study_state(study) == "completed"


def test_a_round_cut_with_its_process_gone_is_said_interrupted(failed, tmp_path):
    """Killed hard mid round (out of memory), the record is left "running"
    and no process runs it: read completed from its runs."""
    import subprocess
    import sys

    from fastmdxplora.gui.records_answer import answer_from_the_records
    from fastmdxplora.gui.telemetry import analyze_health
    from fastmdxplora.gui.workspace import study_state
    from fastmdxplora.orchestrator import RUN_PROCESS_FILE, this_machine

    def running(record):
        last = record["rounds"][-1]
        for key in ("stopped_by", "extended"):
            last.pop(key, None)
        last["decision"] = "extend"
        record["outcome"] = "running"
        record["said"] = last["said"] = "Extending by 1.8 ps, to 2.4 ps of the 6 ps allowed."

    study = _with_record(failed, tmp_path, running)
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    (study / RUN_PROCESS_FILE).write_text(json.dumps({"pid": gone.pid, **this_machine()}))
    assert study_state(study) == "interrupted"
    health = analyze_health({}, [], root=study)
    assert health["state"] == "interrupted"
    assert health["message"].startswith("Its stopping rule's round 1 did not finish: the "
                                        "study's process ended while it extended the runs")
    assert f"fastmdx resume {study}" in health["explanation"]
    assert "did not finish" in answer_from_the_records(study, "found")


def test_a_failed_run_is_said_before_a_round_stopped_when_asked(failed, tmp_path):
    from fastmdxplora.gui.workspace import study_state

    def asked(record):
        record["rounds"][-1]["stopped_by"] = "asked"
        record["said"] = record["rounds"][-1]["said"] = (
            f"Stopped when asked, while extending {SECOND}. Resuming the study judges "
            "the runs again and carries on from there.")

    study = _with_record(failed, tmp_path, asked)
    status = study / "runs" / SECOND / "simulation" / "live_status.json"
    record = json.loads(status.read_text())
    record.update({"status": "failed", "latest_error": "Simulation failed."})
    status.write_text(json.dumps(record))
    assert study_state(study) == "failed"


def _rerun(study: Path) -> tuple[int, list[str]]:
    from fastmdxplora.cli.main import main

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = main(["explore", "--output", str(study), "--include", "analysis", "report",
                     "--rerun"])
    return code, [line for line in out.getvalue().splitlines() if line.strip()]


def test_a_failed_study_analysed_again_is_said_failed(failed, tmp_path):
    """Analysed again, the rule is not applied, and the study said "Batch
    complete" with exit 1, and its manifest dropped how the round ended."""
    study = tmp_path / "study"
    shutil.copytree(failed["study"], study, symlinks=True)
    code, printed = _rerun(study)
    assert code == 1
    assert not any(line.startswith("Batch complete") for line in printed)
    assert any(line.startswith("Batch failed") for line in printed)
    assert printed[-2].startswith("Its stopping rule's round 1 failed")
    assert printed[-1].startswith(f"`fastmdx resume {study}`")
    manifest = json.loads((study / "batch_manifest.json").read_text())
    assert (manifest.get("round_ended") or {}).get("state") == "failed"


def test_a_round_stopped_when_asked_exits_as_interrupted(failed, tmp_path):
    def asked(record):
        record["rounds"][-1]["stopped_by"] = "asked"
        record["said"] = record["rounds"][-1]["said"] = (
            f"Stopped when asked, while extending {SECOND}. Resuming the study judges "
            "the runs again and carries on from there.")

    study = _with_record(failed, tmp_path, asked)
    code, printed = _rerun(study)
    assert code == 130
    assert printed[-2].startswith("Its stopping rule's round 1 was stopped when asked")


def test_the_resume_brings_the_runs_to_one_length_and_never_past_the_ceiling(failed, tmp_path):
    """After a partial round (the first seed extended, the second not), the
    resume extended every run from the shortest, and the first went past
    the ceiling; and what it was said to run was not what it ran."""
    from fastmdxplora.simulation import resume
    from fastmdxplora.simulation.stopping import round_ended

    study = tmp_path / "study"
    shutil.copytree(failed["study"], study, symlinks=True)
    runs = {name: study / "runs" / name for name in ("tri__random-seed-1", SECOND)}
    before = {name: _production(run) for name, run in runs.items()}
    first = before["tri__random-seed-1"]
    assert before[SECOND] < first
    said = round_ended(study).carry_on
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        answer = resume.resume_study(study)
    record = json.loads((study / "stopping.json").read_text())
    after = {name: _production(run) for name, run in runs.items()}
    ceiling = record["max_duration_ns"]
    assert all(ns <= ceiling + 1e-9 for ns in after.values()), after
    assert after["tri__random-seed-1"] == pytest.approx(after[SECOND], abs=1e-6)
    assert answer["ok"] is True, answer
    # What it was said to run is what it ran: the second seed brought to
    # the first's length, and both judged there.
    assert said.startswith(f"`fastmdx resume {study}` first extends {SECOND} to "
                           f"{first * 1000:g} ps, the length of tri__random-seed-1, then ")
    assert f"Extending {SECOND} to {first * 1000:g} ps" in out.getvalue()
    brought = [r for r in record["rounds"] if r.get("brought_up")]
    assert brought and brought[0]["brought_up"] == [SECOND]
    judged = record["rounds"][record["rounds"].index(brought[0]) + 1]
    assert judged["production_ns"] == pytest.approx(first, abs=1e-6)


# ---------------------------------------------------------------------------
# A study of one run
# ---------------------------------------------------------------------------

def _refusing_once():
    """`extend_study`, its first call refused at planning, with the run's
    checkpoint moved aside, as the killed worker's extension was."""
    from fastmdxplora.simulation import resume

    real = resume.extend_study
    calls = []

    def extend_study(run, *args, **kwargs):
        run = Path(run)
        calls.append(run)
        if len(calls) > 1:
            return real(run, *args, **kwargs)
        aside = run / "aside"
        aside.mkdir()
        moved = list((run / "simulation").glob("checkpoint.chk*"))
        for checkpoint in moved:
            checkpoint.rename(aside / checkpoint.name)
        try:
            return real(run, *args, **kwargs)
        finally:
            for checkpoint in moved:
                (aside / checkpoint.name).rename(checkpoint)
            aside.rmdir()

    return extend_study


@pytest.fixture(scope="module")
def one(tmp_path_factory):
    from fastmdxplora.cli.main import main

    root = tmp_path_factory.mktemp("one_round_failed")
    pdb = root / "tri-ala.pdb"
    pdb.write_text(TRI_ALANINE)
    config = json.loads(json.dumps(_CONFIG))
    config.pop("sweep")
    config["simulation"]["stop_when"]["independent_starts"] = "not_required"
    config["systems"] = [{"id": "tri", "system": str(pdb)}]
    config["output"] = str(root / "study")
    (root / "study.yml").write_text(yaml.safe_dump(config))
    out = io.StringIO()
    with pytest.MonkeyPatch.context() as patch:
        from fastmdxplora.simulation import resume

        patch.setattr(resume, "extend_study", _refusing_once())
        with contextlib.redirect_stdout(out):
            code = main(["explore", "-c", str(root / "study.yml")])
    return {"study": root / "study", "code": code,
            "printed": [line for line in out.getvalue().splitlines() if line.strip()]}


def test_one_run_whose_round_failed_is_said_failed_by_the_cli(one):
    record = json.loads((one["study"] / "stopping.json").read_text())
    assert record["rounds"][-1]["stopped_by"] == "error"
    assert one["code"] == 1
    assert one["printed"][-2].startswith("Its stopping rule's round 1 failed")
    assert one["printed"][-1].startswith(f"`fastmdx resume {one['study']}` judges the run "
                                         "again and extends it")


def test_one_run_whose_round_failed_is_said_failed_on_every_page(one):
    from fastmdxplora.gui.records_answer import answer_from_the_records
    from fastmdxplora.gui.report_dashboard import _study_state
    from fastmdxplora.gui.server import _status_records
    from fastmdxplora.gui.workspace import study_state

    study = one["study"]
    assert study_state(study) == "failed"
    health = _status_records(study)["health"]
    assert health["state"] == "failed"
    assert health["message"].startswith("Its stopping rule's round 1 failed")
    assert f"fastmdx resume {study}" in health["explanation"]
    assert "round 1 failed" in answer_from_the_records(study, "found")
    manifest = json.loads((study / "manifest.json").read_text())
    assert _study_state(manifest, {}, root=study)[0] == "failed"


def test_one_run_whose_round_failed_is_judged_again_by_resume(one, tmp_path):
    """Its run finished, so `fastmdx resume` said there was nothing to carry
    on, and the rule was never applied again."""
    from fastmdxplora.simulation import resume
    from fastmdxplora.simulation.stopping import round_ended

    study = tmp_path / "study"
    shutil.copytree(one["study"], study, symlinks=True)
    before = _production(study)
    answer = resume.resume_study(study)
    record = json.loads((study / "stopping.json").read_text())
    assert record["outcome"] in ("met", "ceiling", "rounds"), record.get("said")
    assert _production(study) > before
    assert _production(study) <= record["max_duration_ns"] + 1e-9
    assert round_ended(study) is None
    assert answer["ok"] is True, answer


def test_the_cli_and_the_agent_count_the_runs_alike(failed, tmp_path):
    """A study stopped in its rounds: the CLI said "2 ok", from the first
    pieces, and the Agent "0 completed", from each run as it stands."""
    from types import SimpleNamespace

    from fastmdxplora.batch.explorer import BatchExplorer
    from fastmdxplora.gui.records_answer import answer_from_the_records

    def asked(record):
        record["rounds"][-1]["stopped_by"] = "asked"
        record["said"] = record["rounds"][-1]["said"] = (
            "Stopped when asked, while extending tri__random-seed-1. Resuming the study "
            "judges the runs again and carries on from there.")

    study = _with_record(failed, tmp_path, asked)
    status = study / "runs" / "tri__random-seed-1" / "segment-001" / "simulation" / \
        "live_status.json"
    record = json.loads(status.read_text())
    record.update({"status": "stopped", "latest_error": "Stopped when asked."})
    status.write_text(json.dumps(record))
    batch = BatchExplorer.__new__(BatchExplorer)
    batch.output_dir = study
    batch.results = [SimpleNamespace(status="ok", error_type=None, phases=())
                     for _ in range(2)]
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        batch._print_summary()
    counted = re.search(r"Batch \w+: (\d+) ok", out.getvalue())
    agent = re.search(r"study of 2 runs, (\d+) completed",
                      answer_from_the_records(study, "found"))
    assert counted and agent
    assert counted.group(1) == agent.group(1) == "1"
