"""A study of several runs is said in one state wherever it is shown.

Its folder keeps no live record of its own, so the sidebar and its Overview
said "Unknown" (orange) while All studies, reading its runs, said
"completed": two derivations of one state (`gui/telemetry.py` and
`workspace._state_of`). Now the health of such a folder is read from the
same derivation as the card (`workspace.study_state`).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_a_study_of_runs_is_shown_as_one import _a_sweep_study


def _health(root: Path) -> dict:
    from fastmdxplora.gui.telemetry import analyze_health, read_metrics, status_as_it_stands

    return analyze_health(status_as_it_stands(root), read_metrics(root), root=root)


def _set(root: Path, run: int, status: dict) -> None:
    folder = sorted((root / "runs").iterdir())[run]
    (folder / "simulation" / "live_status.json").write_text(json.dumps(status), encoding="utf-8")


DONE = {"stage": "completed", "current_step": 500, "total_planned_steps": 500}


@pytest.fixture()
def sweep(tmp_path) -> Path:
    root = _a_sweep_study(tmp_path)
    _set(root, 1, DONE)
    return root


def test_a_finished_study_of_runs_is_completed_in_every_place(sweep):
    from fastmdxplora.gui.workspace import card_of, study_state

    health = _health(sweep)
    assert card_of(sweep)["state"] == "completed"
    assert study_state(sweep) == "completed"
    assert health["state"] == "completed"
    assert health["headline"] == "Completed"
    assert "unknown" not in json.dumps(health).lower()


def test_a_failed_run_makes_the_study_failed_in_every_place(sweep):
    from fastmdxplora.gui.workspace import card_of

    _set(sweep, 1, {"stage": "failed", "status": "failed", "current_step": 20,
                    "total_planned_steps": 500})
    assert card_of(sweep)["state"] == _health(sweep)["state"] == "failed"


def test_a_study_with_a_run_going_is_running_in_every_place(sweep, monkeypatch):
    from fastmdxplora.gui import exploration
    from fastmdxplora.gui.workspace import card_of

    real = exploration.runs_of_a_study

    def one_going(root):
        runs = real(root)
        runs[1]["state"] = "running"
        return runs

    monkeypatch.setattr(exploration, "runs_of_a_study", one_going)
    assert card_of(sweep)["state"] == _health(sweep)["state"] == "running"


def test_each_run_is_named_by_what_sets_it_apart(sweep):
    from fastmdxplora.gui.exploration import run_label, runs_of_a_study

    assert [run["label"] for run in runs_of_a_study(sweep)] == [
        "temperature 300 K", "temperature 310 K"]
    assert run_label({"simulation.random_seed": 2}) == "seed 2"
    assert run_label({"setup.model": 3, "simulation.temperature_K": 310}) == (
        "model 3, temperature 310 K")


def _confined_health(root: Path) -> dict:
    from fastmdxplora.gui.server import _confined, _status_records

    return _confined(root, _status_records)


def test_a_study_of_runs_read_held_to_it_is_said_as_its_card(sweep):
    from fastmdxplora.gui.workspace import card_of

    answer = _confined_health(sweep)
    assert answer["health"]["state"] == card_of(sweep)["state"] == "completed"
    assert not answer.get("unavailable")


def test_a_run_record_linked_out_of_the_study_is_not_read_for_its_state(sweep, tmp_path):
    """Its state is derived from each run's record: one refused by the held
    read is said not read, not passed over for the rest."""
    outside = tmp_path / "elsewhere.json"
    outside.write_text(json.dumps(DONE), encoding="utf-8")
    record = sorted((sweep / "runs").iterdir())[1] / "simulation" / "live_status.json"
    record.unlink()
    record.symlink_to(outside)
    health = _confined_health(sweep)["health"]
    assert health["state"] == "not read"
    assert "live_status.json" in health["message"] and "not read" in health["message"]


def test_a_record_of_runs_linked_out_is_not_read_not_a_folder_of_runs(sweep, tmp_path):
    batch = sweep / "batch_manifest.json"
    outside = tmp_path / "batch_elsewhere.json"
    outside.write_text(batch.read_text(encoding="utf-8"), encoding="utf-8")
    batch.unlink()
    batch.symlink_to(outside)
    health = _confined_health(sweep)["health"]
    assert health["state"] == "not read"
    assert health["message"].startswith("batch_manifest.json was not read")


def test_its_results_summary_says_the_same_state(sweep):
    """The Overview's "Project status" read only the study's own manifest,
    which a study of runs has none of: thirty-six completed windows were said
    "not run"."""
    from fastmdxplora.gui.server import _results_payload
    from fastmdxplora.gui.workspace import study_state

    said = {row["label"]: row["value"] for row in _results_payload(sweep)["summary"]}
    assert said["Project status"] == study_state(sweep) == "completed"
