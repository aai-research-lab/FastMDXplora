"""A study owing the analyses and report of its joined pieces is said so by
the GUI, never "Completed".

`fastmdx resume` read `joined/analysis_owed.json`, and the GUI did not: a
study stopped after its pieces were joined showed "Completed, 8 ps
production in 2 pieces" beside a report of 6 ps and an analysis of 30 of
its 40 frames, and nothing told the person to resume. A study stopped there
by a version that wrote no marker was read as finished by both. The GUI now
reads `resume.analysis_owed`, which also owes the analysis where the
study's analysis is not of its join.

The records are those of a real tri-alanine study extended once and killed
when the joined trajectory's analysis began (`/tmp/R3d/Z` in the review).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _study(tmp_path: Path, *, marker: bool, analysed: str = "joined",
           frames: int = 40, stride: int | None = None) -> Path:
    """A study carried on in two pieces and joined (40 frames), its live
    record saying it completed, every phase in its Manifest done; its
    analysis of ``analysed`` ("joined" or the first piece's "simulation")
    over ``frames`` frames."""
    root = tmp_path / "Z"
    for folder in ("simulation", "segment-001/simulation", "joined", "analysis"):
        (root / folder).mkdir(parents=True)
    (root / "resolved_config.yml").write_text("simulation:\n  duration_ns: 0.008\n",
                                              encoding="utf-8")
    (root / "simulation" / "live_status.json").write_text(json.dumps({
        "status": "completed", "stage": "report", "piece": "segment-001",
        "current_step": 4400, "total_planned_steps": 4400}), encoding="utf-8")
    (root / "manifest.json").write_text(json.dumps({"phases": [
        {"name": name, "status": "ok"}
        for name in ("setup", "simulation", "analysis", "report")]}), encoding="utf-8")
    (root / "joined" / "joined.json").write_text(json.dumps({
        "trajectory": str(root / "joined" / "production.dcd"), "frames": 40,
        "segments": [0, 1]}), encoding="utf-8")
    if marker:
        (root / "joined" / "analysis_owed.json").write_text('{"segment": null}',
                                                            encoding="utf-8")
    (root / "analysis" / "analysis_manifest.json").write_text(json.dumps({
        "trajectory_input": str(root / analysed / "production.dcd"),
        "n_frames": frames,
        "load_kwargs": {"stride": stride, "first": None, "last": None}}), encoding="utf-8")
    return root


def _said(root: Path) -> tuple[dict, dict, str]:
    from fastmdxplora.gui.telemetry import analyze_health, status_as_it_stands
    from fastmdxplora.gui.workspace import study_state

    status = status_as_it_stands(root)
    return status, analyze_health(status, [], root=root), study_state(root)


def test_a_study_whose_join_s_analysis_is_owed_is_not_completed(tmp_path: Path) -> None:
    root = _study(tmp_path, marker=True)
    status, health, state = _said(root)
    assert status["status"] == "interrupted"
    assert status["stage"] == "analysis"
    assert state == "interrupted"
    assert health["headline"] == "Its analysis is owed"
    assert f"fastmdx resume, given this study ({root.name}), runs them" in health["message"]
    # What would fix it says why, not a day's silence, and runs here.
    from fastmdxplora.remedies import _interrupted

    remedy = _interrupted(root)
    assert remedy is not None and remedy.argv == ("resume", str(root))
    assert "after its pieces were joined" in remedy.why and not remedy.decision


def test_without_its_live_record_it_is_not_completed_either(tmp_path: Path) -> None:
    from fastmdxplora.gui.workspace import study_state

    root = _study(tmp_path, marker=True)
    (root / "simulation" / "live_status.json").unlink()
    assert study_state(root) == "interrupted"


def test_its_step_on_the_timeline_says_it_stopped_before_its_analysis(
        tmp_path: Path) -> None:
    from fastmdxplora.gui.study_timeline import _ended

    said = _ended(_study(tmp_path, marker=True))
    # It was no stop at all: a run that completed.
    assert said is not None
    assert said["said"] == "Stopped without finishing, in analysis"
    assert "fastmdx resume runs their analyses and report" in said["detail"]


@pytest.mark.parametrize("analysed, frames", [("simulation", 30), ("joined", 30)])
def test_stopped_by_a_version_that_wrote_no_marker_it_is_owed(
        tmp_path: Path, analysed: str, frames: int) -> None:
    from fastmdxplora.simulation.resume import analysis_owed

    # Its analysis of the first piece (30 frames), or of a join of less.
    root = _study(tmp_path, marker=False, analysed=analysed, frames=frames)
    assert analysis_owed(root)
    status, health, state = _said(root)
    assert (status["status"], state) == ("interrupted", "interrupted")
    assert "fastmdx resume" in health["message"]
    # Its join analysed by a stride the person asked for is the join's.
    strided = _study(tmp_path / "strided", marker=False, frames=20, stride=2)
    assert not analysis_owed(strided)
    assert _said(strided)[0]["status"] == "completed"
