"""An exact frame is read from the study's own trajectory and nothing else.

A chart point opens the frame of the trajectory it came from, through
`/api/exact-frame`, `/structure/exact-frame.xtc` and
`/structure/frames.dcd?exact_frame=`. Each reads the study's trajectory, its
topology and the frames written beside them for the browser, held to the
study (`record_access`): one of them linked out of the study is not opened
where it leads, on this computer or on a public bind, and the JSON route says
it was not read. An audit hook on `open` sees every file Python opens while
a route is asked.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

import pytest

from tests.test_the_study_pages_read_only_the_study import _OPENED, _WATCHED, _ask, _link, _watch

pytest.importorskip("mdtraj")

#: The frame asked for, of the eleven the study's trajectory holds.
FRAME = 4


def _study(tmp_path: Path) -> tuple[Path, str]:
    """A finished study of eleven frames, its browser's frames written, and
    the signature a page names its trajectory by."""
    from fastmdxplora.gui.trajectory_frames import frames_info
    from tests.test_a_graph_opens_exact_source_frame import _study as written

    root = written(tmp_path / "study")
    info = frames_info(root)
    assert info["playback_available"] and info["source_kind"] == "production-dcd"
    return root, str(info["source_signature"])


def _routes(signature: str, on: str = "") -> list[str]:
    fitted = f"&on={on}" if on else ""
    said = quote(signature, safe="")
    return [f"/api/exact-frame?frame={FRAME}&signature={said}{fitted}",
            f"/structure/exact-frame.xtc?frame={FRAME}&signature={said}{fitted}",
            f"/structure/frames.dcd?exact_frame={FRAME}&signature={said}{fitted}"]


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
def test_the_study_s_own_frame_is_given(tmp_path, public):
    root, signature = _study(tmp_path)
    for on in ("", "backbone"):
        info, xtc, dcd = (_ask(root, tmp_path, route, public=public)
                          for route in _routes(signature, on))
        said = json.loads(info[1])
        assert info[0] == 200 and said["ok"] is True and said["frame"] == FRAME, said
        assert "unavailable" not in said
        assert xtc[0] == 200 and len(xtc[1]) > 0
        assert dcd[0] == 200 and len(dcd[1]) > 0


@pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])
@pytest.mark.parametrize("relative", ["simulation/production.dcd",
                                      "simulation/trajectory_topology.pdb"])
def test_a_trajectory_or_topology_linked_out_is_not_read(tmp_path, relative, public):
    root, signature = _study(tmp_path)
    outside = _moved_out(root, tmp_path, relative)
    for on in ("", "backbone"):
        (info, xtc, dcd), opened = _asked(root, tmp_path, _routes(signature, on), outside,
                                          public=public)
        assert opened == []
        said = json.loads(info[1])
        assert info[0] == 200 and said["ok"] is False, said
        assert relative in said["unavailable"]
        assert f"{relative} was not read" in said["not_read"]
        assert str(tmp_path) not in info[1].decode("utf-8")
        assert xtc[0] == 404 and dcd[0] == 404
        assert str(tmp_path).encode() not in xtc[1] + dcd[1]


@pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])
@pytest.mark.parametrize("relative", ["simulation/frames.dcd",
                                      "simulation/frames_topology.pdb"])
def test_the_browser_s_frames_linked_out_are_not_read(tmp_path, relative, public):
    """The frames written beside the trajectory for the browser, which the
    whole-DCD route sends and a fitted frame is fitted to."""
    root, signature = _study(tmp_path)
    outside = _moved_out(root, tmp_path, relative)
    routes = _routes(signature)[2:] + _routes(signature, "backbone")[1:]
    answers, opened = _asked(root, tmp_path, routes, outside, public=public)
    assert opened == []
    assert [status for status, _ in answers] == [404, 404, 404]
