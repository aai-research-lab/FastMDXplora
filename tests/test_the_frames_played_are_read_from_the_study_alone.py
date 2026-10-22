"""The frames the Viewer plays are read from the study's own records alone.

`/api/frames-info` writes the frames the browser plays from the study's
trajectory and topology; `/structure/frames.dcd` and
`/structure/frames-topology.pdb` send them (superposed, in between or as
XTC too), and `/api/frames-pieces` with `/structure/frames-piece.xtc` send
them in pieces. Each is held to the study, as an exact frame is
(`record_access`): a trajectory or topology linked out of it, or a topology
its join names outside it, is not opened where it leads and not played, on
this computer or on a public bind, and `/api/frames-info` says it was not
read. MDTraj reads a DCD in C, where the audit hook on `open` does not see
it, so each answer is checked as well.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from urllib.parse import quote

import pytest

from tests.test_the_study_pages_read_only_the_study import _OPENED, _WATCHED, _ask, _link, _watch

md = pytest.importorskip("mdtraj")

#: What a page asks for to play the frames, and for the frames played
#: otherwise: superposed, in between, as XTC, in pieces.
SENT = ["/structure/frames.dcd", "/structure/frames-topology.pdb",
        "/structure/frames.dcd?superposed=backbone",
        "/structure/frames.dcd?between=1&span=0:2:1",
        "/structure/frames.dcd?as=xtc"]


def _study(tmp_path: Path, *, boxed: bool = False) -> Path:
    """A finished study of eleven frames whose browser's frames are written."""
    from fastmdxplora.gui.trajectory_frames import frames_info
    from tests.test_a_graph_opens_exact_source_frame import _study as written

    root = written(tmp_path / "study")
    if boxed:
        production = root / "simulation" / "production.dcd"
        frames = md.load_dcd(str(production),
                             top=str(root / "simulation" / "trajectory_topology.pdb"))
        frames.unitcell_vectors = None
        frames.unitcell_lengths = [[5.0, 5.0, 5.0]] * frames.n_frames
        frames.unitcell_angles = [[90.0, 90.0, 90.0]] * frames.n_frames
        frames.save_dcd(str(production))
    assert frames_info(root)["playback_available"]
    return root


def _moved_out(root: Path, tmp_path: Path, relative: str) -> Path:
    """``relative`` moved out of the study, a link to it left in its place."""
    outside = tmp_path / "outside"
    target = outside / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    (root / relative).rename(target)
    _link(root / relative, target)
    return outside


def _asked(root: Path, tmp_path: Path, routes: list[str], outside: Path, *,
           public: bool) -> tuple[list[tuple[int, bytes]], list[str]]:
    _watch(outside)
    try:
        answers = [_ask(root, tmp_path, route, public=public) for route in routes]
        opened = sorted(set(_OPENED))
    finally:
        _WATCHED.clear()
    return answers, opened


@pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])
def test_the_study_s_own_frames_are_played(tmp_path, public):
    root = _study(tmp_path)
    status, body = _ask(root, tmp_path, "/api/frames-info", public=public)
    said = json.loads(body)
    assert status == 200 and said["playback_available"] is True, said
    assert "not_read" not in said and "unavailable" not in said
    for route in SENT:
        status, body = _ask(root, tmp_path, route, public=public)
        assert status == 200 and body, route
    status, body = _ask(root, tmp_path, "/api/frames-pieces", public=public)
    pieces = json.loads(body)
    assert pieces["ok"] is True, pieces
    status, body = _ask(root, tmp_path, pieces["pieces"][0]["url"], public=public)
    assert status == 200 and body


@pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])
@pytest.mark.parametrize("relative", ["simulation/production.dcd",
                                      "simulation/trajectory_topology.pdb"])
def test_a_trajectory_or_topology_linked_out_is_not_played(tmp_path, relative, public):
    root = _study(tmp_path)
    outside = _moved_out(root, tmp_path, relative)
    (info, *sent, pieces), opened = _asked(
        root, tmp_path, ["/api/frames-info", *SENT, "/api/frames-pieces"], outside,
        public=public)
    assert opened == []
    said = json.loads(info[1])
    assert info[0] == 200 and said["playback_available"] is False, said
    assert relative in said["unavailable"]
    assert f"{relative} was not read" in said["not_read"]
    assert str(tmp_path) not in info[1].decode("utf-8")
    # The frames written from it before it was linked out are not sent.
    assert [status for status, _ in sent] == [404] * len(SENT)
    assert all(str(tmp_path).encode() not in body for _, body in sent)
    assert json.loads(pieces[1])["ok"] is False


@pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])
def test_a_topology_the_join_names_outside_is_not_read(tmp_path, public):
    root = _study(tmp_path)
    joined = root / "joined"
    joined.mkdir()
    shutil.copyfile(root / "simulation" / "production.dcd", joined / "production.dcd")
    elsewhere = tmp_path / "outside" / "topology.pdb"
    elsewhere.parent.mkdir(parents=True)
    shutil.copyfile(root / "simulation" / "trajectory_topology.pdb", elsewhere)
    (joined / "joined.json").write_text(json.dumps({"topology": str(elsewhere)}),
                                        encoding="utf-8")
    (info, frames), opened = _asked(root, tmp_path, ["/api/frames-info", SENT[0]],
                                    elsewhere.parent, public=public)
    assert opened == []
    said = json.loads(info[1])
    assert said["playback_available"] is False, said
    assert "was not read" in said["not_read"]
    assert str(tmp_path) not in info[1].decode("utf-8")
    assert frames[0] == 404


@pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])
@pytest.mark.parametrize("relative", ["simulation/frames.dcd",
                                      "simulation/frames_topology.pdb"])
def test_the_browser_s_frames_linked_out_are_not_sent(tmp_path, relative, public):
    """Frames or their topology linked out are not read where they lead,
    nor written over: not played, and said not read."""
    root = _study(tmp_path)
    outside = _moved_out(root, tmp_path, relative)
    marker = b"OUTSIDE THE STUDY " * 64
    (outside / relative).write_bytes(marker)
    (info, *sent), opened = _asked(root, tmp_path, ["/api/frames-info", *SENT], outside,
                                   public=public)
    assert opened == []
    said = json.loads(info[1])
    assert said["playback_available"] is False, said
    assert f"{relative} was not read" in said["not_read"]
    assert [status for status, _ in sent] == [404] * len(SENT)
    assert all(marker[:18] not in body for _, body in sent)
    assert (outside / relative).read_bytes() == marker
    assert (root / relative).is_symlink()


@pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])
def test_frames_superposed_linked_out_are_refused_before_they_are_opened(
        tmp_path, public, monkeypatch):
    """The frames superposed, linked out of a study with a box, are not
    stat'ed or read for their box where they lead, for the frames played
    or for an exact frame fitted the same way."""
    from fastmdxplora.gui import trajectory_frames

    root = _study(tmp_path, boxed=True)
    assert trajectory_frames.superposed_frames(root, "backbone")["ok"]
    relative = "simulation/frames_superposed_backbone.dcd"
    outside = _moved_out(root, tmp_path, relative)
    looked: list[str] = []
    real = trajectory_frames._has_a_cell
    monkeypatch.setattr(trajectory_frames, "_has_a_cell",
                        lambda path: looked.append(str(path)) or real(path))
    info = trajectory_frames.frames_info(root)
    signature = quote(info["source_signature"], safe="")
    routes = [SENT[2], f"/structure/frames.dcd?exact_frame=4&signature={signature}&on=backbone",
              f"/api/exact-frame?frame=4&signature={signature}&on=backbone"]
    (fitted, exact, _), opened = _asked(root, tmp_path, routes, outside, public=public)
    assert opened == [] and looked == []
    assert fitted[0] == 404 and exact[0] == 404
    assert (outside / relative).stat().st_size > 0
