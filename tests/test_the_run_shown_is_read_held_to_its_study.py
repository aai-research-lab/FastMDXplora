"""The run a study of several plays is chosen and read held to the study.

A study of several runs plays one of them in the Viewer (`run_shown`), the
others set beside it (`runs_together`). Each is read from the study's own
records: a run folder linked out of the study is passed over, so neither
its frames nor its exact frames are read, written or sent; the records the
runs played together are read from (the frames written beside the run
played, its simulation's parameters) are not read through a link either,
and are said not read. On this computer and on a public bind.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

import pytest

from tests.test_the_study_pages_read_only_the_study import _OPENED, _WATCHED, _ask, _link, _watch

md = pytest.importorskip("mdtraj")


def _runs(tmp_path: Path, *frames: int) -> Path:
    """A study of runs ``r1``, ``r2``, ... of these many frames, each a
    finished run of the same atoms."""
    from tests.test_a_graph_opens_exact_source_frame import _study

    root = tmp_path / "study"
    (root / "runs").mkdir(parents=True)
    (root / "batch_manifest.json").write_text(json.dumps(
        {"planned": [{"run_id": f"r{k + 1}"} for k in range(len(frames))], "runs": []}),
        encoding="utf-8")
    for k, count in enumerate(frames):
        _study(root / "runs" / f"r{k + 1}", frame_count=count, residues=2)
    return root


def _moved_out(root: Path, tmp_path: Path, relative: str) -> Path:
    """``relative`` moved out of the study, a link left in its place."""
    target = tmp_path / "outside" / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    (root / relative).rename(target)
    _link(root / relative, target)
    return target


def _listed(folder: Path) -> dict[str, tuple[int, int]]:
    """Every file under ``folder``, with its size and time."""
    return {str(path.relative_to(folder)): (path.stat().st_size, path.stat().st_mtime_ns)
            for path in sorted(folder.rglob("*")) if path.is_file()}


@pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])
def test_a_run_linked_out_of_the_study_is_not_played(tmp_path, public):
    from fastmdxplora.gui.trajectory_frames import frames_info

    root = _runs(tmp_path, 13, 7)
    # The run moved out had its frames written, and so its signature known.
    theirs = frames_info(root / "runs" / "r1")["source_signature"]
    outside = _moved_out(root, tmp_path, "runs/r1")
    before = _listed(outside)
    _watch(outside)
    try:
        asked = {route: _ask(root, tmp_path, route, public=public) for route in (
            "/api/frames-info", "/structure/frames.dcd", "/structure/frames-topology.pdb",
            "/api/frames-pieces",
            f"/api/exact-frame?frame=12&signature={quote(theirs, safe='')}",
            f"/structure/exact-frame.xtc?frame=12&signature={quote(theirs, safe='')}",
            f"/structure/frames.dcd?exact_frame=12&signature={quote(theirs, safe='')}")}
        opened = sorted(set(_OPENED))
    finally:
        _WATCHED.clear()
    assert opened == []
    assert _listed(outside) == before
    info = json.loads(asked["/api/frames-info"][1])
    assert info["n_frames_total"] == 7
    assert [run["run_id"] for run in info["runs_together"]["runs"]] == ["r2"]
    assert "runs/r1 was not read" in info["not_read"]
    # What is played and sent is the study's own run, never the one linked out.
    own = root / "runs" / "r2" / "simulation"
    status, body = asked["/structure/frames.dcd"]
    assert status == 200 and body == (own / "frames.dcd").read_bytes()
    status, body = asked["/structure/frames-topology.pdb"]
    assert status == 200 and body == (own / "frames_topology.pdb").read_bytes()
    pieces = json.loads(asked["/api/frames-pieces"][1])
    assert pieces["ok"] is True and pieces["signature"] == info["signature"], pieces
    exact = json.loads(next(body for route, (_, body) in asked.items()
                            if route.startswith("/api/exact-frame")))
    assert exact["ok"] is False, exact
    assert [status for route, (status, _) in asked.items() if "exact" in route
            and route.startswith("/structure/")] == [404, 404]
    assert str(tmp_path) not in json.dumps(info)


@pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])
def test_the_records_the_runs_are_played_from_are_not_read_through_a_link(tmp_path, public):
    root = _runs(tmp_path, 9, 7)
    # The run played's parameters linked out, naming a length of its own.
    parameters = _moved_out(root, tmp_path, "runs/r1/simulation/simulation_parameters.json")
    parameters.write_text(json.dumps({"duration_ns_actual": 777.0}), encoding="utf-8")
    _watch(parameters.parent)
    try:
        status, body = _ask(root, tmp_path, "/api/frames-info", public=public)
        opened = sorted(set(_OPENED))
    finally:
        _WATCHED.clear()
    assert opened == []
    said = json.loads(body)
    assert status == 200 and said["playback_available"] is True, said
    assert "runs/r1/simulation/simulation_parameters.json was not read" in said["not_read"]
    # No time spread over a length read through the link.
    assert said["frame_times_ns"] == [None] * said["n_frames_browser"]

    # The index of the runs set beside it linked out, saying other frames.
    index = _moved_out(root, tmp_path, "viewer_runs/index.json")
    kept = json.loads(index.read_text(encoding="utf-8"))
    kept["runs"]["r2"]["entry"]["frames"] = 777
    index.write_text(json.dumps(kept), encoding="utf-8")
    before = index.read_bytes()
    _watch(index.parent)
    try:
        status, body = _ask(root, tmp_path, "/api/frames-info", public=public)
        opened = sorted(set(_OPENED))
    finally:
        _WATCHED.clear()
    assert opened == []
    said = json.loads(body)
    assert "viewer_runs/index.json was not read" in said["not_read"]
    assert all(run["frames"] != 777 for run in said["runs_together"]["runs"])
    assert index.read_bytes() == before
