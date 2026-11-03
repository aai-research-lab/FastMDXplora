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
    # Read as plain text on the page: no code marks around the command.
    assert f"fastmdx resume {failed['study']} first extends {SECOND}" in card["said"]
    assert "`" not in card["said"]
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


# ---------------------------------------------------------------------------
# A study of one run cut mid round, its process killed
# ---------------------------------------------------------------------------

def _source() -> str:
    import fastmdxplora

    return str(Path(fastmdxplora.__file__).resolve().parents[1])


@pytest.fixture(scope="module")
def cut_one(tmp_path_factory):
    """A study of one run whose process is killed (SIGKILL, as the kernel
    kills one out of memory) while its round's extension runs: the piece
    has a checkpoint and has not finished, and the record says the round
    goes on."""
    import os
    import signal
    import subprocess
    import sys
    import time

    root = tmp_path_factory.mktemp("one_cut")
    pdb = root / "tri-ala.pdb"
    pdb.write_text(TRI_ALANINE)
    config = json.loads(json.dumps(_CONFIG))
    config.pop("sweep")
    config["simulation"]["stop_when"]["independent_starts"] = "not_required"
    config["systems"] = [{"id": "tri", "system": str(pdb)}]
    config["output"] = str(root / "study")
    (root / "study.yml").write_text(yaml.safe_dump(config))
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(
        [_source(), *filter(None, [os.environ.get("PYTHONPATH")])])}
    process = subprocess.Popen(
        [sys.executable, "-m", "fastmdxplora.cli.main", "explore", "-c", str(root / "study.yml")],
        cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)
    study = root / "study"
    try:
        deadline = time.monotonic() + 1200
        while time.monotonic() < deadline and process.poll() is None:
            if any((piece / "simulation" / "checkpoint.chk").is_file()
                   for piece in study.glob("segment-*/")):
                break
            time.sleep(0.1)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()
    record = json.loads((study / "stopping.json").read_text())
    assert record["outcome"] == "running", record.get("said")
    return {"study": study, "record": record}


def _length_of_the_round(record: dict) -> float:
    last = record["rounds"][-1]
    return last["production_ns"] + last["more_ns"]


def test_one_run_cut_mid_round_reads_interrupted_with_its_round(cut_one, tmp_path):
    """Its status page said only "The run ended without recording why.";
    the sentence said "the runs" of one run, and that the resume judges
    the run again, where it first carries its extension on."""
    from fastmdxplora.gui.server import _status_records
    from fastmdxplora.gui.workspace import study_state
    from fastmdxplora.simulation.sampling_ask import length_said
    from fastmdxplora.simulation.stopping import round_ended

    study = tmp_path / "study"
    shutil.copytree(cut_one["study"], study, symlinks=True)
    assert _production(study) < _length_of_the_round(cut_one["record"]) - 1e-6
    assert study_state(study) == "interrupted"
    ended = round_ended(study)
    assert ended.said.startswith("Its stopping rule's round 1 did not finish: the study's "
                                 "process ended while it extended the run by ")
    assert ended.carry_on.startswith(
        f"`fastmdx resume {study}` first carries the run's extension on to "
        f"{length_said(_length_of_the_round(cut_one['record']))}, then judges the run again")
    health = _status_records(study)["health"]
    assert health["state"] == "interrupted"
    assert health["message"] == ended.said
    assert health["explanation"] == ended.carry_on.replace("`", "")


def test_one_run_being_resumed_is_not_said_interrupted(cut_one, tmp_path):
    """While a resume carried it on, its process record still named the
    killed process, and All studies, the sidebar and the Agent said it was
    interrupted and to resume it. The resume killed in turn reads cut."""
    import os
    import signal
    import subprocess
    import sys
    import time

    from fastmdxplora.gui.records_answer import answer_from_the_records
    from fastmdxplora.gui.workspace import study_state
    from fastmdxplora.simulation.stopping import round_ended

    study = tmp_path / "study"
    shutil.copytree(cut_one["study"], study, symlinks=True)
    began = time.time()
    resuming = subprocess.Popen(
        [sys.executable, "-c", "import sys\nfrom fastmdxplora.simulation.resume import "
         "resume_study\nresume_study(sys.argv[1])", str(study)],
        env={**os.environ, "PYTHONPATH": _source()}, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        # Until the piece it carries on writes again: the resume is going.
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline and resuming.poll() is None:
            if any(status.stat().st_mtime > began for status in
                   study.glob("segment-*/simulation/live_status.json")):
                break
            time.sleep(0.2)
        assert resuming.poll() is None
        assert round_ended(study) is None
        assert study_state(study) == "running"
        assert "did not finish" not in (answer_from_the_records(study, "found") or "")
    finally:
        if resuming.poll() is None:
            os.killpg(resuming.pid, signal.SIGKILL)
        resuming.wait()
    # Killed hard in turn: cut, and said so.
    assert study_state(study) == "interrupted"
    assert round_ended(study).state == "interrupted"


def test_one_run_cut_mid_round_is_carried_on_by_its_rule(cut_one, tmp_path):
    """Its cut piece was carried on and the resume returned: the rule never
    judged it, the record stayed "running", and the study read completed."""
    from fastmdxplora.gui.workspace import study_state
    from fastmdxplora.simulation import resume
    from fastmdxplora.simulation.stopping import round_ended

    study = tmp_path / "study"
    shutil.copytree(cut_one["study"], study, symlinks=True)
    length = _length_of_the_round(cut_one["record"])
    answer = resume.resume_study(study)
    record = json.loads((study / "stopping.json").read_text())
    assert record["outcome"] in ("met", "ceiling", "rounds"), record.get("said")
    cut = len(cut_one["record"]["rounds"])
    # Judged at the round's length: the run brought to it, then judged.
    assert record["rounds"][cut]["production_ns"] == pytest.approx(length, abs=1e-6)
    assert record["rounds"][cut]["verdicts"]
    assert _production(study) <= record["max_duration_ns"] + 1e-9
    assert round_ended(study) is None
    assert study_state(study) == "completed"
    assert answer["ok"] is True, answer


# ---------------------------------------------------------------------------
# An extension that failed with an answer of its own
# ---------------------------------------------------------------------------

def _noise_in_the_checkpoint(name: str):
    """`extend_study`, with the checkpoint of the run ``name`` overwritten
    with noise while its first extension runs (put back after): the piece's
    own run refuses it, a piece that stopped with an answer."""
    import os

    from fastmdxplora.simulation import resume

    real = resume.extend_study
    done = []

    def extend_study(run, *args, **kwargs):
        run = Path(run)
        if run.name != name or done:
            return real(run, *args, **kwargs)
        done.append(run)
        checkpoint = run / "simulation" / "checkpoint.chk"
        saved = checkpoint.read_bytes()
        checkpoint.write_bytes(os.urandom(4096))
        try:
            return real(run, *args, **kwargs)
        finally:
            checkpoint.write_bytes(saved)

    return extend_study


@pytest.fixture(scope="module")
def answered(failed, tmp_path_factory):
    """The failed study resumed, the second seed's catch-up refused inside
    its own piece."""
    from fastmdxplora.simulation import resume

    study = tmp_path_factory.mktemp("answered") / "study"
    shutil.copytree(failed["study"], study, symlinks=True)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(resume, "extend_study", _noise_in_the_checkpoint(SECOND))
        with contextlib.redirect_stdout(io.StringIO()):
            answer = resume.resume_study(study)
    assert answer["ok"] is False
    assert (study / "runs" / SECOND / "segment-001").is_dir()
    return {"study": study}


def _resume_by_the_cli(study: Path) -> tuple[int, str, str]:
    from fastmdxplora.cli.main import main

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(["resume", str(study)])
    return code, out.getvalue(), err.getvalue()


def test_a_resume_that_does_not_carry_the_round_on_leaves_it_failed(failed, answered,
                                                                     tmp_path):
    """The sentence said the resume extends the second seed; the resume gave
    the piece's answer, wrote "Not applied" over the round's failure and
    printed "Batch complete"."""
    from fastmdxplora.simulation.stopping import round_ended

    study = tmp_path / "study"
    shutil.copytree(answered["study"], study, symlinks=True)
    first = failed["study"] / "runs" / "tri__random-seed-1"
    ended = round_ended(study)
    assert ended.state == "failed"
    before = (study / "stopping.json").read_text()
    code, printed, _said = _resume_by_the_cli(study)
    assert code == 1
    assert "Batch complete" not in printed
    assert "Batch failed" in printed
    assert (study / "stopping.json").read_text() == before
    assert round_ended(study) == ended
    # What the resume did, said before it.
    piece = study / "runs" / SECOND / "segment-001"
    assert ended.carry_on == (
        f"`fastmdx resume {study}` does not carry the round on: {SECOND}'s last piece, "
        "segment-001, stopped with an answer of its own, and running it again would give "
        f"the same one. To run it again anyway, move {piece} out of its run's folder, then "
        f"`fastmdx resume {study}` carries the round on.")
    manifest = json.loads((study / "batch_manifest.json").read_text())
    assert manifest["round_ended"]["carry_on"] == ended.carry_on
    # The runs at the length before the round said there, not extended.
    assert (f"tri__random-seed-1 is at {_production(first) * 1000:g} ps; the extension of "
            f"{SECOND} did not finish") in ended.said
    # What it says works: the piece moved out, the round carried on.
    shutil.move(str(piece), str(tmp_path / "segment-001"))
    with contextlib.redirect_stdout(io.StringIO()):
        from fastmdxplora.simulation import resume

        answer = resume.resume_study(study)
    record = json.loads((study / "stopping.json").read_text())
    after = [_production(study / "runs" / name) for name in record["runs"]]
    assert answer["ok"] is True, answer
    assert record["outcome"] in ("met", "ceiling", "rounds"), record.get("said")
    assert after[0] == pytest.approx(after[1], abs=1e-6)
    assert max(after) <= record["max_duration_ns"] + 1e-9


def test_one_run_whose_piece_answered_is_said_not_carried_on(one, tmp_path):
    from fastmdxplora.simulation import resume
    from fastmdxplora.simulation.stopping import round_ended

    study = tmp_path / "study"
    shutil.copytree(one["study"], study, symlinks=True)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(resume, "extend_study", _noise_in_the_checkpoint(study.name))
        with contextlib.redirect_stdout(io.StringIO()):
            first = resume.resume_study(study)
    assert first["ok"] is False
    ended = round_ended(study)
    assert ended.state == "failed"
    piece = study / "segment-001"
    assert ended.carry_on.startswith(
        f"`fastmdx resume {study}` does not carry the round on: the run's last piece, "
        f"segment-001, stopped with an answer of its own")
    assert f"move {piece} out of the study's folder" in ended.carry_on
    before = (study / "stopping.json").read_text()
    code, _printed, said = _resume_by_the_cli(study)
    assert code == 1
    assert (study / "stopping.json").read_text() == before
    # Its last line is the round's: its own refusal advised an extension
    # outside the rule, by a length in ns.
    assert f"fastmdx: {ended.as_text()}" in said
    assert "extra_ns" not in said


# ---------------------------------------------------------------------------
# A worker killed, a stop past production, a ceiling lowered
# ---------------------------------------------------------------------------

class _BrokenPool:
    """A pool whose worker ended abruptly: every future gets the error, as a
    `ProcessPoolExecutor` gives it to each run when one worker is killed."""

    submitted: list = []

    def __init__(self, *args, **kwargs):
        self._processes = {}

    def submit(self, fn, *args):
        from concurrent.futures import Future
        from concurrent.futures.process import BrokenProcessPool

        type(self).submitted.append(args)
        future = Future()
        future.set_exception(BrokenProcessPool(
            "A process in the process pool was terminated abruptly while the future "
            "was running or pending."))
        return future

    def shutdown(self, *args, **kwargs):
        pass


def test_a_killed_worker_names_no_run_and_a_run_at_the_round_s_length_is_extended(
        failed, tmp_path):
    """Every run of a broken pool got the error, and the first in order was
    named; the run at the round's length was said not finished."""
    from fastmdxplora.batch import explorer
    from fastmdxplora.batch.explorer import BatchExplorer
    from fastmdxplora.simulation.stopping import round_ended, run_until_known, targets_of

    study = tmp_path / "study"
    shutil.copytree(failed["study"], study, symlinks=True)
    record = json.loads((study / "stopping.json").read_text())
    runs = [study / "runs" / name for name in record["runs"]]
    batch = BatchExplorer.__new__(BatchExplorer)
    batch.mode, batch.workers, batch.devices = "parallel", 2, []
    batch.run_specs, batch.output_dir = [None, None], study
    # The ceiling at the first seed's length, so the round extends to it.
    stop_when = {"measures": [{"analysis": "rmsd", "standard_error": 1e-6}],
                 "max_duration_ns": _production(runs[0])}
    _BrokenPool.submitted = []
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(explorer, "ProcessPoolExecutor", _BrokenPool)
        # To a length, side by side, as a catch-up round of two runs asks.
        answers = batch._extend_runs(runs, None, total_ns=0.004)
        assert [args[-1] for args in _BrokenPool.submitted] == [0.004, 0.004]
        assert not any(answer["ok"] for answer in answers)
        # Judged from the first piece again, as round 1 was.
        (study / "stopping.json").unlink()
        with contextlib.redirect_stdout(io.StringIO()):
            run_until_known(runs, targets_of(stop_when), stop_when, record_in=study,
                            extend_all=batch._extend_runs,
                            production_of=lambda run: record["rounds"][0]["production_ns"])
    ended = round_ended(study)
    assert ended.said.startswith("Its stopping rule's round 1 failed: a worker extending "
                                 "the runs ended abruptly (BrokenProcessPool: ")
    assert "which run it was extending is not known" in ended.said
    assert "extending tri__random-seed-1 failed" not in ended.said
    from fastmdxplora.simulation.sampling_ask import length_said

    last = json.loads((study / "stopping.json").read_text())["rounds"][-1]
    length = last["production_ns"] + last["more_ns"]
    assert _production(runs[0]) >= length - 1e-6 > _production(runs[1])
    assert (f"tri__random-seed-1 was extended to {length_said(length)}; the extension of "
            f"{SECOND} did not finish") in ended.said


def _stopped_in_the_report_of(name: str):
    """The phase runner, with a stop (SIGTERM, outside production) reaching
    the report of ``name``'s joined pieces once, as a Stop pressed there."""
    from fastmdxplora.orchestrator import FastMDXplora
    from fastmdxplora.refusals import RunStopped

    real = FastMDXplora._resolve_phase_runner
    done = []

    def resolve(phase):
        runner = real(phase)
        if phase != "report":
            return runner

        def run(orchestrator, output_dir, **kwargs):
            if Path(orchestrator.output_dir).name == name and not done:
                done.append(name)
                raise RunStopped("SIGTERM")
            return runner(orchestrator=orchestrator, output_dir=output_dir, **kwargs)

        return run

    return staticmethod(resolve)


def test_a_stop_past_production_is_said_stopped_and_exits_130(failed, tmp_path):
    """Stopped in the extension's report, the round was said failed "at
    analysis", and the resume exited 1 and said the sentence twice."""
    from fastmdxplora.orchestrator import FastMDXplora
    from fastmdxplora.simulation.stopping import round_ended

    study = tmp_path / "study"
    shutil.copytree(failed["study"], study, symlinks=True)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(FastMDXplora, "_resolve_phase_runner", _stopped_in_the_report_of(SECOND))
        code, printed, said = _resume_by_the_cli(study)
    record = json.loads((study / "stopping.json").read_text())
    assert record["rounds"][-1]["stopped_by"] == "asked", record["said"]
    assert record["said"].startswith(f"Stopped when asked, while extending {SECOND}.")
    ended = round_ended(study)
    assert ended.state == "stopped"
    assert code == 130
    assert (printed + said).count("Its stopping rule's round 2 was stopped when asked") == 1


def test_a_ceiling_lowered_before_a_resume_is_not_passed(failed, tmp_path):
    """Lowered below the longest run, the shorter run was brought up to it
    and judged beside the longer run, past it."""
    from fastmdxplora.simulation import resume
    from fastmdxplora.simulation.stopping import round_ended

    study = tmp_path / "study"
    shutil.copytree(failed["study"], study, symlinks=True)
    config = yaml.safe_load((study / "resolved_config.yml").read_text())
    config["simulation"]["stop_when"]["max_duration_ns"] = 0.001
    (study / "resolved_config.yml").write_text(yaml.safe_dump(config))
    runs = {name: study / "runs" / name for name in ("tri__random-seed-1", SECOND)}
    before = {name: _production(run) for name, run in runs.items()}
    assert before["tri__random-seed-1"] > 0.001 > before[SECOND]
    assert round_ended(study).carry_on.startswith(
        f"`fastmdx resume {study}` does not judge the runs together: tri__random-seed-1 is "
        "past the 1 ps ceiling the study now sets, and carries no run on.")
    with contextlib.redirect_stdout(io.StringIO()):
        resume.resume_study(study)
    record = json.loads((study / "stopping.json").read_text())
    assert {name: _production(run) for name, run in runs.items()} == before
    assert record["outcome"] == "ceiling"
    assert record["rounds"][-1]["past_the_ceiling"] == ["tri__random-seed-1"]
    assert round_ended(study) is None


# ---------------------------------------------------------------------------
# Fix round 4: a run in error before the rule, an extension that failed in
# its analyses, records linked out, a ceiling lowered over a cut piece
# ---------------------------------------------------------------------------

def _failing_once(phase: str, name: str, error: type[BaseException] = RuntimeError):
    """The phase runner, ``phase`` of the run whose folder is ``name``
    failing with an error once (``error`` raised where it starts), not a
    piece's (the joined pieces' analyses, or the run's own)."""
    from fastmdxplora.orchestrator import FastMDXplora

    real = FastMDXplora._resolve_phase_runner
    done = []

    def resolve(asked):
        runner = real(asked)
        if asked != phase:
            return runner

        def run(orchestrator, output_dir, **kwargs):
            if Path(orchestrator.output_dir).name == name and not done:
                done.append(name)
                raise error(f"{phase} failed (made for the test)")
            return runner(orchestrator=orchestrator, output_dir=output_dir, **kwargs)

        return run

    return staticmethod(resolve)


def _analysed_again(run: Path) -> None:
    from fastmdxplora.cli.main import main

    with contextlib.redirect_stdout(io.StringIO()):
        main(["explore", "--output", str(run), "--include", "analysis", "report", "--rerun"])


@pytest.fixture(scope="module")
def three(tmp_path_factory):
    """Three seeds: the third fails in its production, before the rule; the
    rule judges the other two, and the second's extension is refused."""
    from fastmdxplora.cli.main import main
    from fastmdxplora.orchestrator import FastMDXplora
    from fastmdxplora.simulation import resume

    root = tmp_path_factory.mktemp("three")
    pdb = root / "tri-ala.pdb"
    pdb.write_text(TRI_ALANINE)
    config = json.loads(json.dumps(_CONFIG))
    config["sweep"] = {"simulation.random_seed": [1, 2, 3]}
    config["systems"] = [{"id": "tri", "system": str(pdb)}]
    config["output"] = str(root / "study")
    (root / "study.yml").write_text(yaml.safe_dump(config))
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(FastMDXplora, "_resolve_phase_runner",
                      _failing_once("simulation", "tri__random-seed-3"))
        patch.setattr(resume, "extend_study", _refusing_the_second({}))
        with contextlib.redirect_stdout(io.StringIO()):
            main(["explore", "-c", str(root / "study.yml")])
    record = json.loads((root / "study" / "stopping.json").read_text())
    assert record["runs"] == ["tri__random-seed-1", SECOND]
    assert record["rounds"][-1]["stopped_by"] == "error"
    return {"study": root / "study"}


def test_a_run_in_error_before_the_rule_does_not_stop_its_round_being_carried_on(three,
                                                                                 tmp_path):
    """Every resume said "The stopping rule's round is not carried on:
    tri__random-seed-3 did not finish.", a run the rule never judged, and
    carried nothing on, while the sentence said it extends the second seed."""
    from fastmdxplora.simulation.stopping import round_ended

    study = tmp_path / "study"
    shutil.copytree(three["study"], study, symlinks=True)
    assert round_ended(study).carry_on.startswith(f"`fastmdx resume {study}` first extends "
                                                  f"{SECOND} to ")
    _code, printed, _said = _resume_by_the_cli(study)
    assert "round is not carried on" not in printed
    record = json.loads((study / "stopping.json").read_text())
    assert record["outcome"] in ("met", "ceiling", "rounds"), record.get("said")
    after = [_production(study / "runs" / name) for name in record["runs"]]
    assert after[0] == pytest.approx(after[1], abs=1e-6)
    assert max(after) <= record["max_duration_ns"] + 1e-9
    assert round_ended(study) is None


@pytest.fixture(scope="module")
def one_analysis_failed(one, tmp_path_factory):
    """The study of one run resumed, its extension failing in the analyses
    of its joined pieces (an error, not a piece's answer)."""
    from fastmdxplora.orchestrator import FastMDXplora
    from fastmdxplora.simulation import resume

    study = tmp_path_factory.mktemp("one_analysis") / "study"
    shutil.copytree(one["study"], study, symlinks=True)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(FastMDXplora, "_resolve_phase_runner",
                      _failing_once("analysis", study.name))
        with contextlib.redirect_stdout(io.StringIO()):
            answer = resume.resume_study(study)
    assert answer["ok"] is False
    return {"study": study}


def test_one_run_whose_extension_failed_in_its_analyses_is_said_not_carried_on(
        one_analysis_failed, tmp_path):
    """The sentence said `fastmdx resume` judges the run again; the resume
    refused, "The study stopped with an answer", and advised `fastmdx
    resume` again. The run was named by its folder ("extending study")."""
    from fastmdxplora.simulation.stopping import round_ended

    study = tmp_path / "study"
    shutil.copytree(one_analysis_failed["study"], study, symlinks=True)
    ended = round_ended(study)
    assert ended.state == "failed"
    assert ended.carry_on == (
        f"`fastmdx resume {study}` does not carry the round on: the run stopped with an "
        "answer of its own in its analysis, and running it again would give the same one. "
        f"Once what it says is put right, `fastmdx explore --output {study} --include "
        "analysis report --rerun` runs its analyses and report again, then "
        f"`fastmdx resume {study}` carries the round on.")
    # Named "the run", not by its folder ("extending study").
    assert ended.said.startswith("Its stopping rule's round 2 failed: extending the run "
                                 "failed at analysis: analysis failed (made for the test).")
    before = (study / "stopping.json").read_text()
    code, _printed, said = _resume_by_the_cli(study)
    assert code == 1
    assert (study / "stopping.json").read_text() == before
    assert f"fastmdx: {ended.as_text()}" in said
    assert "What would fix it" not in said
    # What it says works.
    _analysed_again(study)
    with contextlib.redirect_stdout(io.StringIO()):
        from fastmdxplora.simulation import resume

        answer = resume.resume_study(study)
    record = json.loads((study / "stopping.json").read_text())
    assert answer["ok"] is True, answer
    assert record["outcome"] in ("met", "ceiling", "rounds"), record.get("said")
    assert round_ended(study) is None


def test_runs_one_of_whose_extensions_failed_in_its_analyses_are_said_not_carried_on(
        failed, tmp_path):
    from fastmdxplora.orchestrator import FastMDXplora
    from fastmdxplora.simulation import resume
    from fastmdxplora.simulation.stopping import round_ended

    study = tmp_path / "study"
    shutil.copytree(failed["study"], study, symlinks=True)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(FastMDXplora, "_resolve_phase_runner", _failing_once("analysis", SECOND))
        with contextlib.redirect_stdout(io.StringIO()):
            resume.resume_study(study)
    ended = round_ended(study)
    assert ended.state == "failed"
    run = study / "runs" / SECOND
    assert ended.carry_on.startswith(
        f"`fastmdx resume {study}` does not carry the round on: {SECOND} stopped with an "
        "answer of its own in its analysis")
    assert f"`fastmdx explore --output {run} --include analysis report --rerun`" in \
        ended.carry_on
    before = (study / "stopping.json").read_text()
    code, printed, said = _resume_by_the_cli(study)
    assert code == 1
    assert "Batch failed" in printed
    assert printed.rstrip().splitlines()[-1] == ended.carry_on
    assert (study / "stopping.json").read_text() == before
    assert "extra_ns" not in said
    _analysed_again(run)
    with contextlib.redirect_stdout(io.StringIO()):
        answer = resume.resume_study(study)
    record = json.loads((study / "stopping.json").read_text())
    after = [_production(study / "runs" / name) for name in record["runs"]]
    assert answer["ok"] is True, answer
    assert after[0] == pytest.approx(after[1], abs=1e-6)
    assert round_ended(study) is None


def test_a_failed_round_s_records_linked_out_are_not_read(one_analysis_failed, tmp_path):
    """All studies opened the last piece's Manifest and config where the
    study's links led (`resume._where_it_stands`, read by `round_ended`)."""
    from tests.test_the_study_pages_read_only_the_study import _OPENED, _WATCHED, _watch

    from fastmdxplora.gui.server import _confined
    from fastmdxplora.gui.workspace import studies_in, study_state

    space, outside = tmp_path / "space", tmp_path / "outside"
    outside.mkdir()
    study = space / "linked"
    shutil.copytree(one_analysis_failed["study"], study, symlinks=True)
    for name in ("segment-001/manifest.json", "segment-001/resolved_config.yml",
                 "joined/joined.json"):
        record = study / name
        assert record.is_file(), name
        moved = outside / name.replace("/", "_")
        shutil.move(str(record), moved)
        record.symlink_to(moved)
    _watch(outside)
    try:
        found = studies_in(space)
        state = _confined(study, study_state)
        opened = sorted(set(_OPENED))
    finally:
        _WATCHED.clear()
    assert opened == []
    assert found["studies"] and state == "failed"


@pytest.fixture(scope="module")
def catch_up_cut(failed, tmp_path_factory):
    """The failed study resumed in a process killed (SIGKILL) while the
    second seed's catch-up piece runs: its piece cut, the record running."""
    import os
    import signal
    import subprocess
    import sys
    import time

    study = tmp_path_factory.mktemp("catch_up_cut") / "study"
    shutil.copytree(failed["study"], study, symlinks=True)
    process = subprocess.Popen(
        [sys.executable, "-c", "import sys\nfrom fastmdxplora.simulation.resume import "
         "resume_study\nresume_study(sys.argv[1])", str(study)],
        env={**os.environ, "PYTHONPATH": _source()}, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True)
    checkpoint = study / "runs" / SECOND / "segment-001" / "simulation" / "checkpoint.chk"
    try:
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline and process.poll() is None:
            if checkpoint.is_file():
                break
            time.sleep(0.02)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()
    record = json.loads((study / "stopping.json").read_text())
    assert record["outcome"] == "running" and record["rounds"][-1]["brought_up"] == [SECOND]
    return {"study": study, "record": record}


def test_a_cut_catch_up_round_is_said_as_it_was(catch_up_cut, tmp_path):
    """Said "while it extended the runs by 1 ps" of a round extending one
    run to a length, and "first extends" where the resume carries its cut
    piece on."""
    from fastmdxplora.simulation.sampling_ask import length_said
    from fastmdxplora.simulation.stopping import round_ended

    study = tmp_path / "study"
    shutil.copytree(catch_up_cut["study"], study, symlinks=True)
    length = _length_of_the_round(catch_up_cut["record"])
    assert _production(study / "runs" / SECOND) < length - 1e-6
    ended = round_ended(study)
    assert ended.state == "interrupted"
    assert (f"ended while it extended {SECOND} to {length_said(length)}, with no record"
            in ended.said)
    assert ended.carry_on.startswith(
        f"`fastmdx resume {study}` first carries the extension of {SECOND} on to "
        f"{length_said(length)}, then judges its 2 runs again")


def test_a_ceiling_lowered_over_a_cut_piece_is_not_passed(catch_up_cut, tmp_path):
    """The cut piece was carried on to its own length, past the ceiling the
    sentence said would not be passed, and judged there."""
    from fastmdxplora.simulation import resume
    from fastmdxplora.simulation.stopping import round_ended

    study = tmp_path / "study"
    shutil.copytree(catch_up_cut["study"], study, symlinks=True)
    runs = {name: study / "runs" / name for name in ("tri__random-seed-1", SECOND)}
    before = {name: _production(run) for name, run in runs.items()}
    # Between the two: the first past it, the cut piece below it.
    ceiling = round(sum(before.values()) / 2, 6)
    config = yaml.safe_load((study / "resolved_config.yml").read_text())
    config["simulation"]["stop_when"]["max_duration_ns"] = ceiling
    (study / "resolved_config.yml").write_text(yaml.safe_dump(config))
    assert "does not judge the runs together" in round_ended(study).carry_on
    with contextlib.redirect_stdout(io.StringIO()):
        resume.resume_study(study)
    assert {name: _production(run) for name, run in runs.items()} == before
    record = json.loads((study / "stopping.json").read_text())
    assert record["outcome"] == "ceiling"
    assert round_ended(study) is None


@pytest.fixture(scope="module")
def cut_after_its_join(tmp_path_factory):
    """A study of one run whose process is killed after its round's piece
    was joined, before the joined pieces' analyses and report had run."""
    import os
    import signal
    import subprocess
    import sys
    import time

    root = tmp_path_factory.mktemp("one_cut_after_join")
    pdb = root / "tri-ala.pdb"
    pdb.write_text(TRI_ALANINE)
    config = json.loads(json.dumps(_CONFIG))
    config.pop("sweep")
    config["simulation"]["stop_when"]["independent_starts"] = "not_required"
    config["systems"] = [{"id": "tri", "system": str(pdb)}]
    config["output"] = str(root / "study")
    (root / "study.yml").write_text(yaml.safe_dump(config))
    process = subprocess.Popen(
        [sys.executable, "-m", "fastmdxplora.cli.main", "explore", "-c", str(root / "study.yml")],
        cwd=root, env={**os.environ, "PYTHONPATH": _source()}, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True)
    study = root / "study"
    try:
        deadline = time.monotonic() + 1200
        while time.monotonic() < deadline and process.poll() is None:
            if (study / "joined" / "analysis_owed.json").is_file():
                break
            time.sleep(0.1)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()
    record = json.loads((study / "stopping.json").read_text())
    assert record["outcome"] == "running", record.get("said")
    return {"study": study, "record": record}


def test_one_run_cut_after_its_join_is_said_with_its_round(cut_after_its_join, tmp_path):
    """Its status page said only that its analyses were owed, and the
    sentence that the resume carries on an extension already whole."""
    from fastmdxplora.gui.server import _status_records
    from fastmdxplora.simulation.stopping import round_ended

    study = tmp_path / "study"
    shutil.copytree(cut_after_its_join["study"], study, symlinks=True)
    ended = round_ended(study)
    assert ended.state == "interrupted"
    assert ended.carry_on.startswith(
        f"`fastmdx resume {study}` first runs the analyses and report of the run's joined "
        "pieces, then judges the run again")
    health = _status_records(study)["health"]
    assert health["message"] == ended.said
    assert health["explanation"] == ended.carry_on_on_a_page


def test_a_worker_killed_in_a_round_names_no_run(tmp_path):
    """A real pool, a worker killed (SIGKILL) while the runs are extended
    side by side: every run's future gets the error, and none is named."""
    import os
    import signal
    import subprocess
    import sys
    import time

    pdb = tmp_path / "tri-ala.pdb"
    pdb.write_text(TRI_ALANINE)
    config = json.loads(json.dumps(_CONFIG))
    config["execution"] = {"mode": "parallel", "workers": 2}
    config["systems"] = [{"id": "tri", "system": str(pdb)}]
    config["output"] = str(tmp_path / "study")
    (tmp_path / "study.yml").write_text(yaml.safe_dump(config))
    process = subprocess.Popen(
        [sys.executable, "-m", "fastmdxplora.cli.main", "explore", "-c",
         str(tmp_path / "study.yml")],
        cwd=tmp_path, env={**os.environ, "PYTHONPATH": _source()},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    held = tmp_path / "study" / "runs" / SECOND / "segment-001" / ".fastmdxplora_run.json"
    killed = None
    try:
        deadline = time.monotonic() + 1200
        while time.monotonic() < deadline and process.poll() is None and killed is None:
            try:
                killed = json.loads(held.read_text())["pid"]
            except (OSError, ValueError, KeyError):
                time.sleep(0.05)
        assert killed is not None
        os.kill(killed, signal.SIGKILL)
        process.wait(timeout=1200)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
    from fastmdxplora.simulation.stopping import round_ended

    ended = round_ended(tmp_path / "study")
    assert ended.state == "failed"
    assert ended.said.startswith("Its stopping rule's round 1 failed: a worker extending "
                                 "the runs ended abruptly (BrokenProcessPool")
    assert "extending tri__random-seed" not in ended.said
