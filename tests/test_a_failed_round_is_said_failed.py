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
    assert printed[-1].startswith(f"`fastmdx resume {failed['study']}` judges its 2 runs "
                                  "again and extends them until what it asks is determined")
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
    # Which runs the round had extended: they are not at one length.
    assert re.search(r"tri__random-seed-1 was extended by [\d.]+ ps and tri__random-seed-2 "
                     r"was not, so the runs are not at one length\.", health["message"])
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
