"""A piece's live record not read: the study's status is not read.

A study carried on is read as one run across its pieces, its status the
newest piece's. With only the newest piece's live record linked out of the
study (or its whole folder), the piece was passed over as one that kept no
record, and the study read as its first piece: stopped by Ctrl+C, with that
piece's 16 frames and a resume command, beside a list naming the record.
The status is now not read, and said so, as for the study's own record.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_the_study_pages_read_only_the_study import (
    _OPENED,
    _WATCHED,
    _a_study_carried_on,
    _ask,
    _link,
    _watch,
    _write,
)

PUBLIC = pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])
RECORD = "segment-001/simulation/live_status.json"


def _first_piece_stopped(tmp_path: Path) -> Path:
    """A study whose first piece was stopped by Ctrl+C at 16 frames, carried
    on in `segment-001` to the end of its 100."""
    root = _a_study_carried_on(tmp_path)
    _write(root, "simulation/live_status.json", {
        "status": "stopped", "stage": "production", "current_step": 1600,
        "total_planned_steps": 10000, "current_frame_count": 16, "planned_frame_count": 100,
        "timestep_fs": 2.0, "latest_error": "Production was stopped by Ctrl+C at step 1,600.",
        "current_checkpoint_path": str(root / "simulation" / "checkpoint.chk"),
        "last_update_timestamp": "2026-10-01T00:00:00+00:00"})
    _write(root, "simulation/checkpoint.chk.json", {
        "stage": "production", "step": 1600, "trajectory_interval_steps": 100})
    _write(root, RECORD, {
        "status": "completed", "stage": "production", "current_step": 8400,
        "total_planned_steps": 8400, "current_frame_count": 84, "planned_frame_count": 84,
        "timestep_fs": 2.0, "last_update_timestamp": "2026-10-02T00:00:00+00:00"})
    return root


def _linked_out(root: Path, tmp_path: Path, relative: str) -> Path:
    """``relative`` moved outside the study, a link left in its place: what
    it leads to is the study's own, so only the reads can tell."""
    outside = tmp_path / "outside"
    target = outside / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    (root / relative).rename(target)
    _link(root / relative, target)
    return outside


def _asked(root: Path, tmp_path: Path, outside: Path, route: str, public: bool) -> dict:
    _watch(outside)
    try:
        status, body = _ask(root, tmp_path, route, public=public)
        opened = list(_OPENED)
    finally:
        _WATCHED.clear()
    assert status == 200
    assert opened == []
    return json.loads(body)


@PUBLIC
def test_every_record_read_the_study_is_completed(tmp_path, public):
    """No link: the study is its newest piece's word, completed."""
    root = _first_piece_stopped(tmp_path)
    answer = json.loads(_ask(root, tmp_path, "/api/status", public=public)[1])
    assert answer["status"]["status"] == "completed"
    assert answer["health"]["state"] != "not read"


@PUBLIC
@pytest.mark.parametrize("linked", [RECORD, "segment-001"], ids=["its-record", "its-folder"])
def test_a_piece_s_live_record_not_read_is_not_its_first_piece_s(tmp_path, linked, public):
    root = _first_piece_stopped(tmp_path)
    outside = _linked_out(root, tmp_path, linked)

    status = _asked(root, tmp_path, outside, "/api/status", public)
    assert status["status"].get("status") != "stopped"
    assert "Ctrl+C" not in json.dumps(status)
    assert status["health"]["state"] == "not read"
    # The record, or, where its piece's folder is the link, that folder said
    # once as a folder (`series.folder_out`).
    named = RECORD if linked == RECORD else f"{linked}/ is a link out of the study"
    assert named in status["health"]["message"]
    assert RECORD in status["unavailable"] or f"{linked}/" in status["unavailable"]

    results = _asked(root, tmp_path, outside, "/api/results", public)
    said = {card["label"]: card["value"] for card in results["summary"]}
    assert said["Latest status"] == "not read"
    assert results["simulation"]["n_production_frames"] != 16
    assert "checkpoint.chk" not in results["system"]["checkpoint"]
    assert "Ctrl+C" not in json.dumps(results)


def _with_final_states(tmp_path: Path, *, said: bool) -> Path:
    """A study carried on, each piece with its final state; its
    checkpoint's record saying it finished where ``said``, else silent
    (as one written before the record said so)."""
    root = _a_study_carried_on(tmp_path)
    for piece in ("", "segment-001/"):
        _write(root, f"{piece}simulation/state_final.xml", "<State/>\n")
        if not said:
            _write(root, f"{piece}simulation/checkpoint.chk.json", {
                "stage": "production", "step": 10000, "trajectory_interval_steps": 100})
    return root


@PUBLIC
def test_a_final_state_not_needed_is_not_refused(tmp_path, public):
    """A piece's checkpoint record says it finished: its final state is not
    what says so, and its link out does not leave the production not read."""
    root = _with_final_states(tmp_path, said=True)
    before = json.loads(_ask(root, tmp_path, "/api/status", public=public)[1])["times"]
    assert before["production_ns"] is not None
    outside = _linked_out(root, tmp_path, "segment-001/simulation/state_final.xml")

    times = _asked(root, tmp_path, outside, "/api/status", public)["times"]
    assert times["production_ns"] == before["production_ns"]
    assert not times.get("production_not_read")


@PUBLIC
def test_a_final_state_that_says_the_piece_finished_is_still_refused(tmp_path, public):
    """Its checkpoint record silent, the final state is what says a piece
    finished: linked out, the production is not read, and it is named."""
    root = _with_final_states(tmp_path, said=False)
    outside = _linked_out(root, tmp_path, "segment-001/simulation/state_final.xml")

    answer = _asked(root, tmp_path, outside, "/api/status", public)
    assert answer["times"]["production_ns"] is None
    assert "segment-001/simulation/state_final.xml" in answer["times"]["production_not_read"]
