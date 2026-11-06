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
import subprocess
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
#: study extends its runs after the first piece. Minimization capped at 100
#: iterations: run to convergence it took most of each run's first piece.
_CONFIG = {
    "sweep": {"simulation.random_seed": [1, 2]},
    "execution": {"mode": "sequential"},
    "setup": {"ph": 7.0, "solvent_padding_nm": 1.2, "nonbonded_cutoff_nm": 0.9},
    "simulation": {"platform": "CPU", "production_steps": 300,
                   "nvt_steps": 100, "npt_steps": 100, "timestep_fs": 2,
                   "trajectory_interval_steps": 50, "state_interval_steps": 25,
                   "checkpoint_interval_steps": 100, "minimize_max_iterations": 100,
                   "stop_when": {"measures": [{"analysis": "rmsd", "standard_error": 1e-6}],
                                 "max_duration_ns": 0.006}},
    "analysis": {"include": ["rmsd"]},
    "report": {"document": True, "slides": False, "pdf": False, "bundle": False},
}

SECOND = "tri__random-seed-2"

@pytest.fixture(scope="module", autouse=True)
def _the_logger_as_the_module_found_it():
    """The package logger put back as it was once this module's studies
    are done: its module fixtures call the CLI (`main`), which sets
    `propagate = False`, before the suite's per-test restore
    (`conftest._logging_state_is_restored`) reads it, so that restore put
    back the CLI's state and every later `caplog` read nothing."""
    import logging

    from fastmdxplora.utils import logging as fastmdx_logging

    base = logging.getLogger("fastmdx")
    propagate, level, handlers = base.propagate, base.level, list(base.handlers)
    console = fastmdx_logging._console_handler
    try:
        yield
    finally:
        base.propagate = propagate
        base.setLevel(level)
        base.handlers[:] = handlers
        fastmdx_logging._console_handler = console


#: The longest any wait here on a study's process (or for it to be held)
#: lasts: less than pytest-timeout's 600 s, so a study that never ends fails
#: its test, not the whole session.
_A_STUDY_S_WAIT = 480


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
    complete", and its manifest dropped how the round ended. Its rerun ended
    well, and its exit code says so."""
    study = tmp_path / "study"
    shutil.copytree(failed["study"], study, symlinks=True)
    code, printed = _rerun(study)
    assert code == 0
    assert not any(line.startswith("Batch complete") for line in printed)
    assert any(line.startswith("Batch failed") for line in printed)
    assert printed[-2].startswith("Its stopping rule's round 1 failed")
    assert printed[-1].startswith(f"`fastmdx resume {study}`")
    manifest = json.loads((study / "batch_manifest.json").read_text())
    assert (manifest.get("round_ended") or {}).get("state") == "failed"


def test_a_round_stopped_when_asked_is_said_after_its_rerun(failed, tmp_path):
    """Its phases run again ended well: exit 0, the round said stopped last."""
    def asked(record):
        record["rounds"][-1]["stopped_by"] = "asked"
        record["said"] = record["rounds"][-1]["said"] = (
            f"Stopped when asked, while extending {SECOND}. Resuming the study judges "
            "the runs again and carries on from there.")

    study = _with_record(failed, tmp_path, asked)
    code, printed = _rerun(study)
    assert code == 0
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
    # Stopped short of its end, as its checkpoint says (one at its end is
    # whole, `joining.piece_finished`).
    side_path = status.parent / "checkpoint.chk.json"
    side = json.loads(side_path.read_text())
    side_path.write_text(json.dumps({**side, "finished": False, "step": 1}))
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


def _held_study(argv: list[str], cwd: Path, hold: Path, **at: str):
    """``argv`` (after the interpreter) in a process group of its own, held
    at a known point by `tests/_held/sitecustomize.py` (``piece``,
    ``after``, ``runs``, ``checkpoint`` or ``call``, its variables): the
    fixture then signals or kills it there, whatever the machine's speed."""
    import os
    import subprocess
    import sys

    hold.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "FASTMDX_TEST_HOLD": str(hold),
           "PYTHONPATH": os.pathsep.join([str(Path(__file__).parent / "_held"), _source()]),
           **{f"FASTMDX_TEST_HOLD_{key.upper()}": str(value) for key, value in at.items()}}
    # Ctrl-C as a terminal leaves it, whatever started the tests.
    return subprocess.Popen(
        [sys.executable, "-c", "import signal, sys, runpy\n"
         "signal.signal(signal.SIGINT, signal.default_int_handler)\n"
         "sys.argv = sys.argv[1:]\nrunpy.run_path(sys.argv[0], run_name='__main__')"
         if argv[0].endswith(".py") else
         "import signal, sys, runpy\n"
         "signal.signal(signal.SIGINT, signal.default_int_handler)\n"
         "sys.argv = sys.argv[1:]\nrunpy.run_module(sys.argv[0], run_name='__main__')",
         *argv],
        cwd=cwd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)


def _until_held(process, hold: Path, names: list[str], *, timeout: float = _A_STUDY_S_WAIT) -> dict:
    """Each of ``names``'s marker (``<name>.held``) written, with the id of
    the process held there; fails where the study ended first."""
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and process.poll() is None:
        if all((hold / f"{name}.held").is_file() for name in names):
            time.sleep(0.1)
            return {name: int((hold / f"{name}.held").read_text()) for name in names}
        time.sleep(0.05)
    raise AssertionError(f"not held at {names}: the study ended with {process.poll()}")


@pytest.fixture(scope="module")
def cut_one(tmp_path_factory):
    """A study of one run whose process is killed (SIGKILL, as the kernel
    kills one out of memory) while its round's extension runs: the piece
    has a checkpoint and has not finished, and the record says the round
    goes on."""
    import os
    import signal

    root = tmp_path_factory.mktemp("one_cut")
    pdb = root / "tri-ala.pdb"
    pdb.write_text(TRI_ALANINE)
    config = json.loads(json.dumps(_CONFIG))
    config.pop("sweep")
    config["simulation"]["stop_when"]["independent_starts"] = "not_required"
    config["systems"] = [{"id": "tri", "system": str(pdb)}]
    config["output"] = str(root / "study")
    (root / "study.yml").write_text(yaml.safe_dump(config))
    # Held in its round's piece once that has a checkpoint, then killed.
    process = _held_study(["fastmdxplora.cli.main", "explore", "-c", str(root / "study.yml")],
                    root, root / "hold", piece="segment-001", after="100", checkpoint="1")
    study = root / "study"
    try:
        _until_held(process, root / "hold", ["study"])
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

    from fastmdxplora.gui.records_answer import answer_from_the_records
    from fastmdxplora.gui.workspace import study_state
    from fastmdxplora.simulation.stopping import round_ended

    study = tmp_path / "study"
    shutil.copytree(cut_one["study"], study, symlinks=True)
    # Held in the piece it carries on: the resume is going.
    resuming = _held_study(["fastmdxplora.cli.main", "resume", str(study)], tmp_path,
                           tmp_path / "hold", piece="segment-002")
    try:
        _until_held(resuming, tmp_path / "hold", ["study"])
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
    # Its run's resume refused: said refused.
    assert "Resume refused" in printed
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
    assert manifest["round_ended"] == ended.as_record()
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
    # Round 1 carried on (its catch-up is not a round the rule judged).
    assert (printed + said).count("Its stopping rule's round 1 was stopped when asked") == 1
    # What carries the round on read when asked, not saved to go stale.
    manifest = json.loads((study / "batch_manifest.json").read_text())
    assert manifest["round_ended"] == ended.as_record() and "carry_on" not in \
        manifest["round_ended"]


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
        f"`fastmdx resume {study}` carries no run on: tri__random-seed-1 is past the 1 ps "
        "ceiling the study now sets. With `max_duration_ns` at least ")
    before_record = (study / "stopping.json").read_text()
    with contextlib.redirect_stdout(io.StringIO()):
        resume.resume_study(study)
    assert {name: _production(run) for name, run in runs.items()} == before
    # No round judged, and the round's failure said as before.
    assert (study / "stopping.json").read_text() == before_record
    assert round_ended(study).state == "failed"


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
        "error of its own in its analysis. "
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

    from fastmdxplora.gui.records_answer import answer_from_the_records

    study = tmp_path / "study"
    shutil.copytree(failed["study"], study, symlinks=True)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(FastMDXplora, "_resolve_phase_runner", _failing_once("analysis", SECOND))
        _code, first, _said = _resume_by_the_cli(study)
    ended = round_ended(study)
    assert ended.state == "failed"

    def counted(printed):
        manifest = json.loads((study / "batch_manifest.json").read_text())
        return (re.search(r"(?:Batch failed|Resume refused): (\d+ ok, \d+ error\(s\))",
                          printed).group(1),
                {run["run_id"]: run["status"] for run in manifest["runs"]},
                re.search(r"study of 2 runs, (\d+) completed",
                          answer_from_the_records(study, "found")).group(1))

    # Its analyses failed: in error, from its record, before a resume as after.
    once = counted(first)
    # The run's own answer and remedy say what its study's sentence says,
    # not `fastmdx resume`, which refuses it.
    from fastmdxplora.remedies import remedies_of

    rerun = (f"fastmdx explore --output {study / 'runs' / SECOND} --include analysis report "
             "--rerun")
    own = answer_from_the_records(study / "runs" / SECOND, "found")
    assert rerun in own and "fastmdx resume" not in own.split("\n\n")[1]
    assert [remedy.command for remedy in remedies_of(study / "runs" / SECOND)] == [rerun]
    # The run's page too: failed, analysed again, not "Interrupted" with resume.
    from fastmdxplora.gui.server import _status_records

    health = _status_records(study / "runs" / SECOND)["health"]
    assert (health["state"], health.get("carry_on")) == ("failed", rerun)
    assert once == ("1 ok, 1 error(s)", {"tri__random-seed-1": "ok", SECOND: "error"}, "1")
    run = study / "runs" / SECOND
    assert ended.carry_on.startswith(
        f"`fastmdx resume {study}` does not carry the round on: {SECOND} stopped with an "
        "error of its own in its analysis.")
    assert f"`fastmdx explore --output {run} --include analysis report --rerun`" in \
        ended.carry_on
    before = (study / "stopping.json").read_text()
    code, printed, said = _resume_by_the_cli(study)
    assert code == 1
    assert counted(printed) == once
    # Its run's resume refused: said so, not stopped.
    # Each run counted once: the refused one is among the errors; exit 1.
    assert "Resume refused: 1 ok, 1 error(s) (1 of them refused by its resume), " in printed
    assert printed.rstrip().splitlines()[-1] == ended.carry_on
    # The run's own remedy ("suggests nothing", a cost in production) is not
    # said above the round's sentence, which says what fixes it.
    assert "What would fix it" not in printed
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

    study = tmp_path_factory.mktemp("catch_up_cut") / "study"
    shutil.copytree(failed["study"], study, symlinks=True)
    # Held in the second seed's catch-up piece once it has a checkpoint.
    hold = study.parent / "hold"
    process = _held_study(["fastmdxplora.cli.main", "resume", str(study)], study.parent, hold,
                    piece="segment-001", runs=SECOND, after="100", checkpoint="1")
    try:
        _until_held(process, hold, [SECOND])
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
    from fastmdxplora.gui.stopping_view import stopping_payload

    ended = round_ended(study)
    assert ended.carry_on.startswith(f"`fastmdx resume {study}` carries no run on: "
                                     "tri__random-seed-1 is past the ")
    before_record = (study / "stopping.json").read_text()
    code, printed, said = _resume_by_the_cli(study)
    assert {name: _production(run) for name, run in runs.items()} == before
    # Each run as it stands, the round as it ended, its sentence last.
    manifest = json.loads((study / "batch_manifest.json").read_text())
    # The cut run is not "ok": as the summary and the Agent count it.
    assert {run["run_id"]: (run["status"], run.get("error_type"))
            for run in manifest["runs"]} == {"tri__random-seed-1": ("ok", None),
                                             SECOND: ("error", "CutInTheRounds")}
    assert re.search(r"^Batch stopped: 1 ok, 0 error\(s\), 1 cut in the rounds, ", printed,
                     re.M)
    assert printed.rstrip().splitlines()[-1] == round_ended(study).carry_on
    assert ".;" not in printed + said
    assert (study / "stopping.json").read_text() == before_record
    assert round_ended(study).state == "interrupted"
    card = stopping_payload(study)
    assert "`" not in card["said"] and "carries no run on" in card["said"]
    assert " to " in card["production_said"]


@pytest.fixture(scope="module")
def cut_after_its_join(tmp_path_factory):
    """A study of one run whose process is killed after its round's piece
    was joined, before the joined pieces' analyses and report had run."""
    import os
    import signal

    root = tmp_path_factory.mktemp("one_cut_after_join")
    pdb = root / "tri-ala.pdb"
    pdb.write_text(TRI_ALANINE)
    config = json.loads(json.dumps(_CONFIG))
    config.pop("sweep")
    config["simulation"]["stop_when"]["independent_starts"] = "not_required"
    config["systems"] = [{"id": "tri", "system": str(pdb)}]
    config["output"] = str(root / "study")
    (root / "study.yml").write_text(yaml.safe_dump(config))
    # Held with its join written whole, before the analyses of it.
    process = _held_study(["fastmdxplora.cli.main", "explore", "-c", str(root / "study.yml")],
                    root, root / "hold",
                    call="fastmdxplora.simulation.resume:_the_study_s_config_text")
    study = root / "study"
    try:
        _until_held(process, root / "hold", ["call"])
        assert (study / "joined" / "joined.json").is_file()
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

    pdb = tmp_path / "tri-ala.pdb"
    pdb.write_text(TRI_ALANINE)
    config = json.loads(json.dumps(_CONFIG))
    config["execution"] = {"mode": "parallel", "workers": 2}
    config["systems"] = [{"id": "tri", "system": str(pdb)}]
    config["output"] = str(tmp_path / "study")
    (tmp_path / "study.yml").write_text(yaml.safe_dump(config))
    # The worker extending the second seed, held in its piece, then killed.
    process = _held_study(["fastmdxplora.cli.main", "explore", "-c", str(tmp_path / "study.yml")],
                    tmp_path, tmp_path / "hold", piece="segment-001", runs=SECOND)
    try:
        killed = _until_held(process, tmp_path / "hold", [SECOND])[SECOND]
        os.kill(killed, signal.SIGKILL)
        try:
            process.wait(timeout=_A_STUDY_S_WAIT)
        except subprocess.TimeoutExpired:
            raise AssertionError(f"the study did not end {_A_STUDY_S_WAIT} s after its "
                                 "worker was killed") from None
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


_A_POOL_BROKEN = """
import os, signal, sys, time
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context

from fastmdxplora.batch import explorer


def killed():
    time.sleep(0.5)
    os.kill(os.getpid(), signal.SIGKILL)


if __name__ == "__main__":
    # Two workers, as a study's pool makes them, both started and then idle.
    pool = ProcessPoolExecutor(2, mp_context=get_context(sys.argv[1]),
                               initializer=explorer._give_this_worker_its_share,
                               initargs=(1,))
    for future in [pool.submit(time.sleep, 0.5) for _ in range(2)]:
        future.result()
    assert len(pool._processes) == 2
    try:
        pool.submit(killed).result()
    except Exception as exc:
        print(type(exc).__name__, flush=True)
    # As the study's pools end (before this, a plain shutdown).
    getattr(explorer, "_shut_down",
            lambda pool: pool.shutdown(wait=True, cancel_futures=True))(pool)
    print("ended", flush=True)
"""


@pytest.mark.parametrize("method", ["spawn", "fork"])
def test_a_pool_whose_worker_is_killed_ends(method, tmp_path):
    """A study's pool, one worker killed and the other idle, ends: on
    Python 3.12 and later the pool sends the other worker SIGTERM alone,
    which a worker takes as a request to stop its run, and the study waited
    on it for ever (`explorer._shut_down`)."""
    import multiprocessing
    import os
    import sys

    if method not in multiprocessing.get_all_start_methods():
        pytest.skip(f"no {method} here")
    script = tmp_path / "pool.py"
    script.write_text(_A_POOL_BROKEN)
    env = {**os.environ, "PYTHONPATH": _source(), "FASTMDX_STOP_GRACE_SECONDS": "1"}
    import signal

    ran = subprocess.Popen([sys.executable, str(script), method], cwd=tmp_path, env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                           start_new_session=True)
    try:
        printed, said = ran.communicate(timeout=120)
    except subprocess.TimeoutExpired:
        raise AssertionError("the pool did not end 120 s after its worker was killed") from None
    finally:
        if ran.poll() is None:
            # Its workers with it.
            os.killpg(ran.pid, signal.SIGKILL)
            ran.communicate()
    assert ran.returncode == 0, said[-2000:]
    assert printed.split() == ["BrokenProcessPool", "ended"], said[-2000:]


# ---------------------------------------------------------------------------
# Fix after round 4: pieces stopped at their last step, a stop between a
# piece and its join, a ceiling below a cut extension's length
# ---------------------------------------------------------------------------

def _stopped_at_the_last_step_of(name: str):
    """A stop (SIGINT) asked as the production of ``name``'s piece reaches
    its last step, as a Ctrl-C landing in its last frame interval: the
    runner's own stop, its checkpoint written at the piece's full length and
    said not finished. Patches the runner's chunking and stop request."""
    import signal

    from fastmdxplora.simulation import runner

    real_cap = runner._cap_for_stopping
    seen: dict = {}

    def cap(chunk, done, elapsed):
        chunk = real_cap(chunk, done, elapsed)
        seen["reached"] = done + chunk
        # Whose steps: a production asks before its first chunk too.
        seen["where"] = (runner._LISTENING[-1].folder if runner._LISTENING else None)
        return chunk

    def planned(folder) -> int | None:
        try:
            simulation = yaml.safe_load((Path(folder) / "resolved_config.yml").read_text())[
                "simulation"]
            return round(float(simulation["duration_ns"]) * 1e6
                         / float(simulation.get("timestep_fs") or 2.0))
        except (OSError, KeyError, TypeError, ValueError):
            return None

    def requested(self):
        folder = Path(self.folder) if self.folder is not None else None
        if (self.signal is None and folder is not None and folder.parent.name == name
                and folder.name.startswith("segment-") and seen.get("where") == self.folder
                and seen.get("reached") is not None
                and seen["reached"] >= (planned(folder) or float("inf"))):
            self.signal = signal.SIGINT
        return self.signal is not None

    return cap, property(requested)


@pytest.fixture(scope="module")
def stopped_at_their_last_step(failed, tmp_path_factory):
    """The failed study resumed, and stopped as the second seed's catch-up
    piece reached its last step: the round stopped when asked, the piece's
    checkpoint at its full length, said not finished."""
    from fastmdxplora.simulation import runner

    study = tmp_path_factory.mktemp("last_step") / "study"
    shutil.copytree(failed["study"], study, symlinks=True)
    cap, requested = _stopped_at_the_last_step_of(SECOND)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(runner, "_cap_for_stopping", cap)
        patch.setattr(runner._StopRequests, "requested", requested)
        code, _printed, _said = _resume_by_the_cli(study)
    record = json.loads((study / "stopping.json").read_text())
    side = json.loads((study / "runs" / SECOND / "segment-001" / "simulation" /
                       "checkpoint.chk.json").read_text())
    assert code == 130 and record["rounds"][-1]["stopped_by"] == "asked"
    assert side["stage"] == "production"
    return {"study": study, "step": side["step"], "side": side}


def test_pieces_stopped_at_their_last_step_are_carried_on(stopped_at_their_last_step,
                                                          tmp_path):
    """Every resume refused ("Segments [1] did not finish: their checkpoints
    were written along the way") and said "Batch stopped", exit 130, with no
    stop asked, while every page said `fastmdx resume` carried the round on."""
    from fastmdxplora.simulation.stopping import round_ended

    study = tmp_path / "study"
    shutil.copytree(stopped_at_their_last_step["study"], study, symlinks=True)
    sentence = round_ended(study).carry_on
    code, printed, said = _resume_by_the_cli(study)
    record = json.loads((study / "stopping.json").read_text())
    assert "did not finish" not in printed + said
    assert "Batch stopped" not in printed
    assert code == 0, said[-2000:]
    assert record["outcome"] in ("met", "ceiling", "rounds"), record.get("said")
    after = [_production(study / "runs" / name) for name in record["runs"]]
    assert after[0] == pytest.approx(after[1], abs=1e-6)
    assert max(after) <= record["max_duration_ns"] + 1e-9
    # What it was said to run first, before it ran.
    assert f"first joins {SECOND}'s pieces and runs their analyses and report, then" in sentence
    # The whole run's frames, with pieces after the stopped one: every piece's,
    # as the joined trajectory holds them.
    from fastmdxplora.gui.telemetry import read_study_status

    run = study / "runs" / SECOND
    joined = json.loads((run / "joined" / "joined.json").read_text())
    assert read_study_status(run)["current_frame_count"] == joined["frames"]


def test_a_refused_resume_is_not_said_stopped():
    """A run of a resumed study its resume refused, whose own records hold
    the stop it came back from, was counted stopped ("Batch stopped", 130)."""
    from fastmdxplora.batch.explorer import _was_stopped
    from fastmdxplora.orchestrator import PhaseResult, RunResult

    stop = PhaseResult(name="simulation", status="error",
                       refusal={"code": "simulation.run.stopped"})
    refused = RunResult(run_id="a", system="a", status="error", phases=[stop],
                        error_type="ResumeRefused")
    assert not _was_stopped(refused)


@pytest.fixture(scope="module")
def stopped_before_its_join(tmp_path_factory):
    """A study of one run whose explore is stopped (Ctrl-C) as its round's
    piece is joined: the join written, its analyses owed, the record left
    "running" and no process record."""
    from fastmdxplora.cli.main import main
    from fastmdxplora.orchestrator import RUN_PROCESS_FILE
    from fastmdxplora.simulation import resume

    root = tmp_path_factory.mktemp("before_join")
    pdb = root / "tri-ala.pdb"
    pdb.write_text(TRI_ALANINE)
    config = json.loads(json.dumps(_CONFIG))
    config.pop("sweep")
    config["simulation"]["stop_when"]["independent_starts"] = "not_required"
    config["systems"] = [{"id": "tri", "system": str(pdb)}]
    config["output"] = str(root / "study")
    (root / "study.yml").write_text(yaml.safe_dump(config))
    # Its join written (joined.json), the analyses of it not begun.
    def the_study_s_config_text(root):
        raise KeyboardInterrupt

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(resume, "_the_study_s_config_text", the_study_s_config_text)
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                main(["explore", "-c", str(root / "study.yml")])
            except KeyboardInterrupt:
                pass
    study = root / "study"
    record = json.loads((study / "stopping.json").read_text())
    assert record["outcome"] == "running"
    assert not (study / RUN_PROCESS_FILE).exists()
    assert (study / "joined" / "analysis_owed.json").is_file()
    assert (study / "joined" / "joined.json").is_file()
    return {"study": study}


def test_one_run_stopped_before_its_join_is_a_cut_round(stopped_before_its_join, tmp_path):
    """Its round was said nowhere: the pages said the resume only analyses
    (it judged and extended), and the Agent gave the means of before the
    join as the whole run's."""
    from fastmdxplora.gui.records_answer import answer_from_the_records
    from fastmdxplora.gui.workspace import study_state
    from fastmdxplora.simulation.stopping import round_ended

    study = tmp_path / "study"
    shutil.copytree(stopped_before_its_join["study"], study, symlinks=True)
    ended = round_ended(study)
    assert ended is not None and ended.state == "interrupted"
    assert ended.carry_on.startswith(
        f"`fastmdx resume {study}` first runs the analyses and report of the run's joined "
        "pieces, then judges the run again")
    assert study_state(study) == "interrupted"
    answer = answer_from_the_records(study, "found")
    # The means are of the first piece analysed; the length said is the
    # joined one.
    owed = re.search(r"Its pieces were joined and their analyses and report have not run, "
                     r"so the means below are of its first ([\d.]+ ps), not of its "
                     r"([\d.]+ ps); fastmdx resume \S+ runs them\.", answer)
    assert owed, answer[:600]
    assert owed.group(1) != owed.group(2)
    assert answer.index(owed.group(0)) < answer.index("The prepared system")
    # Its length said one way: the paragraph's is the note's.
    assert f"simulated for {owed.group(2)} in " in answer


@pytest.fixture(scope="module")
def both_cut(tmp_path_factory):
    """Two seeds side by side, the explore killed (SIGKILL) while both
    runs' round 1 pieces run: both cut, the record running."""
    import os
    import signal

    root = tmp_path_factory.mktemp("both_cut")
    pdb = root / "tri-ala.pdb"
    pdb.write_text(TRI_ALANINE)
    config = json.loads(json.dumps(_CONFIG))
    config["execution"] = {"mode": "parallel", "workers": 2}
    config["systems"] = [{"id": "tri", "system": str(pdb)}]
    config["output"] = str(root / "study")
    (root / "study.yml").write_text(yaml.safe_dump(config))
    # Both round pieces held once each has a checkpoint, then killed: mid
    # piece whatever the machine's speed.
    process = _held_study(["fastmdxplora.cli.main", "explore", "-c", str(root / "study.yml")],
                    root, root / "hold", piece="segment-001", after="100", checkpoint="1")
    study = root / "study"
    try:
        _until_held(process, root / "hold", ["tri__random-seed-1", SECOND])
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()
    record = json.loads((study / "stopping.json").read_text())
    length = _length_of_the_round(record)
    assert record["outcome"] == "running"
    assert all(_production(study / "runs" / name) < length - 1e-6 for name in record["runs"])
    return {"study": study, "record": record, "length": length}


def test_both_runs_cut_are_said_carried_on_first(both_cut, tmp_path):
    from fastmdxplora.simulation.sampling_ask import length_said
    from fastmdxplora.simulation.stopping import round_ended

    study = tmp_path / "study"
    shutil.copytree(both_cut["study"], study, symlinks=True)
    assert round_ended(study).carry_on.startswith(
        f"`fastmdx resume {study}` first carries the extensions of tri__random-seed-1 and "
        f"{SECOND} on to {length_said(both_cut['length'])}, then judges its 2 runs again")


def test_a_ceiling_below_cut_extensions_length_carries_no_run_on(both_cut, tmp_path):
    """Both pieces were carried on to the round's length, past a ceiling
    lowered below it, under a sentence naming that ceiling as the most."""
    from fastmdxplora.simulation.sampling_ask import length_said
    from fastmdxplora.simulation.stopping import round_ended

    study = tmp_path / "study"
    shutil.copytree(both_cut["study"], study, symlinks=True)
    runs = {name: study / "runs" / name for name in both_cut["record"]["runs"]}
    before = {name: _production(run) for name, run in runs.items()}
    length = both_cut["length"]
    ceiling = round((max(before.values()) + length) / 2, 6)
    config = yaml.safe_load((study / "resolved_config.yml").read_text())
    config["simulation"]["stop_when"]["max_duration_ns"] = ceiling
    (study / "resolved_config.yml").write_text(yaml.safe_dump(config))
    assert round_ended(study).carry_on.startswith(
        f"`fastmdx resume {study}` carries no run on: the extension of tri__random-seed-1 "
        f"and {SECOND} was to run to {length_said(length)}, past the "
        f"{length_said(ceiling)} ceiling the study now sets.")
    _code, printed, _said = _resume_by_the_cli(study)
    assert {name: _production(run) for name, run in runs.items()} == before
    assert re.search(r"^Batch stopped: ", printed, re.M)
    assert round_ended(study).state == "interrupted"


def test_a_stop_when_asked_of_one_run_says_the_run():
    from fastmdxplora.simulation.stopping import _the_round_stopped

    record, entry = {}, {}
    _the_round_stopped(record, entry, "the run", {"stopped": True})
    assert record["said"] == ("Stopped when asked, while extending the run. Resuming the "
                              "study carries its round on from there.")


# ---------------------------------------------------------------------------
# Fix after round 5: one reading of a piece that reached its end
# ---------------------------------------------------------------------------

def _ended_at_the_last_piece(study: Path) -> float:
    """The study's ceiling set to the length its stopped piece reaches, so
    the resume's judgement ends it there: that piece stays its run's last."""
    record = json.loads((study / "stopping.json").read_text())
    length = _length_of_the_round(record)
    config = yaml.safe_load((study / "resolved_config.yml").read_text())
    config["simulation"]["stop_when"]["max_duration_ns"] = length
    (study / "resolved_config.yml").write_text(yaml.safe_dump(config))
    return length


def _read_as_completed(study: Path) -> None:
    from fastmdxplora.gui.exploration import runs_of_a_study
    from fastmdxplora.gui.records_answer import answer_from_the_records
    from fastmdxplora.gui.server import _status_records
    from fastmdxplora.gui.workspace import study_state
    from fastmdxplora.simulation.lengths import last_piece_stopped
    from fastmdxplora.simulation.stopping import round_ended

    assert round_ended(study) is None
    assert study_state(study) == "completed"
    assert {run["run_id"]: run["state"] for run in runs_of_a_study(study)} == {
        "tri__random-seed-1": "completed", SECOND: "completed"}
    assert "study of 2 runs, 2 completed" in answer_from_the_records(study, "found")
    run = study / "runs" / SECOND
    assert _status_records(run)["health"]["message"] == "Completed"
    assert not last_piece_stopped(run)


def _joined_once_in_order(run: Path) -> None:
    """The joined trajectory holds each piece's frames once, in order, at
    the record's spacing (`joined/joined.json`)."""
    import mdtraj
    import numpy as np

    joined = json.loads((run / "joined" / "joined.json").read_text())
    topology = run / "simulation" / "trajectory_topology.pdb"
    whole = mdtraj.load(str(run / "joined" / "production.dcd"), top=str(topology))
    pieces = [mdtraj.load(str(folder / "simulation" / "production.dcd"), top=str(topology))
              for folder in [run, *sorted(run.glob("segment-*/"))]]
    assert joined["segments"] == list(range(len(pieces)))
    assert whole.n_frames == joined["frames"] == sum(piece.n_frames for piece in pieces)
    np.testing.assert_allclose(whole.xyz, np.concatenate([piece.xyz for piece in pieces]),
                               atol=1e-4)
    # Its times: one frame every saving interval, to the run's production.
    assert whole.n_frames * joined["saving_interval_ps"] / 1000 == pytest.approx(
        _production(run), abs=1e-6)


def test_a_piece_stopped_at_its_end_is_said_finished_by_the_runner(stopped_at_their_last_step):
    """Its checkpoint said not finished and its record stopped, so every
    page but the join read it stopped for good."""
    side = stopped_at_their_last_step["side"]
    piece = stopped_at_their_last_step["study"] / "runs" / SECOND / "segment-001"
    status = json.loads((piece / "simulation" / "live_status.json").read_text())
    assert side["finished"] is True
    assert status["stage_states"]["production"] == "completed"
    assert status["current_step"] == side["step"]
    # Its simulated time with its step, never left at an earlier sample's.
    assert status["simulation_time_completed_ns"] == pytest.approx(
        status["current_step"] * 2.0 / 1e6)


def test_a_run_whose_last_piece_was_stopped_at_its_end_reads_completed(
        stopped_at_their_last_step, tmp_path):
    """Its resume joined the piece and ended the study at its ceiling, and
    the study read stopped on every page and by the Agent; a second resume
    ran nothing and added the same round again."""
    study = tmp_path / "study"
    shutil.copytree(stopped_at_their_last_step["study"], study, symlinks=True)
    _ended_at_the_last_piece(study)
    code, _printed, said = _resume_by_the_cli(study)
    assert code == 0, said[-2000:]
    _read_as_completed(study)
    _joined_once_in_order(study / "runs" / SECOND)
    before = (study / "stopping.json").read_text()
    code, printed, _said = _resume_by_the_cli(study)
    assert code == 0
    assert (study / "stopping.json").read_text() == before
    assert "Nothing run: the runs were judged at these lengths already." in printed
    # And says so last, not that its runs were carried on.
    assert printed.rstrip().splitlines()[-1] == "Nothing to do: the study finished."
    _read_as_completed(study)


def test_a_rule_changed_at_the_ceiling_is_judged(stopped_at_their_last_step, tmp_path):
    """At its ceiling, a resume said "Nothing run" of a rule changed since,
    and its record still held the old rule; and its last line said every run was
    carried on where only the rule was applied."""
    study = tmp_path / "study"
    shutil.copytree(stopped_at_their_last_step["study"], study, symlinks=True)
    _ended_at_the_last_piece(study)
    code, _printed, said = _resume_by_the_cli(study)
    assert code == 0, said[-2000:]
    config = yaml.safe_load((study / "resolved_config.yml").read_text())
    config["simulation"]["stop_when"]["measures"] = [{"analysis": "rmsd",
                                                      "standard_error": 1.0}]
    (study / "resolved_config.yml").write_text(yaml.safe_dump(config))
    code, printed, _said = _resume_by_the_cli(study)
    record = json.loads((study / "stopping.json").read_text())
    assert "Nothing run" not in printed
    assert record["targets"][0]["standard_error"] == 1.0
    assert record["rounds"][-1]["verdicts"]
    lines = printed.rstrip().splitlines()
    assert "Every run of the study was carried on or run" not in printed
    assert lines[-2] == ("Nothing was simulated: its stopping rule was applied to its "
                         "production as it stands.")
    assert lines[-1] == f"Its stopping rule: {record['said']}"


def test_a_record_written_before_the_runner_said_it_reads_completed(
        stopped_at_their_last_step, tmp_path):
    """A piece stopped at its end by a runner that said it not finished (its
    sidecar false, its record stopped) reads as the join reads it."""
    study = tmp_path / "study"
    shutil.copytree(stopped_at_their_last_step["study"], study, symlinks=True)
    simulation = study / "runs" / SECOND / "segment-001" / "simulation"
    side = json.loads((simulation / "checkpoint.chk.json").read_text())
    (simulation / "checkpoint.chk.json").write_text(json.dumps({**side, "finished": False}))
    status = json.loads((simulation / "live_status.json").read_text())
    status["stage_states"]["production"] = "stopped"
    (simulation / "live_status.json").write_text(json.dumps({**status, "status": "stopped"}))
    _ended_at_the_last_piece(study)
    code, _printed, said = _resume_by_the_cli(study)
    assert code == 0, said[-2000:]
    _read_as_completed(study)


def test_a_piece_s_planned_steps_are_the_runner_s(tmp_path):
    """At 3 fs and 10 ns the runner plans 3,333,330 steps; the join counted
    3,333,333, and a piece stopped at its real last step was not finished."""
    from fastmdxplora.analysis.joining import survey_segments
    from fastmdxplora.simulation.lengths import steps_in

    planned = steps_in(10.0, 3.0)
    assert planned == 3_333_330
    simulation = tmp_path / "segment-001" / "simulation"
    simulation.mkdir(parents=True)
    (simulation.parent / "resolved_config.yml").write_text(yaml.safe_dump(
        {"simulation": {"duration_ns": 10.0, "timestep_fs": 3.0}}))
    (simulation / "production.dcd").write_bytes(b"")
    (simulation / "checkpoint.chk").write_bytes(b"")
    (simulation / "checkpoint.chk.json").write_text(json.dumps(
        {"stage": "production", "step": planned, "finished": False}))
    piece = next(piece for piece in survey_segments(tmp_path) if piece.index == 1)
    assert piece.finished


@pytest.fixture(scope="module")
def variable_step_stopped(tmp_path_factory):
    """A study of one run whose integrator chooses its own step, stopped as
    its round 1 piece reached its last step."""
    from fastmdxplora.cli.main import main
    from fastmdxplora.simulation import runner

    root = tmp_path_factory.mktemp("variable")
    pdb = root / "tri-ala.pdb"
    pdb.write_text(TRI_ALANINE)
    config = json.loads(json.dumps(_CONFIG))
    config.pop("sweep")
    config["simulation"]["integrator"] = "variable_langevin"
    config["simulation"]["stop_when"]["independent_starts"] = "not_required"
    config["systems"] = [{"id": "tri", "system": str(pdb)}]
    config["output"] = str(root / "study")
    (root / "study.yml").write_text(yaml.safe_dump(config))
    cap, requested = _stopped_at_the_last_step_of("study")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(runner, "_cap_for_stopping", cap)
        patch.setattr(runner._StopRequests, "requested", requested)
        with contextlib.redirect_stdout(io.StringIO()):
            code = main(["explore", "-c", str(root / "study.yml")])
    record = json.loads((root / "study" / "stopping.json").read_text())
    assert code == 130 and record["rounds"][-1]["stopped_by"] == "asked"
    return {"study": root / "study"}


def test_a_variable_step_piece_stopped_at_its_end_is_carried_on(variable_step_stopped,
                                                                tmp_path):
    """Its clock short of the nominal length, the join refused the piece at
    every resume ("Segments [1] did not finish"), said "stopped", exit 130."""
    from fastmdxplora.simulation.stopping import round_ended

    study = tmp_path / "study"
    shutil.copytree(variable_step_stopped["study"], study, symlinks=True)
    code, printed, said = _resume_by_the_cli(study)
    record = json.loads((study / "stopping.json").read_text())
    assert "did not finish" not in printed + said
    assert code == 0, said[-2000:]
    assert record["outcome"] in ("met", "ceiling", "rounds"), record.get("said")
    assert round_ended(study) is None


def test_a_refused_resume_is_said_refused():
    """A study of runs whose resume each run refused headlined "Batch
    stopped", "2 stopped in the rounds", exit 130, with no stop asked."""
    from types import SimpleNamespace

    from fastmdxplora.batch.explorer import BatchExplorer

    batch = BatchExplorer.__new__(BatchExplorer)
    batch.output_dir = Path("/nonexistent/study")
    batch.resume = True
    batch.results = [SimpleNamespace(status="error", error_type="ResumeRefused", phases=(),
                                     message="refused", run_id=name)
                     for name in ("a", "b")]
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        batch._print_summary()
    assert ("Resume refused: 0 ok, 2 error(s) (2 of them refused by their resume), "
            in out.getvalue())


def test_the_owed_note_is_not_said_while_the_study_is_carried_on(stopped_before_its_join,
                                                                 tmp_path):
    """While a resume ran the joined pieces' analyses, the Agent said they
    "have not run" and that `fastmdx resume` runs them."""
    import os
    import subprocess
    import sys

    from fastmdxplora.gui.records_answer import answer_from_the_records

    study = tmp_path / "study"
    shutil.copytree(stopped_before_its_join["study"], study, symlinks=True)
    holding = subprocess.Popen(
        [sys.executable, "-c",
         "import sys, time; from pathlib import Path; "
         "from fastmdxplora.simulation.resume import _carrying_on\n"
         "with _carrying_on(Path(sys.argv[1]).resolve()):\n"
         "    print('held', flush=True); time.sleep(600)", str(study)],
        env={**os.environ, "PYTHONPATH": _source()}, stdout=subprocess.PIPE, text=True)
    try:
        assert holding.stdout.readline().strip() == "held"
        answer = answer_from_the_records(study, "found")
    finally:
        holding.kill()
        holding.wait()
    assert "have not run" not in answer and "fastmdx resume" not in answer
    assert "Its pieces are being joined and analysed now" in answer


def test_an_unjoined_piece_s_run_says_what_its_means_are_of(stopped_at_their_last_step,
                                                            tmp_path):
    """Its run said "simulated for 8 ps in 2 pieces" with means of its first
    piece, nothing said of what span they were of."""
    from fastmdxplora.gui.records_answer import answer_from_the_records

    study = tmp_path / "study"
    shutil.copytree(stopped_at_their_last_step["study"], study, symlinks=True)
    answer = answer_from_the_records(study / "runs" / SECOND, "found")
    assert re.search(r"Its last piece is not joined, so the means below are of its first "
                     r"[\d.]+ ps, not of its [\d.]+ ps; fastmdx resume " + re.escape(str(study))
                     + r" joins it and runs them\.", answer), answer[:500]


def test_runs_stopped_side_by_side_are_each_named(tmp_path, monkeypatch):
    from fastmdxplora.simulation import stopping

    runs = [tmp_path / "a", tmp_path / "b"]
    for run in runs:
        run.mkdir()
    monkeypatch.setattr(stopping, "judge", lambda runs, targets, production: [
        stopping.Verdict(analysis="rmsd", met=False, said="rmsd not yet", value=0.1,
                         error=0.01, allowed=1e-6, unit="nm", more_ns=1.0)])
    monkeypatch.setattr(stopping, "_frame_ns", lambda run: 0.0001)
    monkeypatch.setattr(stopping, "_swept", lambda *args, **kwargs: None)
    stop_when = {"measures": [{"analysis": "rmsd", "standard_error": 1e-6}],
                 "max_duration_ns": 0.01}
    record = stopping.run_until_known(
        runs, stopping.targets_of(stop_when), stop_when, record_in=tmp_path,
        extend_all=lambda runs, more: [{"ok": False, "stopped": True} for _ in runs],
        say=lambda said: None, production_of=lambda run: 0.002)
    assert record["said"] == ("Stopped when asked, while extending a and b. Resuming the "
                              "study carries its round on from there.")


# ---------------------------------------------------------------------------
# Fix after round 6: the exit code, one run not split into pieces, the
# rule's comparison, a piece at its end before its join
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def stopped_mid_round(tmp_path_factory):
    """Two runs side by side, Ctrl-C (to the whole group, as a terminal
    sends it) while their round's pieces run, mid piece."""
    import os
    import signal

    tmp_path = tmp_path_factory.mktemp("mid_round")
    pdb = tmp_path / "tri-ala.pdb"
    pdb.write_text(TRI_ALANINE)
    config = json.loads(json.dumps(_CONFIG))
    config["execution"] = {"mode": "parallel", "workers": 2}
    config["systems"] = [{"id": "tri", "system": str(pdb)}]
    config["output"] = str(tmp_path / "study")
    (tmp_path / "study.yml").write_text(yaml.safe_dump(config))
    # Both round pieces held once each has a checkpoint, until the stop is
    # heard: a real Ctrl-C to the group lands mid piece whatever the
    # machine's speed (both ran to their end before it on a loaded one).
    process = _held_study(["fastmdxplora.cli.main", "explore", "-c", str(tmp_path / "study.yml")],
                    tmp_path, tmp_path / "hold", piece="segment-001", after="100",
                    checkpoint="1")
    checkpoints = [tmp_path / "study" / "runs" / name / "segment-001" / "simulation" /
                   "checkpoint.chk" for name in ("tri__random-seed-1", SECOND)]
    try:
        _until_held(process, tmp_path / "hold", ["tri__random-seed-1", SECOND])
        os.killpg(process.pid, signal.SIGINT)
        code = process.wait(timeout=_A_STUDY_S_WAIT)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
    return {"study": tmp_path / "study", "code": code, "checkpoints": checkpoints}


def test_runs_stopped_mid_round_side_by_side_exit_130(stopped_mid_round):
    """Ctrl-C in a round of two runs side by side exited 1: the runs stopped
    in the round were recorded in error, and the code was read from them."""
    study = stopped_mid_round["study"]
    record = json.loads((study / "stopping.json").read_text())
    assert record["rounds"][-1]["stopped_by"] == "asked", record.get("said")
    pieces = [json.loads(checkpoint.with_name("checkpoint.chk.json").read_text())
              for checkpoint in stopped_mid_round["checkpoints"]]
    assert not any(piece["finished"] for piece in pieces), pieces
    assert stopped_mid_round["code"] == 130
    # The GUI's fixes card: no refusal of the runs' own with no code and no
    # cost; they are carried on as the round's sentence says.
    from fastmdxplora.gui.fixes_view import fixes_payload

    said = json.dumps(fixes_payload(study))
    assert "has not been given a code" not in said and "no simulation" not in said


def _stopped_at_its_last_step(match):
    """The runner's own stop (SIGINT) asked as the production of the run
    ``match(folder)`` names reaches its last step."""
    import signal

    from fastmdxplora.simulation import runner

    real_cap = runner._cap_for_stopping
    seen: dict = {}

    def cap(chunk, done, elapsed):
        chunk = real_cap(chunk, done, elapsed)
        seen["reached"] = done + chunk
        # Whose steps: a production asks before its first chunk too.
        seen["where"] = (runner._LISTENING[-1].folder if runner._LISTENING else None)
        return chunk

    def planned(folder) -> int | None:
        try:
            simulation = yaml.safe_load((Path(folder) / "resolved_config.yml").read_text())[
                "simulation"]
            if simulation.get("production_steps"):
                return int(simulation["production_steps"])
            return round(float(simulation["duration_ns"]) * 1e6
                         / float(simulation.get("timestep_fs") or 2.0))
        except (OSError, KeyError, TypeError, ValueError):
            return None

    def requested(self):
        if self.folder is None or not match(Path(self.folder)):
            # Another production's steps: not this piece's (a production
            # asks before its first chunk, where a stop noted earlier waits).
            seen.pop("reached", None)
        elif (self.signal is None and not seen.get("fired") and seen.get("reached") is not None
                and seen.get("where") == self.folder
                and seen["reached"] >= (planned(self.folder) or float("inf"))):
            seen["fired"] = True
            self.signal = signal.SIGINT
        return self.signal is not None

    return cap, property(requested)


def _one_run_stopped_at_its_end(root: Path, *, rule: bool) -> Path:
    """A study of one run, not split into pieces, stopped as its production
    reaches its last step: with a rule whose ceiling is its length, or none."""
    from fastmdxplora.cli.main import main
    from fastmdxplora.simulation import runner

    pdb = root / "tri-ala.pdb"
    pdb.write_text(TRI_ALANINE)
    config = json.loads(json.dumps(_CONFIG))
    config.pop("sweep")
    if rule:
        config["simulation"]["stop_when"].update(max_duration_ns=0.0006,
                                                 independent_starts="not_required")
    else:
        config["simulation"].pop("stop_when")
    config["systems"] = [{"id": "tri", "system": str(pdb)}]
    config["output"] = str(root / "study")
    (root / "study.yml").write_text(yaml.safe_dump(config))
    study = (root / "study").resolve()
    cap, requested = _stopped_at_its_last_step(lambda folder: folder.resolve() == study)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(runner, "_cap_for_stopping", cap)
        patch.setattr(runner._StopRequests, "requested", requested)
        with contextlib.redirect_stdout(io.StringIO()):
            code = main(["explore", "-c", str(root / "study.yml")])
    assert code == 130
    return root / "study"


@pytest.mark.parametrize("rule", [True, False], ids=["a rule", "no rule"])
def test_one_run_stopped_at_its_end_is_completed_by_its_resume(tmp_path, rule):
    """Its resume ran the analyses and report only, never applied the rule
    its record said the resume applies, and it read stopped for good."""
    from fastmdxplora.gui.server import _status_records
    from fastmdxplora.gui.workspace import study_state

    from fastmdxplora.batch.explorer import _what_would_fix_it
    from fastmdxplora.gui.fixes_view import fixes_payload
    from fastmdxplora.remedies import remedies_of

    study = _one_run_stopped_at_its_end(tmp_path, rule=rule)
    code, printed, said = _resume_by_the_cli(study)
    assert code == 0, said[-2000:]
    # Said as what ran: its analyses, and the rule; nothing carried on.
    assert "Production was complete; the analyses and report were run." in printed
    assert "carried on from its last checkpoint" not in printed
    # Every reader of its Manifest reads it as its state does: nothing to fix.
    assert remedies_of(study) == [] and _what_would_fix_it(study) == []
    assert fixes_payload(study)["fixes"] == []
    manifest = json.loads((study / "manifest.json").read_text())
    simulation = next(phase for phase in manifest["phases"] if phase["name"] == "simulation")
    assert simulation["status"] == "ok" and simulation["stopped_at_its_end"]["refusal"]
    assert study_state(study) == "completed"
    # Its health reads its last sample (a temperature, say), never the stop.
    health = _status_records(study)["health"]
    assert health["state"] in ("ok", "warning") and "stopped" not in health["message"]
    if rule:
        record = json.loads((study / "stopping.json").read_text())
        assert record["outcome"] == "ceiling", record.get("said")
        assert record["rounds"] and record["rounds"][-1]["verdicts"]
        assert f"Its stopping rule: {record['said']}" in printed


def test_a_piece_at_its_end_reads_stopped_until_it_is_joined(stopped_at_their_last_step,
                                                             tmp_path):
    """Before its join, a run whose round piece reached its end read
    Completed, "2 ok", "2 completed", while its round said stopped."""
    from fastmdxplora.gui.exploration import runs_of_a_study
    from fastmdxplora.gui.records_answer import answer_from_the_records
    from fastmdxplora.gui.server import _status_records
    from fastmdxplora.gui.workspace import study_state

    study = tmp_path / "study"
    shutil.copytree(stopped_at_their_last_step["study"], study, symlinks=True)
    assert study_state(study) == "stopped"
    assert {run["run_id"]: run["state"] for run in runs_of_a_study(study)}[SECOND] == "stopped"
    assert _status_records(study / "runs" / SECOND)["health"]["message"] != "Completed"
    assert "study of 2 runs, 1 completed" in answer_from_the_records(study, "found")
    manifest = json.loads((study / "batch_manifest.json").read_text())
    assert {run["run_id"]: run["status"] for run in manifest["runs"]}[SECOND] == "error"


def test_a_variable_step_piece_at_its_end_is_not_said_short(variable_step_stopped, tmp_path):
    """The methods said the last piece ran to its last checkpoint, by the
    raw checkpoint record (as written before the runner said it finished),
    where the join reads it whole."""
    from fastmdxplora.simulation.lengths import production_clock

    study = tmp_path / "study"
    shutil.copytree(variable_step_stopped["study"], study, symlinks=True)
    side_path = study / "segment-001" / "simulation" / "checkpoint.chk.json"
    side_path.write_text(json.dumps({**json.loads(side_path.read_text()), "finished": False}))
    assert production_clock(study)["stopped"] is False


def test_the_span_of_the_means_follows_the_analysis_s_stride(tmp_path):
    from fastmdxplora.gui.records_answer import _span_analysed

    (tmp_path / "analysis").mkdir()
    (tmp_path / "analysis" / "analysis_manifest.json").write_text(json.dumps(
        {"n_frames": 10, "load_kwargs": {"stride": 2}}))
    (tmp_path / "resolved_config.yml").write_text(yaml.safe_dump(
        {"simulation": {"timestep_fs": 2.0, "trajectory_interval_steps": 50}}))
    # 10 frames read every 2nd of frames 50 steps of 2 fs apart: 2 ps.
    assert _span_analysed(tmp_path) == pytest.approx(0.002)


def _held(folder: Path):
    import os
    import subprocess
    import sys

    holding = subprocess.Popen(
        [sys.executable, "-c",
         "import sys, time; from pathlib import Path; "
         "from fastmdxplora.simulation.resume import _carrying_on\n"
         "with _carrying_on(Path(sys.argv[1]).resolve()):\n"
         "    print('held', flush=True); time.sleep(600)", str(folder)],
        env={**os.environ, "PYTHONPATH": _source()}, stdout=subprocess.PIPE, text=True)
    assert holding.stdout.readline().strip() == "held"
    return holding


def test_a_piece_being_simulated_is_not_said_joined(stopped_at_their_last_step, tmp_path):
    """While a resume joins a piece stopped at its end (its first act), the
    Agent said a piece was being simulated; while a piece runs, it said
    they were being joined."""
    from fastmdxplora.gui.records_answer import answer_from_the_records

    study = tmp_path / "study"
    shutil.copytree(stopped_at_their_last_step["study"], study, symlinks=True)
    run = study / "runs" / SECOND
    holding = _held(run)
    try:
        joining = answer_from_the_records(run, "found")
        # Its newest piece's own record going: a piece is being simulated.
        status = run / "segment-001" / "simulation" / "live_status.json"
        status.write_text(json.dumps({**json.loads(status.read_text()), "status": "running"}))
        simulating = answer_from_the_records(run, "found")
    finally:
        holding.kill()
        holding.wait()
    assert "Its pieces are being joined and analysed now" in joining
    assert "Its next piece is being simulated now" in simulating


def test_a_study_carried_on_is_not_said_completed(failed, tmp_path):
    """A resume's first instant (its lock taken, no process record yet) read
    completed from the runs."""
    from fastmdxplora.gui.workspace import study_state

    study = tmp_path / "study"
    shutil.copytree(failed["study"], study, symlinks=True)
    holding = _held(study)
    try:
        state = study_state(study)
    finally:
        holding.kill()
        holding.wait()
    assert state == "running"


# ---------------------------------------------------------------------------
# Fix after round 7
# ---------------------------------------------------------------------------

def test_one_run_asking_no_analysis_stopped_at_its_end_is_completed(tmp_path):
    """A study whose phases end at its simulation, stopped at its last step,
    read stopped for good after a resume that said "Nothing to do"."""
    from fastmdxplora.cli.main import main
    from fastmdxplora.gui.workspace import study_state
    from fastmdxplora.simulation import runner

    pdb = tmp_path / "tri-ala.pdb"
    pdb.write_text(TRI_ALANINE)
    config = json.loads(json.dumps(_CONFIG))
    config.pop("sweep")
    config["simulation"].pop("stop_when")
    config["exclude_phase"] = ["analysis", "report"]
    config["systems"] = [{"id": "tri", "system": str(pdb)}]
    config["output"] = str(tmp_path / "study")
    (tmp_path / "study.yml").write_text(yaml.safe_dump(config))
    study = (tmp_path / "study").resolve()
    cap, requested = _stopped_at_its_last_step(lambda folder: folder.resolve() == study)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(runner, "_cap_for_stopping", cap)
        patch.setattr(runner._StopRequests, "requested", requested)
        with contextlib.redirect_stdout(io.StringIO()):
            assert main(["explore", "-c", str(tmp_path / "study.yml")]) == 130
    code, printed, said = _resume_by_the_cli(study)
    assert code == 0, said[-2000:]
    # It finished the run's records, and says so (not "Nothing to do").
    assert "the run's records were finished" in printed, printed[-1500:]
    assert study_state(study) == "completed"


def test_a_run_whose_extension_never_began_is_said_so():
    """Sequential: "The extension of seed-1 and seed-2 did not finish" where
    seed-2's never began."""
    from fastmdxplora.simulation.stopping import _how_far_each_got

    said = _how_far_each_got(["a", "b"], {"extended": [], "started": ["a"]},
                             {"a": 0.0008, "b": 0.0006}, 0.0012)
    assert said.startswith(" The extension of a did not finish (a has 0.8 ps of production)")
    assert "the extension of b did not start" in said


def test_a_refused_resume_s_card_says_what_its_header_says(stopped_mid_round, tmp_path):
    """After a resume a run refused (its checkpoint altered), the header said
    Failed and the round's card Stopped."""
    from fastmdxplora.gui.stopping_view import stopping_payload
    from fastmdxplora.gui.workspace import study_state

    study = tmp_path / "study"
    shutil.copytree(stopped_mid_round["study"], study, symlinks=True)
    checkpoint = study / "runs" / SECOND / "segment-001" / "simulation" / "checkpoint.chk"
    data = bytearray(checkpoint.read_bytes())
    data[len(data) // 2] ^= 0xFF
    checkpoint.write_bytes(bytes(data))
    code, printed, _said = _resume_by_the_cli(study)
    assert code == 1 and "Resume refused" in printed, printed[-1500:]
    assert study_state(study) == "failed"
    assert stopping_payload(study)["outcome_label"] == "Failed"


@pytest.fixture(scope="module")
def stopped_at_its_end(tmp_path_factory):
    """One run with a rule, stopped as its production reached its last step."""
    return _one_run_stopped_at_its_end(tmp_path_factory.mktemp("at_its_end"), rule=True)


_KILLED_AT = '''
import os, sys
from fastmdxplora.simulation import resume
from fastmdxplora.cli.main import main

def killed(*args, **kwargs):
    os._exit(9)

setattr(resume, sys.argv[2], killed)
sys.exit(main(["resume", sys.argv[1]]))
'''


def _resumed_until(study: Path, where: str) -> int:
    """`fastmdx resume STUDY` in a process of its own, killed (no handler
    runs, as SIGKILL) as it calls ``resume.<where>``."""
    import os
    import subprocess
    import sys

    import fastmdxplora

    driver = study.parent / "killed_at.py"
    driver.write_text(_KILLED_AT)
    env = {**os.environ, "PYTHONPATH": str(Path(fastmdxplora.__file__).parents[1])}
    return subprocess.run([sys.executable, str(driver), str(study), where], env=env,
                          capture_output=True, timeout=_A_STUDY_S_WAIT).returncode


def _summary_of(study: Path) -> dict[str, str]:
    from fastmdxplora.gui import server

    def read(path: Path) -> dict:
        return json.loads(path.read_text()) if path.is_file() else {}

    from fastmdxplora.simulation.resume import extended_production

    records = server._summary_records(
        study, read(study / "manifest.json"),
        read(study / "analysis" / "analysis_manifest.json"),
        read(study / "simulation" / "simulation_parameters.json"),
        extended=extended_production(study))
    return {record["label"]: record["value"] for record in records}


def _read_whole_after_its_resume(study: Path) -> None:
    """Every reader of a run stopped at its end, its resume finished: the
    rule applied, completed, its stop not a problem, its length and frames."""
    from fastmdxplora.gui.fixes_view import fixes_payload
    from fastmdxplora.gui.overview_view import _phases
    from fastmdxplora.gui.problems_view import problems_payload
    from fastmdxplora.gui.workspace import study_state

    record = json.loads((study / "stopping.json").read_text())
    assert record["outcome"] == "ceiling" and record["rounds"], record.get("said")
    assert study_state(study) == "completed"
    assert fixes_payload(study)["fixes"] == []
    manifest = json.loads((study / "manifest.json").read_text())
    simulation = next(phase for phase in manifest["phases"] if phase["name"] == "simulation")
    assert simulation["status"] == "ok" and simulation["stopped_at_its_end"]["refusal"]
    assert "production.dcd" in simulation["artifacts"]
    assert {phase["status"] for phase in _phases(study)} == {"ok"}
    items = problems_payload(study)["items"]
    assert not [item for item in items if "stopped" in json.dumps(item).lower()], items
    summary = _summary_of(study)
    assert summary["Project status"] == "completed"
    # The GUI's dash for a value not there.
    assert summary["Production"] not in ("", "\u2014") and summary["Frames"] not in ("", "\u2014")


@pytest.mark.parametrize("where", ["_judge_the_run_again", "_phases_after_ended_well",
                                   "_its_stop_at_its_end_said_whole"],
                         ids=["before its rule", "before its live record",
                              "before its Manifest"])
def test_a_resume_killed_before_it_said_the_run_whole_is_finished_by_the_next(
        stopped_at_its_end, tmp_path, where):
    """Killed after its analyses and before its rule, it read completed with
    its rule never applied and no fix offered; killed between the Manifest
    and its live record, it read stopped and the next resume did nothing."""
    from fastmdxplora.gui.fixes_view import fixes_payload
    from fastmdxplora.gui.workspace import study_state

    study = tmp_path / "study"
    shutil.copytree(stopped_at_its_end, study, symlinks=True)
    assert _resumed_until(study, where) == 9
    # Killed before its rule, its rule is owed by a resume that died: cut,
    # never "stopped when asked" by the earlier stop's words (R21 L2). After
    # its rule, its run stopped at its end and not yet said whole: stopped.
    state = "interrupted" if where == "_judge_the_run_again" else "stopped"
    assert study_state(study) == state
    if state == "interrupted":
        from fastmdxplora.simulation.stopping import round_ended

        assert "the resume carrying it on ended" in round_ended(study).said
        # Its fixes give that reason, not the stop the resume carried on
        # from (R24 L4: "Production was stopped by Ctrl+C").
        fixes = json.dumps(fixes_payload(study)["fixes"])
        assert "the resume carrying it on ended" in fixes, fixes[:800]
        assert "stopped by Ctrl+C" not in fixes, fixes[:800]
    # The run page's "Project status" as the study's state, one reading.
    assert _summary_of(study)["Project status"] == state
    assert "fastmdx resume" in json.dumps(fixes_payload(study)["fixes"])
    code, printed, said = _resume_by_the_cli(study)
    assert code == 0, said[-2000:]
    # It says what it did: the rule applied, or the run's records finished.
    assert "Nothing to do" not in printed, printed[-1500:]
    _read_whole_after_its_resume(study)


def test_a_resume_of_one_run_stopped_at_its_end_is_read_whole_on_every_page(
        stopped_at_its_end, tmp_path):
    """Its Problems listed the stop as a run warning offering `fastmdx
    resume`, its run page said no Production and no Frames, and its Manifest
    listed none of the files it wrote."""
    study = tmp_path / "study"
    shutil.copytree(stopped_at_its_end, study, symlinks=True)
    code, printed, said = _resume_by_the_cli(study)
    assert code == 0, said[-2000:]
    _read_whole_after_its_resume(study)


# ---------------------------------------------------------------------------
# Fix after round 8: a resume stopped in its first act, Ctrl-C in a join and
# at a catch-up's start, a piece finished and not joined, the fold, the
# rounds' numbering
# ---------------------------------------------------------------------------

def _stopped_part_way_in(match, *, at: int = 100):
    """The runner's own stop (SIGINT) asked once, as the production of the
    piece ``match(folder)`` names passes step ``at``: it stops at the next
    frame, part way, with a checkpoint there."""
    import signal

    from fastmdxplora.simulation import runner

    real_cap = runner._cap_for_stopping
    seen: dict = {}

    def cap(chunk, done, elapsed):
        chunk = min(real_cap(chunk, done, elapsed), 50)
        seen["reached"] = done + chunk
        # Whose steps: a production asks before its first chunk too.
        seen["where"] = (runner._LISTENING[-1].folder if runner._LISTENING else None)
        return chunk

    def requested(self):
        if self.folder is None or not match(Path(self.folder)):
            seen.pop("reached", None)
        elif (self.signal is None and not seen.get("fired") and seen.get("where") == self.folder
                and seen.get("reached") is not None and seen["reached"] >= at):
            seen["fired"] = True
            self.signal = signal.SIGINT
        return self.signal is not None

    return cap, property(requested)


#: The CLI's last lines and exit code of a fixture's study, where it ran
#: the CLI in this process.
_RAN: dict = {}


def _piece_of(name: str, piece: str):
    return lambda folder: folder.name == piece and folder.parent.name == name


@pytest.fixture(scope="module")
def sequential_stopped(tmp_path_factory):
    """Two seeds one after another, stopped part way through the first
    seed's round piece (R15's q2m)."""
    from fastmdxplora.cli.main import main
    from fastmdxplora.simulation import runner

    tmp_path = tmp_path_factory.mktemp("sequential_stopped")
    pdb = tmp_path / "tri-ala.pdb"
    pdb.write_text(TRI_ALANINE)
    config = json.loads(json.dumps(_CONFIG))
    config["systems"] = [{"id": "tri", "system": str(pdb)}]
    config["output"] = str(tmp_path / "study")
    (tmp_path / "study.yml").write_text(yaml.safe_dump(config))
    cap, requested = _stopped_part_way_in(_piece_of("tri__random-seed-1", "segment-001"))
    out = io.StringIO()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(runner, "_cap_for_stopping", cap)
        patch.setattr(runner._StopRequests, "requested", requested)
        with contextlib.redirect_stdout(out):
            code = main(["explore", "-c", str(tmp_path / "study.yml")])
    assert code == 130
    _RAN[tmp_path / "study"] = {"code": code, "printed": out.getvalue()}
    return tmp_path / "study"


def test_a_resume_stopped_as_it_carries_its_round_on_keeps_the_round(sequential_stopped,
                                                                      tmp_path):
    """Stopped as it carried seed-1's cut piece on, it exited 1, said "before
    the rule was applied again" (the round no longer said), counted seed-2
    not started, and said a refusal that has no code at no cost."""
    from fastmdxplora.gui.exploration import runs_of_a_study
    from fastmdxplora.gui.fixes_view import fixes_payload
    from fastmdxplora.simulation import runner
    from fastmdxplora.simulation.stopping import STOPPED_BEFORE_THE_RULE_AGAIN, round_ended

    study = tmp_path / "study"
    shutil.copytree(sequential_stopped, study, symlinks=True)
    cap, requested = _stopped_part_way_in(_piece_of("tri__random-seed-1", "segment-002"),
                                          at=0)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(runner, "_cap_for_stopping", cap)
        patch.setattr(runner._StopRequests, "requested", requested)
        code, printed, said = _resume_by_the_cli(study)
    assert code == 130, (printed + said)[-2000:]
    record = json.loads((study / "stopping.json").read_text())
    assert record["said"] != STOPPED_BEFORE_THE_RULE_AGAIN
    ended = round_ended(study)
    assert ended is not None and ended.state == "stopped"
    assert ended.said.startswith("Its stopping rule's round 1 "), ended.said
    states = {run["run_id"]: run["state"] for run in runs_of_a_study(study)}
    assert states == {"tri__random-seed-1": "stopped", SECOND: "completed"}, states
    batch = json.loads((study / "batch_manifest.json").read_text())
    assert {run["run_id"]: run["status"] for run in batch["runs"]}[SECOND] == "ok"
    for text in (printed + said, json.dumps(fixes_payload(study))):
        assert "has not been given a code" not in text and "no simulation" not in text


_SIGNALLED_AT = '''
import os, signal, sys
from fastmdxplora.simulation import resume
from fastmdxplora.cli.main import main

# Ctrl-C as a terminal leaves it, whatever the tests were started from (a
# shell starts a job in the background with it ignored).
signal.signal(signal.SIGINT, signal.default_int_handler)
name, run = sys.argv[1], sys.argv[2]
# SIGINT by default; SIGTERM where the environment says, and after the
# function returns where it says so.
number = getattr(signal, os.environ.get("SIGNALLED_WITH") or "SIGINT")
after = bool(os.environ.get("SIGNALLED_AFTER"))
real = getattr(resume, name)
done = []

def signalled(*args, **kwargs):
    first = not done and (run == "-" or run in str(args[0]))
    if first:
        done.append(1)
        if not after:
            os.kill(os.getpid(), number)
    answer = real(*args, **kwargs)
    if first and after:
        os.kill(os.getpid(), number)
    return answer

setattr(resume, name, signalled)
sys.exit(main(sys.argv[3:]))
'''


def _signalled_at(where: Path, name: str, run: str, *argv: str, with_: str = "SIGINT",
                  after: bool = False):
    """`fastmdx ARGV` in a process of its own, sent a real signal (``with_``)
    as it first calls ``resume.<name>`` (for ``run``'s folder; "-" any), or,
    ``after``, as that call returns."""
    import os
    import subprocess
    import sys

    driver = where / "signalled_at.py"
    driver.write_text(_SIGNALLED_AT)
    env = {**os.environ, "PYTHONPATH": _source(), "SIGNALLED_WITH": with_,
           "SIGNALLED_AFTER": "1" if after else ""}
    return subprocess.run([sys.executable, str(driver), name, run, *argv],
                          env=env, cwd=where, capture_output=True, text=True,
                          timeout=_A_STUDY_S_WAIT)


@pytest.fixture(scope="module")
def one_stopped_in_its_join(tmp_path_factory):
    """One run with a rule, Ctrl-C (a real SIGINT) as its round's piece is
    joined."""
    tmp_path = tmp_path_factory.mktemp("stopped_in_its_join")
    pdb = tmp_path / "tri-ala.pdb"
    pdb.write_text(TRI_ALANINE)
    config = json.loads(json.dumps(_CONFIG))
    config.pop("sweep")
    config["simulation"]["stop_when"].update(max_duration_ns=0.0018,
                                             independent_starts="not_required")
    config["systems"] = [{"id": "tri", "system": str(pdb)}]
    config["output"] = str(tmp_path / "study")
    (tmp_path / "study.yml").write_text(yaml.safe_dump(config))
    ran = _signalled_at(tmp_path, "_join_and_analyse", "-", "explore", "-c",
                        str(tmp_path / "study.yml"))
    return {"study": tmp_path / "study", "ran": ran}


def test_ctrl_c_in_a_round_s_join_of_one_run_is_a_stop_asked(one_stopped_in_its_join):
    """It was recorded a cut: stopping.json "running", every page saying the
    round did not finish."""
    from fastmdxplora.gui.workspace import study_state

    study, ran = one_stopped_in_its_join["study"], one_stopped_in_its_join["ran"]
    assert ran.returncode == 130, ran.stderr[-2000:]
    record = json.loads((study / "stopping.json").read_text())
    assert record["outcome"] == "stopped" and record["rounds"][-1]["stopped_by"] == "asked"
    assert study_state(study) == "stopped"


def test_ctrl_c_in_a_round_s_join_of_its_resume_ends_without_a_traceback(
        one_stopped_in_its_join, tmp_path):
    """Its resume, Ctrl-C as its round's piece was joined: a traceback, -2."""
    from fastmdxplora.gui.workspace import study_state

    study = tmp_path / "study"
    shutil.copytree(one_stopped_in_its_join["study"], study, symlinks=True)
    # A ceiling past its first round's length, so its resume extends it and
    # joins a piece whatever length the rule chose for that round (it could
    # reach the ceiling there, and its resume then judged it, joining none).
    # (A study of one run keeps its rule in its record of its rounds.)
    record = json.loads((study / "stopping.json").read_text())
    record["max_duration_ns"] = 0.006
    (study / "stopping.json").write_text(json.dumps(record))
    ran = _signalled_at(tmp_path, "_join_and_analyse", "-", "resume", str(study))
    assert ran.returncode == 130 and "Traceback" not in ran.stderr, (ran.stdout[-1500:]
                                                                     + ran.stderr[-2000:])
    assert study_state(study) == "stopped"


def test_ctrl_c_in_a_sequential_resume_s_join_ends_without_a_traceback(sequential_stopped,
                                                                         tmp_path):
    """Ctrl-C as seed-1's carried-on piece was joined: a traceback."""
    study = tmp_path / "study"
    shutil.copytree(sequential_stopped, study, symlinks=True)
    ran = _signalled_at(tmp_path, "_join_and_analyse", "-", "resume", str(study))
    assert ran.returncode == 130 and "Traceback" not in ran.stderr, (ran.stdout[-3000:]
                                                                     + ran.stderr[-2000:])
    record = json.loads((study / "stopping.json").read_text())
    assert record["outcome"] == "stopped" and record["rounds"][-1]["stopped_by"] == "asked"


def test_ctrl_c_as_a_catch_up_begins_is_not_dropped(sequential_stopped, tmp_path):
    """Ctrl-C as seed-2's catch-up began ended the resume in a traceback,
    its round said cut (or, raised where Python ignores it, was lost and the
    resume ran on to its ceiling)."""
    from fastmdxplora.gui.workspace import study_state

    study = tmp_path / "study"
    shutil.copytree(sequential_stopped, study, symlinks=True)
    ran = _signalled_at(tmp_path, "extend_study", SECOND, "resume", str(study))
    assert ran.returncode == 130 and "Traceback" not in ran.stderr, ran.stderr[-2000:]
    record = json.loads((study / "stopping.json").read_text())
    assert record["outcome"] == "stopped", record.get("said")
    assert study_state(study) == "stopped"
    # Its catch-up piece, begun after the stop, took no step (it stepped a
    # whole frame interval, past the grace): nothing of it to keep.
    piece = sorted((study / "runs" / SECOND).glob("segment-*/manifest.json"))[-1]
    simulation = next(p for p in json.loads(piece.read_text())["phases"]
                      if p["name"] == "simulation")
    assert "asked before its first step" in simulation["message"], simulation["message"]
    # Its study's resume, which carries the round on (the piece's own ran
    # the piece alone), and nothing of it "kept".
    assert (f"`fastmdx resume {study.resolve()}` carries the study's round on"
            in simulation["message"]), simulation["message"]
    assert "saved" in simulation["message"] and "kept" not in simulation["message"]


_KILLED_IN = '''
import importlib, os, sys
from fastmdxplora.cli.main import main

def killed(*args, **kwargs):
    os._exit(9)

where, _, name = sys.argv[1].rpartition(":")
module = importlib.import_module(where or "fastmdxplora.simulation.resume")
setattr(module, name, killed)
sys.exit(main(sys.argv[2:]))
'''


def _killed_in(where: Path, name: str, *argv: str) -> int:
    """`fastmdx ARGV` in a process of its own, killed (no handler runs, as
    SIGKILL) as it first calls ``name`` (``module:function``, or a function
    of `simulation.resume`)."""
    import os
    import subprocess
    import sys

    driver = where / "killed_in.py"
    driver.write_text(_KILLED_IN)
    return subprocess.run([sys.executable, str(driver), name, *argv],
                          env={**os.environ, "PYTHONPATH": _source()}, cwd=where,
                          capture_output=True, timeout=_A_STUDY_S_WAIT).returncode


@pytest.fixture(scope="module")
def sequential_unjoined(tmp_path_factory):
    """Two seeds one after another, the study's process killed as the first
    seed's round piece, finished, was to be joined."""
    import os
    import subprocess
    import sys

    tmp_path = tmp_path_factory.mktemp("sequential_unjoined")
    pdb = tmp_path / "tri-ala.pdb"
    pdb.write_text(TRI_ALANINE)
    config = json.loads(json.dumps(_CONFIG))
    config["systems"] = [{"id": "tri", "system": str(pdb)}]
    config["output"] = str(tmp_path / "study")
    (tmp_path / "study.yml").write_text(yaml.safe_dump(config))
    driver = tmp_path / "killed_in.py"
    driver.write_text(_KILLED_IN)
    ran = subprocess.run([sys.executable, str(driver), "_join_and_analyse", "explore", "-c",
                          str(tmp_path / "study.yml")],
                         env={**os.environ, "PYTHONPATH": _source()}, cwd=tmp_path,
                         capture_output=True, timeout=_A_STUDY_S_WAIT)
    assert ran.returncode == 9
    return tmp_path / "study"


def test_a_round_piece_finished_and_not_joined_reads_its_join_owed(sequential_unjoined):
    """seed-1, its round piece run whole and never joined, read completed
    (the runs list, its health, the Agent's count) beside the Agent saying
    its last piece is not joined."""
    from fastmdxplora.gui.exploration import runs_of_a_study
    from fastmdxplora.gui.records_answer import answer_from_the_records
    from fastmdxplora.gui.telemetry import status_as_it_stands

    study = sequential_unjoined
    run = study / "runs" / "tri__random-seed-1"
    assert json.loads((run / "segment-001" / "simulation" / "live_status.json").read_text())[
        "status"] == "completed"
    states = {r["run_id"]: r["state"] for r in runs_of_a_study(study)}
    assert states["tri__random-seed-1"] == "interrupted", states
    # Cut, with its join owed, in its own words: never "stopped, as asked"
    # (it was killed), on its health and its page's header.
    health = status_as_it_stands(run)
    assert health["status"] == "interrupted", health
    assert ("ran to its end, and the study's process ended before the piece was joined"
            in health["latest_error"]), health["latest_error"]
    assert "as asked" not in health["latest_error"]
    from fastmdxplora.gui.server import _status_records
    from fastmdxplora.gui.workspace import study_state

    health_card = _status_records(run)["health"]
    message = health_card["message"]
    assert "as asked" not in message and "joins it" in message, message
    # Its header reads the health's state first: it said "Failed".
    assert health_card["state"] == "interrupted", health_card
    assert study_state(study) == "interrupted"
    said = answer_from_the_records(study, "found") or ""
    assert "2 completed" not in said, said[:600]
    summary = _summary_of(run)
    assert "not joined" in summary["Production"] and "of its first" in summary["Frames"], summary


def test_a_piece_not_joined_is_told_without_the_config_beside_it(sequential_unjoined,
                                                                  tmp_path):
    """Told only by `segment-NNN.yml` beside it: a copy without it read
    the run completed under the round's sentence that it was extended."""
    from fastmdxplora.gui.exploration import runs_of_a_study

    study = tmp_path / "study"
    shutil.copytree(sequential_unjoined, study, symlinks=True)
    (study / "runs" / "tri__random-seed-1" / "segment-001.yml").unlink()
    states = {r["run_id"]: r["state"] for r in runs_of_a_study(study)}
    assert states["tri__random-seed-1"] == "interrupted", states


def test_a_study_cut_in_a_round_s_join_is_not_said_not_read(sequential_unjoined, tmp_path):
    """Killed holding its run's lock, the study read "Not read" (the lock
    refused as a file that is not one of its results) on its header and
    Overview."""
    from fastmdxplora.gui.server import _confined
    from fastmdxplora.gui.workspace import study_state

    study = tmp_path / "study"
    shutil.copytree(sequential_unjoined, study, symlinks=True)
    lock = study / "runs" / "tri__random-seed-1" / ".fastmdxplora-carrying-on"
    assert lock.is_file()
    for where in (study, study / "runs" / "tri__random-seed-1"):
        refused: set = set()

        def read(inside, refused=refused):
            from fastmdxplora import record_access

            with record_access.refusals_noted() as noted:
                state = study_state(inside)
            refused.update(noted)
            return state

        assert _confined(where, read) == "interrupted"
        assert not [name for name in refused if "carrying-on" in name], refused


def test_a_catch_up_is_folded_only_into_a_round_stopped_when_asked(tmp_path, monkeypatch):
    """A failed round was written over as stopped when asked; the folded
    round kept the old round's runs extended."""
    from fastmdxplora.simulation import stopping

    runs = [tmp_path / "runs" / "a", tmp_path / "runs" / "b"]
    for run in runs:
        run.mkdir(parents=True)
    monkeypatch.setattr(stopping, "_frame_ns", lambda run: 0.0001)
    monkeypatch.setattr(stopping, "_swept", lambda *args, **kwargs: None)
    stop_when = {"measures": [{"analysis": "rmsd", "standard_error": 1e-6}],
                 "max_duration_ns": 0.01}
    lengths = {"a": 0.004, "b": 0.002}

    def resumed(stopped_by: str) -> dict:
        earlier = [{"production_ns": 0.002, "verdicts": [], "decision": "stopped",
                    "more_ns": 0.002, "stopped_by": stopped_by, "extended": ["a"],
                    "said": "Stopped: it failed." if stopped_by == "error" else
                    "Stopped when asked, while extending a. Resuming the study carries its "
                    "round on from there."}]
        return stopping.run_until_known(
            runs, stopping.targets_of(stop_when), stop_when, record_in=tmp_path,
            extend_to=lambda some, total: [{"ok": False, "stopped": True}],
            say=lambda said: None, production_of=lambda run: lengths[run.name],
            earlier_rounds=earlier)

    record = resumed("asked")
    assert len(record["rounds"]) == 1
    assert record["rounds"][0]["stopped_by"] == "asked"
    # What the catch-up began and extended, not the old round's.
    folded = record["rounds"][0]
    assert (folded.get("started"), folded.get("extended")) == (["b"], []), folded
    record = resumed("error")
    assert len(record["rounds"]) == 2
    assert record["rounds"][0]["stopped_by"] == "error"
    assert record["rounds"][0]["said"] == "Stopped: it failed."


def test_a_cut_says_an_extension_never_begun_did_not_start(tmp_path, monkeypatch):
    """Cut in seed-1's extension, one after another: "the extension of seed-2
    did not finish" for an extension that never began."""
    from fastmdxplora.simulation import resume, stopping

    runs = [tmp_path / "runs" / "a", tmp_path / "runs" / "b"]
    for run in runs:
        run.mkdir(parents=True)
    monkeypatch.setattr(stopping, "judge", lambda runs, targets, production: [
        stopping.Verdict(analysis="rmsd", met=False, said="rmsd not yet", value=0.1,
                         error=0.01, allowed=1e-6, unit="nm", more_ns=1.0)])
    monkeypatch.setattr(stopping, "_frame_ns", lambda run: 0.0001)
    monkeypatch.setattr(stopping, "_swept", lambda *args, **kwargs: None)

    class _Cut(BaseException):
        pass

    def cut(run, **kwargs):
        raise _Cut

    monkeypatch.setattr(resume, "extend_study", cut)
    stop_when = {"measures": [{"analysis": "rmsd", "standard_error": 1e-6}],
                 "max_duration_ns": 0.01}
    with pytest.raises(_Cut):
        stopping.run_until_known(runs, stopping.targets_of(stop_when), stop_when,
                                 record_in=tmp_path, say=lambda said: None,
                                 production_of=lambda run: 0.002)
    # Cut after a's extension reached the round's length, before its answer.
    length = stopping.round_length(json.loads((tmp_path / "stopping.json").read_text())[
        "rounds"])
    monkeypatch.setattr(stopping, "_production_of_each",
                        lambda study, names: {"a": length, "b": 0.002})
    monkeypatch.setattr(stopping, "_what_resume_finds", lambda study, names: {})
    monkeypatch.setattr(stopping, "_its_process_is_gone", lambda study: True)
    said = stopping.round_ended(tmp_path, resuming=True).said
    assert "the extension of b did not start" in said, said


def test_the_rounds_are_numbered_as_judged_on_every_reader(tmp_path):
    """A round stopped and its catch-up stopped after a run moved: the
    sentence said round 1, the card's table and count, the Timeline, the
    methods and the Agent said 2."""
    from fastmdxplora.gui.agent_panel import _stopping_summary
    from fastmdxplora.gui.stopping_view import stopping_payload
    from fastmdxplora.gui.study_timeline import _rounds
    from fastmdxplora.report.methods import _stopping_sentences

    record = {
        "targets": [{"analysis": "rmsd", "standard_error": 1e-6}], "max_duration_ns": 0.01,
        "independent_starts": "required", "runs": ["a", "b"], "outcome": "stopped",
        "said": "Stopped when asked, while extending b. Resuming the study carries its round "
                "on from there.",
        "rounds": [
            {"production_ns": 0.002, "verdicts": [], "decision": "stopped", "more_ns": 0.002,
             "stopped_by": "asked", "extended": ["a"], "started": ["a", "b"],
             "said": "Stopped when asked, while extending b. Resuming the study carries its "
                     "round on from there."},
            {"production_ns": 0.003, "verdicts": [], "decision": "stopped", "more_ns": 0.001,
             "brought_up": ["b"], "stopped_by": "asked", "extended": [], "started": ["b"],
             "said": "Stopped when asked, while extending b. Resuming the study carries its "
                     "round on from there."}]}
    (tmp_path / "stopping.json").write_text(json.dumps(record))
    card = stopping_payload(tmp_path)
    assert [r["round"] for r in card["rounds"]] == [1, 1] and card["rounds_judged"] == 1
    events = [e for e in _rounds(tmp_path) if e.get("round")]
    assert [e["said"].split(":")[0] for e in events] == ["Round 1", "Round 1, its catch-up"]
    said = " ".join(_stopping_sentences(record, {}))
    assert "1 round" in said and "2 rounds" not in said, said
    assert "round 2" not in _stopping_summary(tmp_path)


def test_a_batch_resume_s_last_line_is_its_rule_s_outcome(sequential_stopped, tmp_path):
    """Its last line said every run was carried on; what its rule decided was
    only in the middle of what it printed."""
    study = tmp_path / "study"
    shutil.copytree(sequential_stopped, study, symlinks=True)
    # Its ceiling at its round's length: the round carried on and judged,
    # no further round run (all the claim needs).
    resolved = study / "resolved_config.yml"
    config = yaml.safe_load(resolved.read_text())
    config["simulation"]["stop_when"]["max_duration_ns"] = _length_of_the_round(
        json.loads((study / "stopping.json").read_text()))
    resolved.write_text(yaml.safe_dump(config))
    code, printed, said = _resume_by_the_cli(study)
    assert code == 0, (printed + said)[-2000:]
    record = json.loads((study / "stopping.json").read_text())
    assert printed.strip().splitlines()[-1] == f"Its stopping rule: {record['said']}"


# ---------------------------------------------------------------------------
# Fix after round 9: a study whose rule was never applied, a run owing its
# analyses counted, the stop while the rule judges, SIGTERM and the GUI's
# own stop in a round, a late Ctrl-C
# ---------------------------------------------------------------------------

def _study_of_seeds(root: Path, *, seeds: bool) -> Path:
    """A study's config, of two seeds one after another or of one run."""
    pdb = root / "tri-ala.pdb"
    pdb.write_text(TRI_ALANINE)
    config = json.loads(json.dumps(_CONFIG))
    if not seeds:
        config.pop("sweep")
        config["simulation"]["stop_when"].update(max_duration_ns=0.0018,
                                                 independent_starts="not_required")
    config["systems"] = [{"id": "tri", "system": str(pdb)}]
    config["output"] = str(root / "study")
    (root / "study.yml").write_text(yaml.safe_dump(config))
    return root / "study.yml"


@pytest.fixture(scope="module", params=["one run", "two seeds"])
def killed_before_its_rule(request, tmp_path_factory):
    """Its process killed as its rule's first round was to be judged, its
    first pieces finished (R17 M1)."""
    root = tmp_path_factory.mktemp("before_its_rule")
    config = _study_of_seeds(root, seeds=request.param == "two seeds")
    assert _killed_in(root, "fastmdxplora.simulation.stopping:run_until_known",
                      "explore", "-c", str(config)) == 9
    return {"study": root / "study", "seeds": request.param == "two seeds"}


def test_a_study_killed_before_its_rule_is_not_said_completed(killed_before_its_rule,
                                                               tmp_path):
    """It read completed everywhere, its rule never applied, no fix offered."""
    from fastmdxplora.gui.exploration import runs_of_a_study
    from fastmdxplora.gui.fixes_view import fixes_payload
    from fastmdxplora.gui.workspace import study_state

    study = tmp_path / "study"
    shutil.copytree(killed_before_its_rule["study"], study, symlinks=True)
    record = json.loads((study / "stopping.json").read_text())
    assert record["outcome"] == "running" and record["rounds"] == []
    assert study_state(study) == "interrupted"
    # Every reader says its rule was not applied, with `fastmdx resume`:
    # its health said Completed, its card "Running the first piece".
    _every_reader_agrees(study, "interrupted")
    assert "rule was not applied" in _every_reader(study)["health"]["message"]
    fixes = json.dumps(fixes_payload(study)["fixes"])
    assert "fastmdx resume" in fixes and "rule was not applied" in fixes, fixes
    if killed_before_its_rule["seeds"]:
        # Each run is as its own records say; the study is not.
        assert {run["state"] for run in runs_of_a_study(study)} == {"completed"}
        # Its resume killed as the rule begins again: still owed.
        assert _killed_in(tmp_path, "fastmdxplora.simulation.stopping:run_until_known",
                          "resume", str(study)) == 9
        assert study_state(study) == "interrupted"
    else:
        code, printed, said = _resume_by_the_cli(study)
        assert code == 0, said[-2000:]
        assert json.loads((study / "stopping.json").read_text())["rounds"]
        assert study_state(study) == "completed"


@pytest.fixture(scope="module")
def sequential_joined_not_analysed(tmp_path_factory):
    """Two seeds one after another, killed once seed-1's round piece was
    joined, before the analyses of the join (R18 M3)."""
    root = tmp_path_factory.mktemp("joined_not_analysed")
    config = _study_of_seeds(root, seeds=True)
    assert _killed_in(root, "_the_study_s_config_text", "explore", "-c", str(config)) == 9
    return root / "study"


def test_a_run_owing_its_analyses_is_not_counted_completed(sequential_joined_not_analysed):
    """Joined and not analysed, it was "completed" in the runs list and the
    Agent's "2 runs, 2 completed", while its page said its analyses owed."""
    from fastmdxplora.gui.exploration import runs_of_a_study
    from fastmdxplora.gui.records_answer import answer_from_the_records

    study = sequential_joined_not_analysed
    assert (study / "runs" / "tri__random-seed-1" / "joined" / "joined.json").is_file()
    states = {run["run_id"]: run["state"] for run in runs_of_a_study(study)}
    assert states["tri__random-seed-1"] == "interrupted", states
    said = answer_from_the_records(study, "found") or ""
    assert "2 runs, 2 completed" not in said, said[:600]


def test_a_ctrl_c_after_the_resume_s_last_write_is_not_a_stop(stopped_at_its_end, tmp_path):
    """Heard after the Manifest said the run whole, it said "stopped when
    asked" and exited 130 of a study every page read completed."""
    from fastmdxplora.gui.workspace import study_state

    study = tmp_path / "study"
    shutil.copytree(stopped_at_its_end, study, symlinks=True)
    ran = _signalled_at(tmp_path, "_its_stop_at_its_end_said_whole", "-", "resume",
                        str(study), after=True)
    assert ran.returncode == 0, ran.stderr[-2000:]
    assert "stopped when asked" not in ran.stderr and "Traceback" not in ran.stderr
    assert study_state(study) == "completed"


def _a_round_heard(tmp_path, monkeypatch, *, judged=None, extend_all=None) -> dict:
    """`run_until_known` over two runs, its first round's verdicts not met,
    with ``judged`` called as it judges and ``extend_all`` extending."""
    from fastmdxplora.simulation import stopping

    runs = [tmp_path / "runs" / "a", tmp_path / "runs" / "b"]
    for run in runs:
        run.mkdir(parents=True, exist_ok=True)

    def judge(runs, targets, production):
        if judged is not None:
            judged()
        return [stopping.Verdict(analysis="rmsd", met=False, said="rmsd not yet", value=0.1,
                                 error=0.01, allowed=1e-6, unit="nm", more_ns=1.0)]

    monkeypatch.setattr(stopping, "judge", judge)
    monkeypatch.setattr(stopping, "_frame_ns", lambda run: 0.0001)
    monkeypatch.setattr(stopping, "_swept", lambda *args, **kwargs: None)
    stop_when = {"measures": [{"analysis": "rmsd", "standard_error": 1e-6}],
                 "max_duration_ns": 0.01}
    return stopping.run_until_known(
        runs, stopping.targets_of(stop_when), stop_when, record_in=tmp_path,
        extend_all=extend_all or (lambda runs, more: [{"ok": True} for _ in runs]),
        say=lambda said: None, production_of=lambda run: 0.002)


def test_a_stop_while_the_rule_judges_names_no_run(tmp_path, monkeypatch):
    """Ctrl-C as the rule judged: "while extending a", and "the extension of
    a and b did not start", of a round no extension of which began."""
    import os
    import signal

    from fastmdxplora.simulation import runner, stopping

    before = signal.getsignal(signal.SIGINT)
    record = _a_round_heard(tmp_path, monkeypatch,
                            judged=lambda: os.kill(os.getpid(), signal.SIGINT))
    assert record["said"].startswith("Stopped when asked, while the runs were judged, "
                                     "before any extension began.")
    monkeypatch.setattr(stopping, "_production_of_each",
                        lambda study, names: {name: 0.002 for name in names})
    monkeypatch.setattr(stopping, "_what_resume_finds", lambda study, names: {})
    said = stopping.round_ended(tmp_path, resuming=True).said
    assert "while extending" not in said and "did not start" not in said, said
    # Its handler given back, nothing left listening.
    assert signal.getsignal(signal.SIGINT) is before and runner._LISTENING == []


def test_sigterm_and_the_gui_s_own_stop_in_a_round_are_stops_asked(tmp_path, monkeypatch):
    """Neither was tested in a round: SIGTERM between pieces, and the GUI's
    Stop in its own process, which answered that production ends at its
    next frame with none running."""
    import os
    import signal

    from fastmdxplora.simulation import runner

    before = signal.getsignal(signal.SIGTERM)

    def sigterm(runs, more):
        os.kill(os.getpid(), signal.SIGTERM)
        return [{"ok": True} for _ in runs]

    record = _a_round_heard(tmp_path / "term", monkeypatch, extend_all=sigterm)
    assert record["rounds"][-1]["stopped_by"] == "asked", record["said"]
    assert signal.getsignal(signal.SIGTERM) is before and runner._LISTENING == []

    answered = []

    def gui_stop(runs, more):
        answered.append(runner.stop_what_listens_here(tmp_path / "gui"))
        return [{"ok": True} for _ in runs]

    record = _a_round_heard(tmp_path / "gui", monkeypatch, extend_all=gui_stop)
    assert record["rounds"][-1]["stopped_by"] == "asked", record["said"]
    assert "round" in answered[0] and "next frame" not in answered[0], answered
    assert runner._LISTENING == []


# ---------------------------------------------------------------------------
# Fix after round 10: every reader of a study's state, over every state the
# file builds, from one derivation (`study_state`, `round_ended`)
# ---------------------------------------------------------------------------

#: The words a reader's chip, card or row gives a study that did not end.
#: The rule's card's word for each of its labels: a round ended short, or a
#: study stopped early, by its state; a rule that decided, completed.
_CARD_SAID = {"Interrupted": "interrupted", "Stopped": "stopped", "Failed": "failed",
              "Stopped early": "stopped", "Determined as asked": "completed",
              "Not determined as asked": "completed", "Not applied": "completed",
              "Not finished when this was written": "running", "Running": "running"}


def _in_its_held_read(folder: Path, read) -> tuple:
    """``read(folder)`` as the GUI's pages read it (`server._confined`), with
    the records its held read refused."""
    from fastmdxplora import record_access
    from fastmdxplora.gui.server import _confined

    refused: set = set()

    def held(inside):
        with record_access.refusals_noted() as noted:
            said = read(inside)
        refused.update(noted)
        return said

    return _confined(folder, held), refused


def stopping_card(folder: Path) -> dict:
    """The rule's card a page of ``folder`` is answered (`/api/stopping`):
    `server.stopping_card`, or where the server has none, the card read in
    the folder's own held read, as its route read it."""
    from fastmdxplora.gui import server

    if hasattr(server, "stopping_card"):
        return server.stopping_card(folder)
    from fastmdxplora.gui.stopping_view import stopping_payload

    return server._said_not_read(server._confined(folder, stopping_payload))


def _shared(study: Path) -> str:
    """Whether `fastmdx share` takes the study as finished: "finished", or
    its refusal's words."""
    from fastmdxplora.sharing.pack import ShareRefused, finished_manifest

    try:
        finished_manifest(study)
    except ShareRefused as refused:
        return str(refused)
    return "finished"


def _shown(page: dict) -> str:
    """A page's words as a person reads them: its HTML's text, or why it
    is empty."""
    import html

    return html.unescape(re.sub(r"<[^>]+>", "", str(page.get("html") or ""))
                         + " " + str(page.get("reason") or ""))


def _every_reader(study: Path) -> dict:
    """What each reader of the study's state says (docs/production.md lists
    them), each read as its page reads it: its word, what it says carries
    the study on, and the records its held read refused."""
    from fastmdxplora.batch.explorer import _what_would_fix_it
    from types import SimpleNamespace

    from fastmdxplora.gui.agent_panel import _run_status, _stopping_summary, run_summary_endpoint
    from fastmdxplora.gui.exploration import runs_of_a_study
    from fastmdxplora.gui.fixes_view import fixes_payload
    from fastmdxplora.gui.records_answer import answer_from_the_records
    from fastmdxplora.gui.report_page import report_payload
    from fastmdxplora.gui.server import _status_records
    from fastmdxplora.gui.stopping_view import stopping_payload
    from fastmdxplora.gui.workspace import card_of, study_state
    from fastmdxplora.remedies import remedies_of
    from fastmdxplora.simulation.resume import _still_running, being_carried_on
    from fastmdxplora.simulation.stopping import round_ended

    (state, health, card), refused = _in_its_held_read(study, lambda inside: (
        study_state(inside), _status_records(inside)["health"], stopping_payload(inside)))
    one_run = not (study / "batch_manifest.json").is_file()
    runs = [] if one_run else runs_of_a_study(study) or []
    note = run_summary_endpoint(
        {"study": str(study)},
        SimpleNamespace(running_root=None, process=None, stopped_roots=()))
    study_card = stopping_card(study)
    said = {
        "card_said": [study_card.get(key) for key in ("said", "carry_on", "ceiling_said",
                                                       "outcome_label")],
        "note": note,
        "words": {
            # The header, the sidebar and All studies.
            "study_state": state,
            "card_of": card_of(study).get("state"),
            # The health card and `/api/status` (the header reads it first).
            "health": {"ok": "completed", "warning": "completed"}.get(health["state"],
                                                                      health["state"]),
            # The rule's card, by its label.
            "card": _CARD_SAID[card["outcome_label"]] if card.get("ok") else "completed",
            # The run-ended note the Agent panel posts.
            "run_summary": note["status"],
            # The Agent's context and the AI app's record: its health line.
            "agent_health": {"ok": "completed", "warning": "completed"}.get(
                *[re.search(r"health: (\S+)", _run_status(SimpleNamespace(
                    active_root=study, snapshot=lambda: {"active_run": True,
                                                         "status": state})) or "").group(1)]
                * 2),
            # The run page's summary, both rows (a study of one run).
            **({"summary": _summary_of(study)["Project status"],
                "latest": _summary_of(study)["Latest status"]} if one_run else {}),
        },
        # The health card's Stop shows only while a process runs it.
        "going": being_carried_on(study) or _still_running(study),
        "refused": refused, "health": health, "card": card, "ended": round_ended(study),
        "fixes": json.dumps(fixes_payload(study)["fixes"]),
        "cli": " ".join(_what_would_fix_it(study)),
        "remedies": " ".join(str(remedy) for remedy in remedies_of(study)),
        "answer": answer_from_the_records(study, "found") or "",
        "context": _stopping_summary(study),
        # The Report page of a study of runs, its words as shown.
        "report": _shown(report_payload(study)),
        "runs": runs,
        # `fastmdx share`: finished only as it reads.
        "shared": _shared(study),
        # The comparison report, as the study's last command wrote it.
        "comparison": ((study / "comparison" / "comparison_report.md").read_text()
                       if (study / "comparison" / "comparison_report.md").is_file() else ""),
        "batch": (json.loads((study / "batch_manifest.json").read_text()) if not one_run
                  else {}),
    }
    # Each run's page of a study of runs, read as its page reads it: its
    # state on its header, card and health, the runs list's, and nothing of
    # its study refused to it.
    said["run_pages"] = {}
    for run in runs:
        folder = Path(run["path"])
        (run_state, run_health), run_refused = _in_its_held_read(
            folder, lambda inside: (study_state(inside), _status_records(inside)["health"]))
        # The rule's card on the run's page, as the server answers it.
        run_card = stopping_card(folder)
        run_refused |= set(run_card.get("unavailable") or ())
        from fastmdxplora.gui.problems_view import problems_payload

        said["run_pages"][run["run_id"]] = {
            # Its Problems, read held to the run as their pane reads them.
            "problems": problems_payload(folder).get("available", True),
            "headline": run_health.get("headline"),
            "card_said": [run_card.get(key) for key in ("said", "carry_on", "ceiling_said",
                                                         "outcome_label")],
            # The rule's card on the run's page.
            "card": (_CARD_SAID[run_card["outcome_label"]] if run_card.get("ok")
                     else "completed"),
            "card_words": json.dumps(run_card),
            "fixes": " ".join(str(remedy) for remedy in remedies_of(folder))
            + json.dumps(fixes_payload(folder)["fixes"]),
            "listed": run["state"], "study_state": run_state, "card_of": card_of(folder)["state"],
            "health": {"ok": "completed", "warning": "completed"}.get(run_health["state"],
                                                                      run_health["state"]),
            "refused": run_refused}
    return said


#: The health card's heading for each state it gives.
_HEADED = {"failed": ("Stopped with an error",), "stopped": ("Stopped",),
           "interrupted": ("Interrupted", "Its analysis is owed")}


def _every_reader_agrees(study: Path, state: str, *, ran: dict | None = None) -> None:
    """Every reader gives the study ``state``; one that did not end says
    what carries it on, as the one derivation (`round_ended`) says it, and
    offers the study's own `fastmdx resume`. ``ran``: the CLI's last lines
    and exit code where a fixture saved them."""
    said = _every_reader(study)
    assert set(said["words"].values()) == {state}, said["words"]
    assert said["health"].get("headline") in _HEADED.get(state, (said["health"].get(
        "headline"),)), said["health"]
    assert not said["refused"], said["refused"]
    assert not said["going"]
    for name, page in said["run_pages"].items():
        # A run's page agrees with itself and with the runs list, and reads
        # nothing of its study it is not given (a run killed before its join
        # said "Not read: ../../resolved_config.yml").
        assert not page["refused"], (name, page["refused"])
        assert page["problems"], (name, "its Problems not read")
        words = {page[key] for key in ("listed", "study_state", "card_of", "health")}
        assert len(words) == 1, (name, page)
        # Its rule's card is the study's card, word for word: what a resume
        # does, from the ceiling the study's config now sets.
        assert page["card_said"] == said["card_said"], (name, page["card_said"],
                                                         said["card_said"])
        # Its health card is headed as its state (a join that failed was
        # headed "Interrupted" beside "Failed").
        if page["health"] in _HEADED:
            assert page["headline"] in _HEADED[page["health"]], (name, page["headline"])
        assert page["card"] in words | {state}, (name, page["card"], page["card_words"][:600])
        # A round that failed is never said cut on its runs' pages.
        if state == "failed":
            assert "process ended before" not in page["fixes"], (name, page["fixes"][:800])
        # A run's fix carries on its study, never the run alone (which left
        # its study's round unjudged).
        for found in re.findall(r"fastmdx resume (/[^\s`'\",\]]+)", page["fixes"]):
            assert found == str(study), (name, found)
    # The Agent's count of runs is the runs list's.
    if said["runs"]:
        done = sum(1 for run in said["runs"] if run["state"] == "completed")
        assert f"{len(said['runs'])} runs, {done} completed" in said["answer"], said["answer"][:600]
    from types import SimpleNamespace

    from fastmdxplora.gui.agent_panel import _run_status

    ended = said["ended"]
    command = f"fastmdx resume {study}"
    assert (said["shared"] == "finished") == (state == "completed"), said["shared"]
    if state == "completed":
        assert ended is None
        for reader in ("fixes", "cli", "remedies", "answer", "report"):
            assert "fastmdx resume" not in said[reader], (reader, said[reader][:500])
        assert "not completed" not in said["context"]
        return
    # Its rule's round, or its rule never applied, said with its command by
    # the health, the card, the Agent's answer and context and the Report
    # page; a study whose run refused its resume fails beside a round
    # stopped.
    assert ended is not None
    assert said["card"]["carry_on"] == " ".join(ended.argv) == command
    for reader in ("answer", "context", "report"):
        assert ended.said in said[reader], (reader, said[reader][:800])
    # The Report page says it once (a study of one run's report, written
    # after its round, said it, and the page said it again above).
    assert said["report"].count(ended.said) == 1, said["report"][:1500]
    # The run-ended note says the round's sentence once, below its head.
    note = said["note"]
    assert (note["head"] + " " + note["found"]).count(ended.said) == 1, note
    if ended.state == state:
        assert said["health"]["message"] == ended.said, said["health"]
    # `fastmdx share` refuses it with the round's sentence and the study's
    # own command.
    assert ended.said in said["shared"] and command in said["shared"], said["shared"]
    if said["batch"].get("round_ended"):
        # Saved by the study's last command as the round then ended.
        assert said["batch"]["round_ended"]["state"] == ended.state, said["batch"]["round_ended"]
        if said["comparison"]:
            # The comparison report, written by that command, says it once.
            assert said["comparison"].count(ended.said.split(":")[0]) == 1, said["comparison"][:800]
    # The Agent's record never says the study cut beside a round that ended
    # otherwise, nor ".;".
    status = _run_status(SimpleNamespace(active_root=study, snapshot=lambda: {
        "active_run": True, "status": state})) or ""
    assert ".;" not in status, status
    if state != "interrupted":
        assert "state: interrupted" not in status, status
    # What the fixes give is the study's own resume where they give one, and
    # never a day of silence, nor "stopped, as asked" of a study no one asked.
    offered = "\n".join((said["fixes"], said["cli"], said["remedies"]))
    for found in re.findall(r"fastmdx resume (/[^\s`'\",\]]+)", offered):
        assert found == str(study), (found, offered[:800])
    for reader in ("fixes", "cli", "remedies"):
        assert "Nothing has been written for a day" not in said[reader], reader
        if state != "stopped":
            assert ", as asked" not in said[reader], (reader, said[reader][:500])
    if ran is not None and ran.get("printed"):
        # The CLI's last lines say the round, and its exit code the state.
        printed = ran["printed"]
        printed = "\n".join(printed) if isinstance(printed, list) else printed
        assert ended.said.split(":")[0] in printed, printed[-1500:]
        assert ran["code"] == {"failed": 1, "stopped": 130}.get(state, ran["code"])


@pytest.fixture(scope="module", params=["one run", "two seeds"])
def join_failed(request, tmp_path_factory):
    """Its rule's first round failed in its join: an error raised as the
    round's piece was joined (R24 M3's f1j and f2s)."""
    from fastmdxplora.analysis import joining
    from fastmdxplora.cli.main import main

    root = tmp_path_factory.mktemp("join_failed")
    config = _study_of_seeds(root, seeds=request.param == "two seeds")
    real, raised = joining.join_segments, []

    def join_segments(*args, **kwargs):
        if not raised:
            raised.append(True)
            raise RuntimeError("joining failed (made for the test)")
        return real(*args, **kwargs)

    out = io.StringIO()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(joining, "join_segments", join_segments)
        with contextlib.redirect_stdout(out):
            code = main(["explore", "-c", str(config)])
    assert raised and code == 1, out.getvalue()[-2000:]
    return {"study": root / "study", "code": code, "printed": out.getvalue()}


@pytest.fixture(scope="module")
def resume_died(sequential_stopped, tmp_path_factory):
    """A round stopped when asked, then a resume killed as it joined: its
    marker left in the study's record of rounds."""
    root = tmp_path_factory.mktemp("resume_died")
    study = root / "study"
    shutil.copytree(sequential_stopped, study, symlinks=True)
    assert _killed_in(root, "_join_and_analyse", "resume", str(study)) == 9
    return study


def _ran_of(value) -> dict | None:
    """The CLI's last lines and exit code a fixture saved, if it did."""
    if isinstance(value, dict):
        ran = value.get("ran")
        if ran is not None and hasattr(ran, "returncode"):
            return {"code": ran.returncode, "printed": ran.stdout + ran.stderr}
        return value if value.get("printed") else None
    return _RAN.get(value)


@pytest.mark.parametrize("built,state", [
    ("failed", "failed"), ("one", "failed"), ("cut_one", "interrupted"),
    ("sequential_stopped", "stopped"), ("one_stopped_in_its_join", "stopped"),
    ("sequential_unjoined", "interrupted"), ("sequential_joined_not_analysed", "interrupted"),
    ("one_killed_in_its_join", "interrupted"), ("resume_died", "interrupted"),
])
def test_every_reader_says_each_state_alike(request, tmp_path, built, state):
    """Each review found a reader left on its own branch of the live record:
    a piece killed before its join "Failed" on the header, a rule never
    applied "Completed" on its health and "Running the first piece" on its
    card, a day of silence on the fixes a minute after a kill; a resume
    that died read by its marker on the study and not on its runs."""
    value = request.getfixturevalue(built)
    study = tmp_path / "study"
    shutil.copytree(value["study"] if isinstance(value, dict) else value, study,
                    symlinks=True)
    _every_reader_agrees(study, state, ran=_ran_of(value))


def test_a_round_that_failed_in_its_join_is_said_failed_by_every_reader(join_failed,
                                                                        tmp_path):
    """The fixes card, the run's page and the Agent's record said a round
    that failed in its join cut ("the study's process ended before the
    piece was joined", "state: interrupted" beside "status: failed"), and
    `fastmdx share` shared a study of runs whose round failed; a run's
    health card headed it "Interrupted", its status bar "Production
    failed"."""
    from fastmdxplora.gui.telemetry import status_as_it_stands

    study = tmp_path / "study"
    shutil.copytree(join_failed["study"], study, symlinks=True)
    _every_reader_agrees(study, "failed", ran=join_failed)
    from fastmdxplora.gui.fixes_view import fixes_payload
    from fastmdxplora.gui.server import _status_records

    # Its pages are headed as it failed, and its status bar says the join
    # failed ("Production failed" from its production's stage).
    for page in [study] + sorted((study / "runs").glob("*")):
        health = _status_records(page)["health"]
        if health["state"] == "failed":
            assert health["headline"] == "Stopped with an error", (page, health)
    pieces = [status_as_it_stands(page) for page in [study] + sorted(
        (study / "runs").glob("*"))]
    assert any(p.get("join_owed") for p in pieces), pieces
    assert all(p.get("stage") == "joining" for p in pieces if p.get("join_owed")), pieces
    # Counted where its join is, at its production's end (it read "Joining
    # failed" beside "Stage 7 of 7").
    assert all(p["stage_states"]["production"] == "failed" for p in pieces
               if p.get("join_owed")), pieces
    # Its fix has a code of its own, not a run's interruption.
    from fastmdxplora.remedies import remedies_of

    codes = {r.code for page in [study] + sorted((study / "runs").glob("*"))
             for r in remedies_of(page)}
    assert "simulation.round.join_failed" in codes and "simulation.run.interrupted" not in codes, \
        codes

    said = json.dumps(fixes_payload(study)["fixes"])
    assert "process ended before" not in said, said[:800]


def _measure_line(data: dict) -> str:
    """The line the rule's card (stopping.js `measureBlock`) gives each
    measure of ``data``, its first one's."""
    import shutil as shell
    import subprocess

    node = shell.which("node")
    if node is None:
        pytest.skip("no node")
    script = (Path(__file__).parents[1] / "src" / "fastmdxplora" / "gui" / "static"
              / "stopping.js").read_text(encoding="utf-8")

    def function(name):
        start = script.index(f"  function {name}(")
        return script[start:script.index("\n  }\n", start) + 4]

    probe = "\n".join([
        "function Node(tag) { this.tag = tag; this.children = []; this.textContent = ''; }",
        "Node.prototype.appendChild = function (c) { this.children.push(c); return c; };",
        "Node.prototype.setAttribute = function () {};",
        "const document = {createElement: (tag) => new Node(tag)};",
        "const STATE_WORDS = {};",
        function("el"), function("withError"), function("plain"), function("ns"),
        function("measureBlock"),
        f"const data = {json.dumps(data)};",
        "const block = measureBlock(data.measures[0], data, 1);",
        "console.log(block.children.find((c) => c.className === 'stopping-now').textContent);"])
    return subprocess.run([node, "-e", probe], capture_output=True, text=True,
                          check=True).stdout.strip()


def test_the_card_of_a_rule_never_applied_says_it_judged_nothing(killed_before_its_rule,
                                                                  tmp_path):
    """Its measure said "the study stopped without finishing its first
    piece" under "Its stopping rule was not applied", its first pieces
    whole."""
    from fastmdxplora.gui.stopping_view import stopping_payload

    study = tmp_path / "study"
    shutil.copytree(killed_before_its_rule["study"], study, symlinks=True)
    data = stopping_payload(study)
    assert data["measures"], data
    line = _measure_line(data)
    assert line == "Not judged: the rule was not applied to it.", line


def test_a_resume_s_marker_is_not_believed_by_its_number_alone(resume_died, tmp_path):
    """The marker of a resume that died, its number now another live
    process's (a reboot, a copy, another machine): the study read "stopped
    when asked" by the earlier stop's words, its run "the study's process
    ended". Only this process's own marker is a resume going."""
    import os
    import subprocess

    from fastmdxplora.gui.workspace import study_state
    from fastmdxplora.orchestrator import process_start_ticks, this_machine
    from fastmdxplora.simulation.stopping import (CARRIED_ON_BY, _a_resume_died,
                                                  carried_on_marked, round_ended,
                                                  round_stopped_when_asked)

    study = tmp_path / "study"
    shutil.copytree(resume_died, study, symlinks=True)
    other = subprocess.Popen(["sleep", "600"])
    try:
        record = json.loads((study / "stopping.json").read_text())
        record[CARRIED_ON_BY] = {"pid": other.pid, "started": process_start_ticks(other.pid),
                                 **this_machine()}
        (study / "stopping.json").write_text(json.dumps(record))
        assert study_state(study) == "interrupted"
        assert "the resume carrying it on ended" in round_ended(study).said
        assert round_stopped_when_asked(study) is False
        # Its number this process's, begun at another time: not this one.
        # Told only where the system says when a process began (Linux):
        # elsewhere a marker names a process by its number and host alone.
        if process_start_ticks(os.getpid()) is not None:
            record[CARRIED_ON_BY] = {**record[CARRIED_ON_BY], "pid": os.getpid()}
            assert _a_resume_died(record)
    finally:
        other.kill()
        other.wait()
    # This process's own marker is no resume that died.
    with carried_on_marked(study):
        assert not _a_resume_died(json.loads((study / "stopping.json").read_text()))
    assert CARRIED_ON_BY not in json.loads((study / "stopping.json").read_text())


@pytest.mark.parametrize("ceiling", [0.001, 0.0024], ids=["lowered", "raised"])
def test_a_run_page_s_rule_card_is_its_study_s_after_its_ceiling_changes(
        sequential_stopped, tmp_path, ceiling):
    """With the study's ceiling lowered, each run page's card said a resume
    "first carries the extension of tri__random-seed-1 on to 1.8 ps", which
    the resume does not do; raised, "at most 1.2 ps" against the study's
    2.4: the record's ceiling, read in the run's scope."""
    study = tmp_path / "study"
    shutil.copytree(sequential_stopped, study, symlinks=True)
    config = yaml.safe_load((study / "resolved_config.yml").read_text())
    config["simulation"]["stop_when"]["max_duration_ns"] = ceiling
    (study / "resolved_config.yml").write_text(yaml.safe_dump(config))
    card = stopping_card(study)
    assert card["ok"] and not card.get("unavailable"), card
    for run in ("tri__random-seed-1", SECOND):
        page = stopping_card(study / "runs" / run)
        assert not page.get("unavailable"), page
        for key in ("said", "carry_on", "ceiling_said", "outcome_label"):
            assert page[key] == card[key], (run, key, page[key], card[key])
    # What the card says a resume does is what the study's config allows.
    assert ("carries no run on" in card["said"]) == (ceiling < 0.0018), card["said"]
    # One ceiling on the card: its strip and chart line are the sentence's.
    assert card["ceiling_ns"] == pytest.approx(ceiling), card["ceiling_said"]


def test_a_linked_out_config_is_named_on_the_rule_s_card(sequential_stopped, tmp_path):
    """A resolved_config.yml linked out of the study was read silently as
    the record's ceiling on the card, a linked-out stopping.json named."""
    study = tmp_path / "study"
    shutil.copytree(sequential_stopped, study, symlinks=True)
    outside = tmp_path / "elsewhere.yml"
    (study / "resolved_config.yml").rename(outside)
    (study / "resolved_config.yml").symlink_to(outside)
    assert "resolved_config.yml" in (stopping_card(study).get("unavailable") or []), \
        stopping_card(study)
    page = stopping_card(study / "runs" / SECOND)
    assert "../../resolved_config.yml" in (page.get("unavailable") or []), page


def _quiet(monkeypatch) -> None:
    """Every record read as written longer ago than a run may go unwritten
    (`telemetry.SILENT_RUN_ENDED_AFTER_SECONDS`, a day, and
    `resume.ANOTHER_HOST_QUIET_SECONDS`, an hour, for another machine's):
    quiet."""
    from fastmdxplora.gui import telemetry
    from fastmdxplora.simulation import resume

    monkeypatch.setattr(telemetry, "SILENT_RUN_ENDED_AFTER_SECONDS", -1)
    monkeypatch.setattr(resume, "ANOTHER_HOST_QUIET_SECONDS", -1)


def _written_on(study: Path, changed: str) -> None:
    """Every process record and lock of the study and its runs said written
    on another boot, machine or container (``changed``)."""
    from fastmdxplora.orchestrator import RUN_PROCESS_FILE
    from fastmdxplora.simulation.resume import CARRYING_ON_FILE

    for name in (RUN_PROCESS_FILE, CARRYING_ON_FILE):
        for path in study.rglob(name):
            try:
                record = json.loads(path.read_text())
            except ValueError:
                continue
            record[changed] = "another-" + changed
            path.write_text(json.dumps(record))


def _a_namespace_is_said() -> None:
    """Skipped where this system does not say a process's namespace (macOS,
    Windows: no `/proc/self/ns/pid`): another container on the same host is
    not told apart there, as the docs say."""
    from fastmdxplora.orchestrator import this_machine

    if not this_machine()["pidns"]:
        pytest.skip("this system does not say which process namespace")


@pytest.mark.parametrize("changed", ["boot", "host", "pidns"])
def test_a_study_killed_before_a_reboot_is_not_said_completed(killed_before_its_rule,
                                                               tmp_path, changed,
                                                               monkeypatch):
    """After a reboot (its run record of another boot of this machine), a
    study killed before its rule read completed on every reader and was
    shared. A record of another machine or container is a process not seen,
    not one seen gone: quiet for a day, it reads interrupted, saying where,
    its fix the person's to run once it has ended there."""
    from fastmdxplora.gui.fixes_view import runnable
    from fastmdxplora.simulation.stopping import round_ended

    if changed == "pidns":
        _a_namespace_is_said()
    study = tmp_path / "study"
    shutil.copytree(killed_before_its_rule["study"], study, symlinks=True)
    _written_on(study, changed)
    _quiet(monkeypatch)
    _every_reader_agrees(study, "interrupted")
    said = round_ended(study).said
    assert ("resume it only once it has ended there" in said) == (changed != "boot"), said
    assert "if it is still running there" not in said, said
    fixes = _fixes_of_its_pages(study)
    assert fixes and all(runnable(fix) == (changed == "boot") for fix in fixes), fixes


def _fixes_of_its_pages(study: Path) -> list:
    """What would fix the study and each of its runs (`remedies_of`)."""
    from fastmdxplora.remedies import remedies_of

    return [remedy for page in [study, *sorted((study / "runs").glob("*"))]
            for remedy in remedies_of(page)]


def _snapshot(study: Path) -> dict:
    """What each file of the study holds, but the live records its held
    process may write while it waits."""
    return {str(path.relative_to(study)): path.read_bytes() for path in sorted(study.rglob("*"))
            if path.is_file() and not path.is_symlink() and path.name != "live_status.json"}


#: Where a study going elsewhere is held: its process waits there.
_HELD_AT = {
    "mid piece": {"piece": "segment-001", "runs": "tri__random-seed-1", "after": "100",
                  "checkpoint": "1"},
    "between pieces": {"call": "fastmdxplora.simulation.stopping:run_until_known"},
    "in the rule": {"call": "fastmdxplora.simulation.stopping:judge"},
    "in the join": {"call": "fastmdxplora.simulation.resume:_join_and_analyse"},
}


def _every_reader_says_going(study: Path, *, elsewhere: bool) -> None:
    """Every reader of a study going (on another machine where
    ``elsewhere``): its header, card and health, the rule's card, the
    run-ended note, the Agent's health line, its fixes, share, and each of
    its runs' pages, their Problems read."""
    from types import SimpleNamespace

    from fastmdxplora.gui.agent_panel import _run_status, run_summary_endpoint
    from fastmdxplora.gui.fixes_view import fixes_payload
    from fastmdxplora.gui.problems_view import problems_payload
    from fastmdxplora.gui.server import _status_records
    from fastmdxplora.gui.workspace import card_of, study_state
    from fastmdxplora.remedies import remedies_of
    from fastmdxplora.sharing.pack import ShareRefused, finished_manifest
    from fastmdxplora.simulation.stopping import round_ended

    assert study_state(study) == "running" and card_of(study)["state"] == "running"
    assert round_ended(study) is None
    health = _status_records(study)["health"]
    assert health["state"] not in ("interrupted", "failed", "stopped", "ok"), health
    card = stopping_card(study)
    assert _CARD_SAID.get(card.get("outcome_label"), "running") == "running", card
    assert run_summary_endpoint({"study": str(study)}, SimpleNamespace(
        running_root=None, process=None, stopped_roots=()))["ended"] is False
    said = _run_status(SimpleNamespace(active_root=study, snapshot=lambda: {
        "active_run": True, "status": "running"})) or ""
    assert "health: interrupted" not in said and "health: ok -- Completed" not in said, said
    assert fixes_payload(study)["fixes"] == []
    with pytest.raises(ShareRefused):
        finished_manifest(study)
    if not elsewhere:
        return
    from fastmdxplora.gui.records_answer import answer_from_the_records as answered

    # The study's own answer leads with it, as each run's does.
    assert (answered(study, "found") or "").count("It is going") == 1
    assert health["message"].startswith("It is going on another-host") or \
        "another container" in health["message"], health
    assert "--taken-over" in health["message"], health
    from fastmdxplora.gui.records_answer import answer_from_the_records
    from fastmdxplora.gui.server import _confined

    for run in sorted((study / "runs").glob("*")):
        # Each run's page as the GUI reads it, held to the run (`_confined`):
        # its header, sidebar and `/api/status`, its records answer.
        state, page, answer = _confined(run, lambda inside: (
            study_state(inside), _status_records(inside)["health"],
            answer_from_the_records(inside, "found") or ""))
        assert state == "running", run
        assert page["state"] == "running", (run, page)
        # No resume offered but the person's own, --taken-over.
        # Said once: where it goes says it is going.
        assert answer.count("It is going") == 1 and "It is still going: It is" not in answer, \
            answer[:600]
        assert not re.search(r"fastmdx resume(?! --taken-over)", answer), answer[:900]
        assert study_state(run) == "running", run
        assert stopping_card(run).get("outcome_label") == card.get("outcome_label")
        assert remedies_of(run) == []
        assert problems_payload(run).get("available", True), problems_payload(run)


@pytest.mark.parametrize("where,changed", [
    *[(where, "host") for where in _HELD_AT], ("mid piece", "pidns"), ("mid piece", None)])
def test_a_study_going_elsewhere_reads_going_and_is_not_resumed_here(tmp_path, where, changed,
                                                                     monkeypatch):
    """A study really going (not killed) whose records name another machine
    (a cluster node, read over a shared folder) read interrupted for its
    whole round with a resume offered from here; its run pages, the rule's
    card, the note and the Agent said it ended while its header said
    running; a resume from here, while a round extended one of its runs,
    rewrote another run's report before it was refused at that run's lock."""
    import os
    import signal

    from fastmdxplora.cli.main import main
    from fastmdxplora.gui.workspace import study_state
    from fastmdxplora.remedies import remedies_of
    from fastmdxplora.simulation.stopping import round_ended

    if changed == "pidns":
        _a_namespace_is_said()
    config = _study_of_seeds(tmp_path, seeds=True)
    hold = tmp_path / "hold"
    process = _held_study(["fastmdxplora.cli.main", "explore", "-c", str(config)],
                          tmp_path, hold, **_HELD_AT[where])
    study = tmp_path / "study"
    try:
        _until_held(process, hold, ["tri__random-seed-1" if where == "mid piece" else "call"])
        if changed:
            _written_on(study, changed)
        _every_reader_says_going(study, elsewhere=bool(changed))
        before = _snapshot(study)
        code, printed, said = _resume_by_the_cli(study)
        assert code != 0 and "still going" in printed + said, (code, printed[-800:], said[-800:])
        if not changed:
            # Taken over is refused where this machine's process holds it.
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                assert main(["resume", "--taken-over", str(study)]) != 0
            assert "--taken-over carries on only" in out.getvalue(), out.getvalue()[-800:]
        after = _snapshot(study)
        assert [name for name in before if before[name] != after.get(name)] == [] \
            and set(after) == set(before), sorted(set(after) ^ set(before))
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()
    if changed:
        # Gone quiet: what its records say, where, and the person's to run.
        _quiet(monkeypatch)
        state = study_state(study)
        assert state == "interrupted", state
        ended = round_ended(study)
        if ended is not None:
            assert "which cannot be asked from here" in ended.said
        fixes = [fix for page in [study, *sorted((study / "runs").glob("*"))]
                 for fix in remedies_of(page)]
        assert fixes and all(fix.decision for fix in fixes), fixes
        assert all("which cannot be asked from here" in fix.why for fix in fixes), fixes


def _going(root: Path, *, minutes: float, host: str = "nodeB") -> Path:
    """A study of one run whose live record says it is going, and whose
    process record (another machine's) was last stamped ``minutes`` ago:
    its files written now, as a copy (`cp -r`) leaves them."""
    from datetime import datetime, timedelta, timezone

    from fastmdxplora.orchestrator import RUN_PROCESS_FILE

    then = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    (root / "simulation").mkdir(parents=True)
    (root / "resolved_config.yml").write_text("simulation: {}\n")
    (root / "simulation" / "live_status.json").write_text(json.dumps({
        "status": "running", "stage": "production", "last_update_timestamp": then,
        "current_step": 100, "total_planned_steps": 1000}))
    (root / RUN_PROCESS_FILE).write_text(json.dumps({
        "pid": 999999, "started_at": then, "alive_at": then, "host": host, "boot": "b",
        "pidns": ""}))
    return root


@pytest.mark.parametrize("minutes", [59, 61])
def test_another_host_s_quiet_hour_is_read_on_one_clock_and_said_so(tmp_path, minutes):
    """The hour was read on two clocks (file times against the records'
    stamps), and an hour's quiet was said as a day's: the card, the health,
    the fix's why and the timeline."""
    from fastmdxplora.gui.study_timeline import ENDED_HOW
    from fastmdxplora.gui.telemetry import analyze_health, status_as_it_stands
    from fastmdxplora.remedies import remedies_of
    from fastmdxplora.simulation.resume import going_elsewhere

    root = _going(tmp_path / "copy", minutes=minutes)
    status = status_as_it_stands(root)
    if minutes < 60:
        assert going_elsewhere(root) and status.get("ended_by") is None, status
        return
    assert not going_elsewhere(root)
    assert status["status"] == "interrupted" and status["ended_by"] == "silence_elsewhere"
    health = analyze_health(status, [], root=root)
    assert "over an hour" in health["message"] and "a day" not in health["message"], health
    assert "an hour" in ENDED_HOW[status["ended_by"]]
    why = " ".join(remedy.why for remedy in remedies_of(root))
    assert "for an hour" in why and "for a day" not in why, why


def test_the_time_left_names_its_zone_and_quotes_the_folder(tmp_path, monkeypatch):
    """The time left was this machine's local time with no zone named, and
    a folder with a space in it broke the command pasted."""
    import time

    from fastmdxplora.simulation import resume

    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    try:
        assert resume._clock(0) == "1970-01-01 00:00 UTC+00:00"
        root = _going(tmp_path / "a study", minutes=5)
        said = resume.going_elsewhere(root)
        assert "UTC+00:00" in said and f"--taken-over '{root}'" in said, said
    finally:
        monkeypatch.delenv("TZ")
        time.tzset()


def test_a_piece_set_aside_is_not_read_as_going(stopped_at_its_end, tmp_path):
    """A piece set aside (stopped before its first checkpoint) left its
    process record, another machine's, read as going: a finished study read
    running for the hour."""
    from datetime import datetime, timezone

    from fastmdxplora.gui.workspace import study_state
    from fastmdxplora.orchestrator import RUN_PROCESS_FILE

    study = tmp_path / "study"
    shutil.copytree(stopped_at_its_end, study, symlinks=True)
    assert _resume_by_the_cli(study)[0] == 0
    aside = study / "segment-001-stopped-before-its-checkpoint"
    aside.mkdir()
    now = datetime.now(timezone.utc).isoformat()
    (aside / RUN_PROCESS_FILE).write_text(json.dumps({
        "pid": 999999, "started_at": now, "alive_at": now, "host": "nodeB"}))
    assert study_state(study) == "completed"


def test_taken_over_refuses_a_lock_held_now(killed_before_its_rule, tmp_path):
    """--taken-over overrode a run's lock really held (a flock taken on
    another node of a shared file system), and wrote the study's records
    before it was refused."""
    import fcntl
    import os

    from fastmdxplora.simulation.resume import CARRYING_ON_FILE, resume_study

    if not killed_before_its_rule["seeds"]:
        pytest.skip("a run's lock: two seeds")
    study = tmp_path / "study"
    shutil.copytree(killed_before_its_rule["study"], study, symlinks=True)
    _written_on(study, "host")
    lock = study / "runs" / SECOND / CARRYING_ON_FILE
    lock.write_text(json.dumps({"pid": 4242, "host": "another-host"}))
    held = os.open(lock, os.O_RDWR)
    try:
        fcntl.flock(held, fcntl.LOCK_EX)
        before = _snapshot(study)
        answer = resume_study(study, taken_over=True)
        assert not answer["ok"] and f"runs/{SECOND}/{CARRYING_ON_FILE}" in answer["error"], answer
        assert _snapshot(study) == before
    finally:
        os.close(held)


_HOLDING = """
import os, sys, time
from pathlib import Path
from fastmdxplora import orchestrator
folder = Path(sys.argv[1])
orchestrator._hold_run_process(folder)
if sys.argv[2] == "cut":
    import resource, signal
    signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
    path = folder / orchestrator.RUN_PROCESS_FILE
    size = len(path.read_bytes())
    resource.setrlimit(resource.RLIMIT_FSIZE, (size // 2, size // 2))
    orchestrator.stamp_alive(path)
    print("stamped", flush=True)
    time.sleep(60)
print("held", flush=True)
time.sleep(60)
"""


def _holding(folder: Path, how: str, beat: str):
    """A process holding ``folder``'s record, its heartbeat every ``beat``
    seconds; ``how`` "cut": its one stamp cut short by a file size limit."""
    import os
    import subprocess
    import sys

    folder.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen(
        [sys.executable, "-c", _HOLDING, str(folder), how],
        env={**os.environ, "FASTMDX_HEARTBEAT_SECONDS": beat,
             "PYTHONPATH": _source()}, stdout=subprocess.PIPE, text=True)


def _now_going_on_another_host(study: Path) -> None:
    """Every process record of the study another machine's, stamped now."""
    from datetime import datetime, timezone

    from fastmdxplora.orchestrator import RUN_PROCESS_FILE

    _written_on(study, "host")
    for path in study.rglob(RUN_PROCESS_FILE):
        record = json.loads(path.read_text())
        record["alive_at"] = datetime.now(timezone.utc).isoformat()
        path.write_text(json.dumps(record))


def test_a_study_going_elsewhere_is_not_analysed_again_from_here(killed_before_its_rule,
                                                                 tmp_path):
    """Analyze again and Write it again were offered on a study going on
    another machine, and `fastmdx report --rerun` wrote into it and took its
    process records: refused, as a resume is, before anything is written."""
    from fastmdxplora import again
    from fastmdxplora.cli.main import main
    from fastmdxplora.refusals import StudyError

    study = tmp_path / "study"
    shutil.copytree(killed_before_its_rule["study"], study, symlinks=True)
    _now_going_on_another_host(study)
    before = _snapshot(study)
    pages = [study, *sorted((study / "runs").glob("*"))]
    for page in pages:
        with pytest.raises(StudyError, match="It is going on another-host"):
            again.plan(page, ["analysis"])
        offered = again.offered(page)
        assert not any(phase.get("can") for phase in offered.values()
                       if isinstance(phase, dict)), offered
        # Its command the refusal's last words, given for the page to set
        # as code, and a rerun named as possible after the time it names.
        for phase in ("analysis", "report"):
            why, terminal = offered[phase]["why"], offered[phase].get("terminal") or ""
            assert terminal.startswith("fastmdx resume --taken-over"), offered[phase]
            assert why.endswith(terminal) and "can be run again or resumed" in why, why
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        code = main(["report", "--output", str(study), "--rerun"])
    assert code != 0 and "It is going on another-host" in out.getvalue(), out.getvalue()[-800:]
    assert _snapshot(study) == before


def test_a_cut_round_s_report_written_again_says_its_round(sequential_unjoined, tmp_path):
    """`fastmdx report --rerun` of a study whose round was cut said "Batch
    complete: 2 ok" with no word why: it read the round as its own, going.
    Its report written again ended well: exit 0."""
    from fastmdxplora.cli.main import main
    from fastmdxplora.simulation.stopping import round_ended

    study = tmp_path / "study"
    shutil.copytree(sequential_unjoined, study, symlinks=True)
    ended = round_ended(study)
    assert ended is not None and ended.state == "interrupted"
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        code = main(["report", "--output", str(study), "--rerun"])
    said = out.getvalue()
    assert code == 0, said[-1500:]
    assert "Batch complete" not in said and ended.said.split(":")[0] in said, said[-1500:]
    assert f"fastmdx resume {study}" in said, said[-1500:]


def test_a_run_s_held_read_admits_only_its_study_s_records(killed_before_its_rule, tmp_path):
    """The run's held read admits its study's folder for its process record:
    a folder named runs of a folder that is no study is not read as one, and
    a study's process record linked out of it is not read on a run's page."""
    from fastmdxplora.gui.server import _a_run_in_its_study, _confined
    from fastmdxplora.gui.workspace import study_state
    from fastmdxplora.orchestrator import RUN_PROCESS_FILE

    if not killed_before_its_rule["seeds"]:
        pytest.skip("runs of a study: two seeds")
    plain = tmp_path / "w" / "runs" / "a"
    plain.mkdir(parents=True)
    (tmp_path / "w" / RUN_PROCESS_FILE).write_text(json.dumps({"pid": 1, "host": "nodeB"}))
    assert _a_run_in_its_study(plain.resolve()) is None
    study = tmp_path / "study"
    shutil.copytree(killed_before_its_rule["study"], study, symlinks=True)
    _now_going_on_another_host(study)
    record = study / RUN_PROCESS_FILE
    outside = tmp_path / "outside.json"
    record.rename(outside)
    record.symlink_to(outside)
    for run in sorted((study / "runs").glob("*")):
        for path in run.rglob(RUN_PROCESS_FILE):
            path.unlink()
        assert _confined(run, study_state) != "running", run


def test_a_cut_stamp_s_temporary_is_cleared_and_not_fetched(tmp_path):
    """A kill between a stamp's temporary and its rename left the temporary
    beside the record for good, and a fetch from a remote machine carried
    it back."""
    from fastmdxplora import orchestrator
    from fastmdxplora.orchestrator import RUN_PROCESS_FILE
    from fastmdxplora.remote.send import _only_files_and_folders

    record = tmp_path / "study" / RUN_PROCESS_FILE
    record.parent.mkdir()
    left = record.with_name(f"{RUN_PROCESS_FILE}.999999.abc.tmp")
    left.write_text("{")
    orchestrator._put_in_place(record, "{}")
    assert sorted(path.name for path in record.parent.iterdir()) == [RUN_PROCESS_FILE]
    arrived = tmp_path / "arrived"
    arrived.mkdir()
    (arrived / f"{RUN_PROCESS_FILE}.4242.xyz.tmp").write_text("{")
    _only_files_and_folders(arrived)
    assert list(arrived.iterdir()) == []


@pytest.mark.parametrize("seeds", [True, False], ids=["two seeds", "one run"])
def test_phases_run_again_into_a_study_going_here_are_refused(tmp_path, seeds):
    """`fastmdx report/analyze --rerun` of a study going on this machine
    wrote into it and took its process record (52 and 45 files), as did
    Analyze again from the GUI's POST: refused, nothing written."""
    import os
    import signal
    from unittest import mock

    from fastmdxplora import again
    from fastmdxplora.cli.main import main
    from fastmdxplora.gui.exploration import DashboardRuntime
    from fastmdxplora.refusals import StudyError

    config = _study_of_seeds(tmp_path, seeds=seeds)
    hold = tmp_path / "hold"
    process = _held_study(["fastmdxplora.cli.main", "explore", "-c", str(config)], tmp_path,
                          hold, call="fastmdxplora.simulation.stopping:run_until_known")
    study = tmp_path / "study"
    try:
        _until_held(process, hold, ["call"])
        before = _snapshot(study)
        pages = [study, *sorted((study / "runs").glob("*"))]
        for page in pages:
            for command in ("report", "analyze"):
                out = io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                    code = main([command, "--output", str(page), "--rerun"])
                assert code == 1 and "is still running" in out.getvalue(), out.getvalue()[-800:]
                # The study named, where it is the one going.
                assert f"{study} is still running" in out.getvalue(), out.getvalue()[-800:]
            runtime = DashboardRuntime(workspace_root=study.parent, exploration_root=study.parent)
            runtime.active_root = page
            with mock.patch.object(DashboardRuntime, "_spawn") as spawned:
                answer = runtime.run_again(["analysis"])
            assert not answer["ok"] and not spawned.called, answer
            with pytest.raises(StudyError) as refused:
                again.plan(page, ["analysis"])
            # A study of one run is its own run: named once.
            assert str(refused.value).count(study.name) <= 1, str(refused.value)
        assert _snapshot(study) == before
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def test_phases_run_again_into_a_study_a_resume_holds_are_refused(killed_before_its_rule,
                                                                  tmp_path):
    """A study held by `fastmdx resume` here (its lock and its process
    record the resume's): `report --rerun` wrote 34 files into it, its
    resume's record and members.json among them."""
    import os
    import signal

    from fastmdxplora.cli.main import main

    study = tmp_path / "study"
    shutil.copytree(killed_before_its_rule["study"], study, symlinks=True)
    hold = tmp_path / "hold"
    process = _held_study(["fastmdxplora.cli.main", "resume", str(study)], tmp_path, hold,
                          call="fastmdxplora.simulation.stopping:run_until_known")
    try:
        _until_held(process, hold, ["call"])
        before = _snapshot(study)
        for page in [study, *sorted((study / "runs").glob("*"))]:
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                code = main(["report", "--output", str(page), "--rerun"])
            assert code == 1 and ("still going" in out.getvalue()
                                  or "is still running" in out.getvalue()), out.getvalue()[-800:]
        assert _snapshot(study) == before
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def test_a_rerun_of_a_study_whose_resume_died_says_its_round_as_every_page(resume_died,
                                                                           tmp_path):
    """A rerun read the round with a resume's reading, which skips the dead
    resume's marker: it said "stopped when asked" in its summary and its
    batch_manifest.json, where every page said it did not finish."""
    from fastmdxplora.cli.main import main
    from fastmdxplora.simulation.stopping import round_ended

    study = tmp_path / "study"
    shutil.copytree(resume_died, study, symlinks=True)
    ended = round_ended(study)
    assert ended.state == "interrupted" and "the resume carrying it on ended" in ended.said
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        main(["report", "--output", str(study), "--rerun"])
    said = out.getvalue()
    assert "was stopped when asked" not in said and "the resume carrying it on ended" in said, \
        said[-1500:]
    recorded = json.loads((study / "batch_manifest.json").read_text())["round_ended"]
    assert recorded["state"] == "interrupted", recorded


def _unreadable(study: Path) -> None:
    """Every trajectory of the study's runs written over: its analyses fail."""
    for path in study.rglob("*.dcd"):
        path.write_bytes(b"not a trajectory")


#: Each case of a rerun over an unfinished round: the study it copies, the
#: page it is run from (the study, or a run of it), the phase run again,
#: the runs whose failure is named and the reason said.
_RERUN_CASES = {
    "cut": ("sequential_unjoined", "", "analysis"),
    "stopped": ("sequential_stopped", "", "analysis"),
    "failed": ("failed", "", "analysis"),
    "run page": ("sequential_unjoined", "runs/tri__random-seed-1", "analysis"),
    "one run": ("one_killed_in_its_join", "", "report"),
}


def _rerun_case(request, case: str, broken: bool, where: Path) -> dict:
    """A copy of the case's study, its trajectories written over (or, for a
    report, its report/ a file) where ``broken``."""
    built, page, phase = _RERUN_CASES[case]
    source = request.getfixturevalue(built)
    source = source["study"] if isinstance(source, dict) else source
    study = where / "study"
    shutil.copytree(source, study, symlinks=True)
    page = study / page if page else study
    names = ([page.name] if page != study
             else sorted(path.name for path in (study / "runs").iterdir())
             if (study / "runs").is_dir() else [])
    if broken and phase == "analysis":
        _unreadable(page)
    elif broken:
        shutil.rmtree(study / "report")
        (study / "report").write_text("not a folder")
    reason = "trajectory" if phase == "analysis" else "Not a directory"
    return {"study": study, "page": page, "phase": phase, "names": names, "reason": reason}


@pytest.mark.parametrize("broken", [False, True], ids=["clean", "failing"])
@pytest.mark.parametrize("case", list(_RERUN_CASES))
def test_a_rerun_over_an_unfinished_round_says_what_it_did(request, tmp_path, monkeypatch,
                                                           case, broken):
    """A rerun's exit code says what it did (0 each phase ended well, 1 one
    failed), whatever round its study was left in, and that round is said
    after it. A rerun over a cut round that ended well exited 1, over a
    stopped one 130; one that failed read "Analyzed again." in the GUI where
    its failure was not in the study's batch manifest (a run's own page, a
    report that could not be put aside), and the CLI's last line was the
    round's carry-on read before it."""
    import time

    from fastmdxplora.batch.explorer import campaign_of
    from fastmdxplora.cli.main import main
    from fastmdxplora.gui.exploration import DashboardRuntime
    from fastmdxplora.simulation.stopping import round_ended

    # The GUI's rerun is `python -m fastmdxplora` in the study's folder:
    # this source, not an installed one.
    monkeypatch.setenv("PYTHONPATH", _source())
    made = _rerun_case(request, case, broken, tmp_path / "cli")
    page, names = made["page"], made["names"]
    assert round_ended(campaign_of(page) or page) is not None
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        code = main(["analyze" if made["phase"] == "analysis" else "report",
                     "--output", str(page), "--rerun"])
    said = out.getvalue()
    tally = re.search(r"Batch \w+: (\d+) ok, (\d+) error", said)
    assert code == (1 if broken else 0), said[-2000:]
    if broken:
        assert made["reason"] in said, said[-2000:]
    if page == made["study"] and names:
        assert tally and int(tally.group(2)) == (len(names) if broken else 0), said[-2000:]
        ended = round_ended(made["study"])
        lines = [line for line in said.splitlines() if line.strip()]
        assert ended.said in lines[-2:], said[-2000:]
        if broken:
            # Its round said last, as every page reads it after the rerun
            # (what carries it on is not what did before it).
            assert lines[-2:] == [ended.said, ended.carry_on], said[-2000:]
            fixes = said.split("What would fix it", 1)[-1]
            assert all(name in fixes for name in names), said[-2000:]
    # Its phase run again from the GUI's runtime: a real rerun, read as it
    # ended.
    made = _rerun_case(request, case, broken, tmp_path / "gui")
    page, study = made["page"], made["study"]
    runtime = DashboardRuntime(workspace_root=study.parent, exploration_root=study.parent)
    runtime.active_root = page
    started = runtime.run_again([made["phase"]])
    assert started["ok"], started
    until = time.monotonic() + 600
    while time.monotonic() < until and runtime.snapshot()["process_running"]:
        time.sleep(1)
    state = runtime.snapshot()
    ended = round_ended(campaign_of(page) or page)
    if broken:
        assert state["status"] == "failed" and state["again_round"] is None, state
        error = state["error"]
        assert made["reason"] in error, error
        for name in names:
            assert f"{name} failed in its analysis: " in error, error
        assert error.endswith(f"{ended.said} {ended.carry_on_on_a_page}"), error
        assert state["error_terminal"] in error, state
    else:
        assert state["status"] == "completed" and state["error"] is None, state
        assert state["again_round"] and state["again_round"]["said"] == (
            f"{ended.said} {ended.carry_on_on_a_page}"), state


@pytest.mark.parametrize("broken", [False, True], ids=["clean", "failing"])
@pytest.mark.parametrize("case", ["cut", "run page", "one run"])
def test_a_rerun_over_an_unfinished_round_is_said_in_a_browser(request, tmp_path, monkeypatch,
                                                                case, broken):
    """In the GUI (Light, 1440): Analyze again of a cut study and of a run's
    own page, and Write it again of a study of one run, ending well, said
    done with the round after it; failing, said with its run and reason and
    the round after it, its command set as code, never done."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.batch.explorer import campaign_of
    from fastmdxplora.gui.server import start_dashboard_session
    from fastmdxplora.simulation.stopping import round_ended

    monkeypatch.setenv("PYTHONPATH", _source())
    made = _rerun_case(request, case, broken, tmp_path)
    page_of, where = made["page"], made["phase"]
    done = "Analyzed again." if where == "analysis" else "Written again."
    box = "#analysis-again-ask" if where == "analysis" else "#report-write-ask"
    session = start_dashboard_session(output=str(page_of), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 1000},
                                    color_scheme="light")
            page.set_default_timeout(60000)
            page.goto(session.url + "#" + where, wait_until="domcontentloaded")
            button = "#analysis-again" if where == "analysis" else "#report-write"
            page.wait_for_selector(button + ":not([hidden])")
            page.click(button)
            page.click(box + " .fix-confirm")
            page.wait_for_function(
                "([box, done, reason]) => { const t = (document.querySelector(box) || {})"
                ".textContent || ''; return t.includes(done) || t.includes(reason); }",
                arg=[box, done, made["reason"]], timeout=300000)
            line = box + (" .again-said" if where == "analysis" else " .fix-said")
            said = page.text_content(line)
            code = page.eval_on_selector_all(line + " code",
                                             "nodes => nodes.map(n => n.textContent)")
            browser.close()
    finally:
        session.server.shutdown()
    ended = round_ended(campaign_of(page_of) or page_of)
    command = " ".join(ended.argv)
    terminal = command if command in ended.carry_on_on_a_page else ""
    if broken:
        assert done not in said and made["reason"] in said, said
        for name in made["names"]:
            assert f"{name} failed in its analysis" in said, said
    else:
        assert said.startswith(done), said
    assert said.endswith(f"{ended.said} {ended.carry_on_on_a_page}"), said
    assert code == ([terminal] if terminal else []), (code, said)


_INTERRUPTED_IN = '''
import importlib, os, signal, sys
from fastmdxplora.cli.main import main

# Ctrl-C as a terminal leaves it, whatever started the tests.
signal.signal(signal.SIGINT, signal.default_int_handler)
where, _, name = sys.argv[1].rpartition(":")
module = importlib.import_module(where)
real = getattr(module, name)
done = []

def interrupted(*args, **kwargs):
    if not done:
        done.append(1)
        os.kill(os.getpid(), signal.SIGINT)
    return real(*args, **kwargs)

setattr(module, name, interrupted)
sys.exit(main(sys.argv[2:]))
'''


@pytest.mark.parametrize("case", ["cut", "stopped", "failed"])
def test_a_rerun_stopped_by_ctrl_c_is_counted_stopped(request, tmp_path, case):
    """Ctrl-C in the first run's analysis of a rerun: over a cut round it
    was tallied "1 cut in the rounds" and exited 1, the round's code; it is
    stopped, and the rerun exits 130, on every round's ending."""
    import os
    import subprocess
    import sys

    made = _rerun_case(request, case, False, tmp_path)
    driver = tmp_path / "interrupted_in.py"
    driver.write_text(_INTERRUPTED_IN)
    ran = subprocess.run([sys.executable, str(driver), "fastmdxplora.analysis.analyze:run",
                          "analyze", "--output", str(made["study"]), "--rerun"],
                         env={**os.environ, "PYTHONPATH": _source()}, cwd=tmp_path,
                         capture_output=True, text=True, timeout=_A_STUDY_S_WAIT)
    said = ran.stdout + ran.stderr
    assert ran.returncode == 130, said[-2000:]
    # Its headline is the study's round's ("Batch failed" for a failed one).
    assert ": 0 ok, 0 error(s), 1 stopped, 1 skipped, 2 total" in said, said[-2000:]


def test_the_gui_reads_a_rerun_by_its_exit_and_words_what_failed(tmp_path):
    """Its rerun's exit says what it did: 130 is a stop, not a failure; 1 a
    failure, worded from the phase this rerun recorded failed (with its
    full stop), not one recorded before it began nor one stopped. A
    completed study's failure said only "Manifest: ... (exit code 1 ...)",
    and a rerun stopped by Ctrl+C read failed."""
    import subprocess
    import sys
    from datetime import datetime, timedelta, timezone

    from fastmdxplora.gui.exploration import DashboardRuntime
    from fastmdxplora.simulation.runner import STOPPED_CODE

    study = tmp_path / "study"
    study.mkdir()
    began = datetime.now(timezone.utc)

    def ended_with(code: int, *, at: datetime, refusal: dict | None = None) -> dict:
        record = {"name": "analysis", "status": "error", "started_at": at.isoformat(),
                  "message": "MDTraj failed to load trajectory: Could not open file: "
                             "simulation/production.dcd"}
        if refusal:
            record["refusal"] = refusal
        (study / "manifest.json").write_text(json.dumps({"phases": [record]}))
        (study / "exploration.log").write_text("x - ERROR - What the log says\n")
        runtime = DashboardRuntime(workspace_root=tmp_path, exploration_root=tmp_path)
        runtime.active_root = runtime.running_root = study
        runtime.process = subprocess.Popen([sys.executable, "-c",
                                            f"import sys; sys.exit({code})"])
        runtime.process.wait()
        runtime.process_started_at = began.isoformat()
        runtime.log_path = study / "exploration.log"
        runtime.command = [sys.executable, "-m", "fastmdxplora", "explore"]
        runtime.ran_again = True
        return runtime.snapshot()

    later = began + timedelta(seconds=5)
    failed = ended_with(1, at=later)
    assert failed["status"] == "failed", failed
    assert failed["error"] == ("The analysis phase failed: MDTraj failed to load trajectory: "
                               "Could not open file: simulation/production.dcd."), failed
    stopped = ended_with(130, at=later)
    assert stopped["status"] == "stopped" and stopped["error"] is None, stopped
    for earlier in (ended_with(1, at=began - timedelta(hours=1)),
                    ended_with(1, at=later, refusal={"code": STOPPED_CODE})):
        assert earlier["status"] == "failed", earlier
        assert "phase failed" not in earlier["error"], earlier
        assert "What the log says" in earlier["error"], earlier


def test_a_process_record_is_written_with_the_umask_s_mode(tmp_path, monkeypatch):
    """`mkstemp` made every process record owner-only: another member of
    the group read a study going on another machine as completed."""
    import stat

    from fastmdxplora import orchestrator
    from fastmdxplora.orchestrator import RUN_PROCESS_FILE
    from fastmdxplora.record_access import _umask

    record = tmp_path / RUN_PROCESS_FILE
    orchestrator._put_in_place(record, "{}")
    assert stat.S_IMODE(record.stat().st_mode) == 0o666 & ~_umask()
    # A live process's temporary beside it (another's, writing it now) is
    # left there.
    import subprocess
    import sys

    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        theirs = tmp_path / f"{RUN_PROCESS_FILE}.{other.pid}.x.tmp"
        theirs.write_text("{")
        orchestrator._put_in_place(record, "{}")
        assert theirs.exists()
    finally:
        other.kill()
        other.wait()
    # A rewrite that fails (a full disk) is no error of the heartbeat's.
    record.write_text("{\"pid\": 4")
    orchestrator._mark_written_here(record)

    def full(path):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(orchestrator, "_write_run_process", full)
    orchestrator.stamp_alive(record)


def test_the_heartbeat_lives_on_past_a_stamp_that_fails(tmp_path, monkeypatch):
    """A stamp that raised ended the heartbeat's thread: a study going on
    another machine then read quiet after the hour."""
    import threading
    import time

    from fastmdxplora import orchestrator

    calls = []

    def failing(path):
        calls.append(path)
        raise OSError(28, "No space left on device")

    monkeypatch.setenv(orchestrator.HEARTBEAT_ENV, "0.02")
    monkeypatch.setattr(orchestrator, "stamp_alive", failing)
    folder = tmp_path / "held"
    folder.mkdir()
    orchestrator._hold_run_process(folder)
    beating = threading.Thread(target=orchestrator._heartbeat, daemon=True)
    try:
        beating.start()
        time.sleep(0.4)
        assert beating.is_alive() and len(calls) >= 2, calls
    finally:
        orchestrator._release_run_process(folder)


def test_a_heartbeat_never_leaves_its_record_empty_or_cut(tmp_path):
    """The heartbeat wrote its record in place (truncate, then write): a
    reader looping found it empty thousands of times, and a stamp cut short
    (a kill, a full disk) left it unparsable: a killed study read running,
    a live one missed the refusal of a second process."""
    import time

    from fastmdxplora.orchestrator import RUN_PROCESS_FILE

    folder = tmp_path / "beating"
    process = _holding(folder, "beat", "0.002")
    try:
        assert process.stdout.readline().strip() == "held"
        unread = 0
        until = time.monotonic() + 3
        while time.monotonic() < until:
            try:
                json.loads((folder / RUN_PROCESS_FILE).read_text())
            except ValueError:
                unread += 1
        assert unread == 0, unread
    finally:
        process.kill()
        process.wait()
    cut = tmp_path / "cut"
    process = _holding(cut, "cut", "1000")
    try:
        before = None
        for _ in range(200):
            if (cut / RUN_PROCESS_FILE).is_file():
                before = (cut / RUN_PROCESS_FILE).read_text()
                break
            time.sleep(0.01)
        assert process.stdout.readline().strip() == "stamped"
        after = (cut / RUN_PROCESS_FILE).read_text()
        assert json.loads(after) == json.loads(before), after
        assert [path.name for path in cut.iterdir()] == [RUN_PROCESS_FILE]
    finally:
        process.kill()
        process.wait()


def test_a_study_held_in_a_long_phase_elsewhere_reads_going(tmp_path, monkeypatch):
    """A study on another machine in a phase longer than the hour (its
    analysis), its live record's stamp an hour old, read quiet, and a plain
    resume from here rewrote it while it ran: its process now stamps its
    record in every phase. Killed, it goes quiet after the span."""
    import os
    import signal
    import time

    from fastmdxplora.gui.workspace import study_state
    from fastmdxplora.orchestrator import RUN_PROCESS_FILE
    from fastmdxplora.simulation import resume

    config = _study_of_seeds(tmp_path, seeds=False)
    # Its heartbeat every half second (`orchestrator.HEARTBEAT_ENV`).
    monkeypatch.setenv("FASTMDX_HEARTBEAT_SECONDS", "0.5")
    hold = tmp_path / "hold"
    process = _held_study(["fastmdxplora.cli.main", "explore", "-c", str(config)], tmp_path,
                          hold, call="fastmdxplora.analysis.orchestrator:get_analysis_class")
    study = tmp_path / "study"
    # The hour, scaled to 4 s: eight heartbeats.
    monkeypatch.setattr(resume, "ANOTHER_HOST_QUIET_SECONDS", 4)
    try:
        _until_held(process, hold, ["call"])
        _written_on(study, "host")
        status = json.loads((study / "simulation" / "live_status.json").read_text())
        status["last_update_timestamp"] = "2000-01-01T00:00:00+00:00"
        (study / "simulation" / "live_status.json").write_text(json.dumps(status))
        time.sleep(6)
        assert study_state(study) == "running"
        from fastmdxplora.gui.records_answer import answer_from_the_records

        # The Agent's answers say it goes, and offer no extension that its
        # going refuses.
        for question in ("found", "long_enough", "strengthen"):
            answer = answer_from_the_records(study, question) or ""
            assert answer.count("It is going on") == 1, (question, answer[:600])
            assert "It is still going: It is" not in answer, (question, answer[:600])
            assert "--simulate-resume-from" not in answer, (question, answer[:600])
        before = {name: data for name, data in _snapshot(study).items()
                  if not name.endswith(RUN_PROCESS_FILE)}
        code, printed, said = _resume_by_the_cli(study)
        assert code != 0 and "It is going on another-host" in printed + said, printed[-800:]
        after = {name: data for name, data in _snapshot(study).items()
                 if not name.endswith(RUN_PROCESS_FILE)}
        assert after == before
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()
    time.sleep(6)
    assert study_state(study) == "interrupted"


def test_an_extension_of_a_one_run_study_held_is_refused(stopped_at_its_end, tmp_path,
                                                          monkeypatch):
    """`explore` with `resume_from` a one-run study running on this machine,
    or going on another, ran a piece into the live run and joined a
    trajectory that is not one run."""
    import os
    import signal
    from datetime import datetime, timezone

    from fastmdxplora.orchestrator import RUN_PROCESS_FILE
    from fastmdxplora.simulation.resume import extend_study

    # Going on another machine: a finished study whose process record is
    # another's, stamped now.
    study = tmp_path / "elsewhere" / "study"
    shutil.copytree(stopped_at_its_end, study, symlinks=True)
    now = datetime.now(timezone.utc).isoformat()
    (study / RUN_PROCESS_FILE).write_text(json.dumps({
        "pid": 4242, "started_at": now, "alive_at": now, "host": "nodeB"}))
    before = _snapshot(study)
    answer = extend_study(study, more_ns=0.0006)
    assert not answer["ok"] and "It is going on nodeB" in answer["error"], answer
    assert _snapshot(study) == before
    # Running on this machine: held as its rule is to be judged.
    (tmp_path / "here").mkdir()
    config = _study_of_seeds(tmp_path / "here", seeds=False)
    hold = tmp_path / "here" / "hold"
    process = _held_study(["fastmdxplora.cli.main", "explore", "-c", str(config)],
                          tmp_path / "here", hold,
                          call="fastmdxplora.simulation.stopping:run_until_known")
    try:
        _until_held(process, hold, ["call"])
        study = tmp_path / "here" / "study"
        before = _snapshot(study)
        answer = extend_study(study, more_ns=0.0006)
        assert not answer["ok"] and "still going" in answer["error"], answer
        assert _snapshot(study) == before
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def test_a_study_killed_elsewhere_is_taken_over_or_resumed_after_the_hour(
        killed_before_its_rule, tmp_path, monkeypatch):
    """A study killed on another machine or container was refused for a
    day, told neither when nor any way past it."""
    from fastmdxplora.cli.main import main
    from fastmdxplora.gui.workspace import study_state

    if killed_before_its_rule["seeds"]:
        pytest.skip("one run says it; two seeds take twice as long")
    help_said = io.StringIO()
    with contextlib.redirect_stdout(help_said), pytest.raises(SystemExit):
        main(["resume", "--help"])
    assert "--taken-over" in help_said.getvalue() and "another node" in help_said.getvalue()
    for how in ("taken over", "after the hour"):
        study = tmp_path / how.replace(" ", "_") / "study"
        shutil.copytree(killed_before_its_rule["study"], study, symlinks=True)
        _written_on(study, "host")
        code, printed, said = _resume_by_the_cli(study)
        assert code != 0 and "--taken-over" in printed + said, printed[-800:]
        assert "resumed from here after" in printed + said, printed[-800:]
        if how == "taken over":
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                assert main(["resume", "--taken-over", str(study)]) == 0, out.getvalue()[-1500:]
        else:
            with monkeypatch.context() as patch:
                _quiet(patch)
                assert _resume_by_the_cli(study)[0] == 0
        assert study_state(study) == "completed", how


def test_a_finished_study_does_not_read_going_from_a_killed_piece_s_record(cut_one, tmp_path):
    """A study whose round's extension was killed, then resumed to its end,
    left that piece's process record, and read running elsewhere for a day
    where the record was another machine's."""
    from fastmdxplora.gui.workspace import study_state
    from fastmdxplora.orchestrator import RUN_PROCESS_FILE

    study = tmp_path / "study"
    shutil.copytree(cut_one["study"], study, symlinks=True)
    assert _resume_by_the_cli(study)[0] == 0
    assert study_state(study) == "completed"
    assert list(study.glob(f"segment-*/{RUN_PROCESS_FILE}")), "no piece record to read"
    _written_on(study, "host")
    assert study_state(study) == "completed"


def test_a_one_run_study_whose_resume_died_judging_owes_its_rule(killed_before_its_rule,
                                                                 tmp_path):
    """Killed before its rule, then its resume killed as it judged: read
    Completed with no fix, and would be shared, from the dead resume's
    process record newer than its Manifest."""
    if killed_before_its_rule["seeds"]:
        pytest.skip("two seeds read it right already")
    study = tmp_path / "study"
    shutil.copytree(killed_before_its_rule["study"], study, symlinks=True)
    assert _killed_in(tmp_path, "fastmdxplora.simulation.stopping:run_until_known", "resume",
                      str(study)) == 9
    _every_reader_agrees(study, "interrupted")


def _a_run_s_page_of_a_study_with_its_manifest(study: Path, manifest: str) -> Path:
    """The study's batch_manifest.json linked out, removed or not JSON."""
    path = study / "batch_manifest.json"
    if manifest == "linked out":
        outside = study.parent / "elsewhere.json"
        path.rename(outside)
        path.symlink_to(outside)
    elif manifest == "missing":
        path.unlink()
    else:
        path.write_text("{not json")
    return study / "runs" / "tri__random-seed-1"


@pytest.mark.parametrize("manifest", ["linked out", "missing", "not json"])
def test_a_run_whose_study_s_record_of_runs_is_not_read_is_a_run_of_it(join_failed, tmp_path,
                                                                       manifest):
    """A run whose study's batch_manifest.json was linked out, missing or
    broken was taken for a study of its own: its card named the study's
    config as not read, its failed join read cut and named the run for the
    resume."""
    from fastmdxplora.gui.server import _status_records

    if not (join_failed["study"] / "batch_manifest.json").is_file():
        pytest.skip("a study of one run")
    study = tmp_path / "study"
    shutil.copytree(join_failed["study"], study, symlinks=True)
    run = _a_run_s_page_of_a_study_with_its_manifest(study, manifest)
    card = stopping_card(run)
    assert card["ok"] and "../../resolved_config.yml" not in (card.get("unavailable") or []), \
        card
    health = _status_records(run)["health"]
    assert health["state"] == "failed", health
    assert "given this study (study)" in health["message"], health["message"]


@pytest.mark.parametrize("folder", ["run page", "study under runs"])
def test_problems_are_read_on_a_run_page_and_a_study_under_runs(sequential_stopped,
                                                                one_killed_in_its_join,
                                                                tmp_path, folder):
    """Problems were withheld ("Records unavailable") on a run page of a
    stopped round and on a study saved in a folder named runs: the study
    above was read outside the held read."""
    from fastmdxplora.gui.problems_view import problems_payload

    if folder == "run page":
        study = tmp_path / "study"
        shutil.copytree(sequential_stopped, study, symlinks=True)
        read = study / "runs" / "tri__random-seed-1"
    else:
        # Its piece not joined: its study asked of the folder above.
        read = tmp_path / "w" / "runs" / "study"
        shutil.copytree(one_killed_in_its_join, read, symlinks=True)
    problems = problems_payload(read)
    assert problems.get("available", True) and not problems.get("unavailable"), problems


def test_the_join_s_code_is_of_its_kind_and_its_fix_waits(join_failed, tmp_path):
    """`simulation.round.join_failed` was ENVIRONMENTAL, and its fix offered
    the same join to run at once, which fails the same way."""
    from fastmdxplora.gui.fixes_view import runnable
    from fastmdxplora.refusals import Kind, code_for

    code = code_for("simulation.round.join_failed")
    assert code.kind == Kind.SEMANTIC and not code.retryable
    study = tmp_path / "study"
    shutil.copytree(join_failed["study"], study, symlinks=True)
    fixes = [fix for fix in _fixes_of_its_pages(study)
             if fix.code == "simulation.round.join_failed"]
    assert fixes and not any(runnable(fix) for fix in fixes), fixes
    assert all("Put right what failed the join" in fix.fix for fix in fixes), fixes


def test_a_failed_round_carried_on_by_a_resume_that_died_reads_cut(join_failed, tmp_path):
    """A resume joined the piece whose join had failed, then was killed:
    every reader said "failed at joining", that join since done."""
    from fastmdxplora.simulation.stopping import round_ended

    study = tmp_path / "study"
    shutil.copytree(join_failed["study"], study, symlinks=True)
    # One run: killed after its join and report, as its rule is judged
    # again; two seeds: as it joins seed-1's piece.
    assert _killed_in(tmp_path, "_judge_the_run_again" if not (
        study / "batch_manifest.json").is_file() else "_join_and_analyse",
        "resume", str(study)) == 9
    ended = round_ended(study)
    assert ended is not None and "the resume carrying it on ended" in ended.said, ended
    _every_reader_agrees(study, "interrupted")


def test_share_s_refusal_with_no_round_names_what_did_not_finish(failed, tmp_path,
                                                                  monkeypatch):
    """Where no round says how a study ended, share said "did not finish:
    It is failed." where it had named the phase."""
    from fastmdxplora.gui import workspace
    from fastmdxplora.sharing.pack import ShareRefused, finished_manifest
    from fastmdxplora.simulation import stopping

    run = tmp_path / "run"
    shutil.copytree(failed["study"] / "runs" / "tri__random-seed-1", run, symlinks=True)
    manifest = json.loads((run / "manifest.json").read_text())
    manifest["phases"][-1]["status"] = "failed"
    (run / "manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(workspace, "study_state", lambda folder: "failed")
    monkeypatch.setattr(stopping, "round_ended", lambda study, **kw: None)
    with pytest.raises(ShareRefused) as refused:
        finished_manifest(run)
    said = str(refused.value)
    assert f"({manifest['phases'][-1]['name']})" in said and "It is failed" not in said, said


def test_a_copied_study_s_comparison_says_the_folder_it_was_written_in(failed, tmp_path):
    """The Report page of a copied study of runs gave the saved comparison
    report's `fastmdx resume` of the folder it was copied from."""
    from fastmdxplora.gui.report_page import report_payload

    study = tmp_path / "copy"
    shutil.copytree(failed["study"], study, symlinks=True)
    shown = _shown(report_payload(study))
    assert f"fastmdx resume {study}" in shown, shown[:1500]
    assert "the folder this study was copied from" in shown, shown[:1500]


def test_a_study_read_where_it_was_written_says_nothing_of_a_copy(failed, tmp_path):
    """A study written with a relative output, or read through a link (as
    macOS's /tmp is), said "the folder this study was copied from" on its
    own Report page."""
    from fastmdxplora.gui.report_page import report_payload

    study = tmp_path / "real" / "study"
    shutil.copytree(failed["study"], study, symlinks=True)
    report = study / "comparison" / "comparison_report.md"
    written = report.read_text().replace(str(failed["study"]), str(study))
    report.write_text(written)
    # Read in place, and through a link to its folder.
    (tmp_path / "link").symlink_to(tmp_path / "real")
    for read in (study, tmp_path / "link" / "study"):
        shown = _shown(report_payload(read))
        assert "copied from" not in shown, (read, shown[:800])
    # Its output given relative to where it was run.
    report.write_text(written.replace(f"fastmdx resume {study}", "fastmdx resume real/study"))
    shown = _shown(report_payload(study))
    assert "copied from" not in shown, shown[:800]


@pytest.mark.parametrize("built", ["one", "stopped_at_its_end"])
def test_a_study_in_a_folder_named_runs_has_its_own_card(request, tmp_path, built):
    """A study of its own saved in a folder named `runs` was read as a run
    of the folder above it: its rule card "this study runs for a fixed
    length", its status bar the wrong study's."""
    from fastmdxplora.gui.server import _status_records
    from fastmdxplora.gui.workspace import study_state

    value = request.getfixturevalue(built)
    alone = tmp_path / "alone" / "study"
    inside = tmp_path / "w" / "runs" / "study"
    for where in (alone, inside):
        shutil.copytree(value["study"] if isinstance(value, dict) else value, where,
                        symlinks=True)
    if built == "stopped_at_its_end":
        for where in (alone, inside):
            assert _resume_by_the_cli(where)[0] == 0
    for key in ("ok", "outcome_label", "outcome"):
        assert stopping_card(inside).get(key) == stopping_card(alone).get(key), (
            key, stopping_card(inside), stopping_card(alone))
    assert stopping_card(inside)["ok"], stopping_card(inside)
    assert study_state(inside) == study_state(alone)
    assert (_status_records(inside)["health"]["state"]
            == _status_records(alone)["health"]["state"])


def test_every_reader_says_a_refused_resume_and_a_completed_study_alike(
        stopped_mid_round, stopped_at_its_end, tmp_path):
    """A study whose run refused its resume reads failed on every reader;
    one whose resume finished reads completed on every reader."""
    refused = tmp_path / "refused" / "study"
    shutil.copytree(stopped_mid_round["study"], refused, symlinks=True)
    checkpoint = refused / "runs" / SECOND / "segment-001" / "simulation" / "checkpoint.chk"
    data = bytearray(checkpoint.read_bytes())
    data[len(data) // 2] ^= 0xFF
    checkpoint.write_bytes(bytes(data))
    assert _resume_by_the_cli(refused)[0] == 1
    _every_reader_agrees(refused, "failed")
    done = tmp_path / "done" / "study"
    shutil.copytree(stopped_at_its_end, done, symlinks=True)
    assert _resume_by_the_cli(done)[0] == 0
    _every_reader_agrees(done, "completed")


@pytest.fixture(scope="module")
def one_killed_in_its_join(tmp_path_factory):
    """One run with a rule, its process killed as its round's piece, run to
    its end, was to be joined (R20 H1, M1)."""
    root = tmp_path_factory.mktemp("killed_in_its_join")
    config = _study_of_seeds(root, seeds=False)
    assert _killed_in(root, "_join_and_analyse", "explore", "-c", str(config)) == 9
    return root / "study"


def test_one_run_killed_in_its_join_is_fixed_in_its_own_words(one_killed_in_its_join,
                                                              tmp_path):
    """Its fixes card, the CLI's fixes and the Agent's remedies said "Nothing
    has been written for a day" a minute after the kill, and its header
    "Failed"."""
    from fastmdxplora.gui.server import _status_records
    from fastmdxplora.remedies import remedies_of

    study = tmp_path / "study"
    shutil.copytree(one_killed_in_its_join, study, symlinks=True)
    assert _status_records(study)["health"]["state"] == "interrupted"
    said = " ".join(str(remedy) for remedy in remedies_of(study))
    assert "fastmdx resume" in said and "Nothing has been written" not in said, said


def test_a_lock_that_is_not_a_regular_file_is_no_lock(tmp_path):
    """A FIFO at the lock's name hung the GUI's study list: its read blocked
    for ever, where it was refused before."""
    import os
    import threading

    from fastmdxplora.gui.workspace import study_state
    from fastmdxplora.record_access import confined_records
    from fastmdxplora.simulation.resume import CARRYING_ON_FILE, being_carried_on

    if not hasattr(os, "mkfifo"):
        pytest.skip("this system makes no FIFO")
    study = tmp_path / "study"
    study.mkdir()
    (study / "resolved_config.yml").write_text("simulation: {}\n")
    os.mkfifo(study / CARRYING_ON_FILE)
    answered: dict = {}

    def read():
        with confined_records(study, lambda path: True) as scope:
            answered["going"] = being_carried_on(study)
            answered["state"] = study_state(study)
        answered["refused"] = set(scope.denied)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    reader.join(timeout=20)
    if reader.is_alive():
        # Opened for writing, the FIFO lets the hung read go.
        os.close(os.open(study / CARRYING_ON_FILE, os.O_WRONLY | os.O_NONBLOCK))
        pytest.fail("the study's lock, a FIFO, was read as a file and blocked")
    assert answered["going"] is False and answered["state"] != "not read", answered
    assert CARRYING_ON_FILE not in answered["refused"]


def test_a_plain_run_s_stop_as_production_begins_keeps_the_grace(tmp_path, monkeypatch):
    """Heard before a plain run's first chunk, the stop stepped a whole frame
    interval, past the grace period (4,000 steps and 19 s where 1,000 and
    4.7 s were taken before)."""
    from fastmdxplora.cli.main import main
    from fastmdxplora.simulation import runner

    pdb = tmp_path / "tri-ala.pdb"
    pdb.write_text(TRI_ALANINE)
    config = json.loads(json.dumps(_CONFIG))
    config.pop("sweep")
    config["simulation"].pop("stop_when")
    config["simulation"].update(production_steps=4000, trajectory_interval_steps=2000,
                                checkpoint_interval_steps=2000)
    config["exclude_phase"] = ["analysis", "report"]
    config["systems"] = [{"id": "tri", "system": str(pdb)}]
    config["output"] = str(tmp_path / "study")
    (tmp_path / "study.yml").write_text(yaml.safe_dump(config))
    # Its stop heard as its production begins; a frame further than allowed.
    monkeypatch.setenv(runner.STOP_GRACE_ENV, "0.000001")

    def requested(self):
        import signal

        if self.signal is None:
            self.signal = signal.SIGINT
        return True

    monkeypatch.setattr(runner._StopRequests, "requested", property(requested))
    with contextlib.redirect_stdout(io.StringIO()):
        assert main(["explore", "-c", str(tmp_path / "study.yml")]) == 130
    manifest = json.loads((tmp_path / "study" / "manifest.json").read_text())
    simulation = next(p for p in manifest["phases"] if p["name"] == "simulation")
    step = simulation["refusal"]["details"].get("details", simulation["refusal"]["details"])[
        "step"]
    # Stopped after its first chunk, its frame refused past the grace.
    assert 0 < step < 2000, simulation["message"]
    assert "further than" in simulation["message"], simulation["message"]


def test_a_resume_killed_in_its_join_after_a_stop_reads_cut_however_its_files_are_dated(
        sequential_stopped, tmp_path):
    """A round stopped when asked, then a resume killed as it joined seed-2's
    piece: the study said "stopped when asked" by the earlier stop's words,
    and seed-2 read stopped or cut by which of two files was written last
    (a touch, or a copy that does not keep times, turned it)."""
    import os

    from fastmdxplora.gui.telemetry import status_as_it_stands
    from fastmdxplora.gui.workspace import study_state
    from fastmdxplora.simulation.stopping import round_ended

    study = tmp_path / "study"
    shutil.copytree(sequential_stopped, study, symlinks=True)
    assert _killed_in(tmp_path, "_join_and_analyse", "resume", str(study)) == 9
    for touched in (False, True):
        if touched:
            os.utime(study / "stopping.json")
        assert study_state(study) == "interrupted", touched
        assert "the resume carrying it on ended" in round_ended(study).said
        joined = [run for run in ("tri__random-seed-1", SECOND)
                  if status_as_it_stands(study / "runs" / run).get("join_owed")]
        assert joined
        for run in joined:
            words = status_as_it_stands(study / "runs" / run)["latest_error"]
            assert "stopped when asked" not in words and "process ended" in words, words
