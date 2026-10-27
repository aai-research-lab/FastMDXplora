"""No record of the study is saved through a link.

The Viewer saves what it computes from the frames beside them (the backbone
angles, the contact map, the contacts between chains, the pocket's room, a
motion), a scene of a view in `scenes/`, and the views and selections named
with the study. Each was written to a temporary file of a fixed name and
renamed into place, so a link planted at that name (in a study shared or
unpacked) had a public GET write over a file outside the study. Each is now
saved through a new temporary file of its own (`record_access.save`), and a
record or folder linked out of the study is not saved over or through, on
this computer or on a public bind.
"""

from __future__ import annotations

import json
import os
import stat
import threading
from pathlib import Path

import pytest

from tests.test_the_study_pages_read_only_the_study import _ask, _link
from tests.test_what_the_viewer_computes_from_the_frames_keeps_to_the_study import _study

pytest.importorskip("mdtraj")

#: Each route that saves what it computes, the record it saves, and the
#: temporary name it wrote that record through.
SAVED = {
    "/api/backbone-angles": ("simulation/backbone_angles.json",
                             "simulation/.backbone_angles.json.tmp"),
    "/api/contact-map": ("simulation/contact_map.json", "simulation/.contact_map.json.tmp"),
    "/api/chain-contacts": ("simulation/chain_contacts.json",
                            "simulation/.chain_contacts.json.tmp"),
    "/api/pocket-volume?ligand=LIG": ("simulation/pocket_volume_LIG_5.00.json",
                                      "simulation/.pocket_volume_LIG_5.00.json.tmp"),
    "/api/motion": ("simulation/motion_modes.npz", "simulation/.motion_modes.npz.tmp.npz"),
}
#: What the person's own file outside the study says.
MINE = "the person's own file\n"
#: A link planted to a file there, or to a name with nothing there.
PLANTED = ("to a file", "dangling")


def _outside(tmp_path: Path, planted: str) -> Path:
    """The folder outside the study, and the file a link leads to in it:
    there, or not."""
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "victim.txt"
    if planted == "to a file":
        victim.write_text(MINE, encoding="utf-8")
    return victim


def _unchanged(victim: Path, planted: str) -> None:
    """Nothing written outside the study: the file there as it was, and
    nothing made beside it."""
    if planted == "to a file":
        assert victim.read_text(encoding="utf-8") == MINE
        assert [p.name for p in victim.parent.iterdir()] == ["victim.txt"]
    else:
        assert list(victim.parent.iterdir()) == []


def _regular(path: Path) -> bool:
    return stat.S_ISREG(os.lstat(path).st_mode)


@pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])
@pytest.mark.parametrize("planted", PLANTED)
@pytest.mark.parametrize("route", sorted(SAVED))
def test_a_temporary_name_planted_as_a_link_is_not_written_through(tmp_path, route, planted,
                                                                    public):
    root = _study(tmp_path)
    record, temporary = SAVED[route]
    victim = _outside(tmp_path, planted)
    _link(root / temporary, victim)

    status, body = _ask(root, tmp_path, route, public=public)

    said = json.loads(body)
    assert status == 200 and said["ok"], said
    _unchanged(victim, planted)
    assert _regular(root / record)
    # The link planted is left as it was, and no temporary file of the
    # route's own is left beside the record.
    assert (root / temporary).is_symlink()
    left = [p.name for p in (root / temporary).parent.iterdir() if p.name.endswith(".tmp")]
    assert left == ([Path(temporary).name] if temporary.endswith(".tmp") else [])


@pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])
@pytest.mark.parametrize("planted", PLANTED)
@pytest.mark.parametrize("route", sorted(SAVED))
def test_a_record_linked_out_is_not_saved_over(tmp_path, route, planted, public):
    root = _study(tmp_path)
    record, _ = SAVED[route]
    victim = _outside(tmp_path, planted)
    _link(root / record, victim)

    status, body = _ask(root, tmp_path, route, public=public)

    said = json.loads(body)
    assert status == 200 and said["ok"] is False, said
    assert f"{record} was not read" in said["not_read"], said
    _unchanged(victim, planted)
    assert (root / record).is_symlink()


def _with_its_structure(tmp_path: Path) -> Path:
    """The study, its system as simulated beside it for the Viewer."""
    from tests.test_every_route_reads_only_the_study import _study as written

    return written(tmp_path)


def _scene(root: Path, tmp_path: Path, name: str) -> dict:
    from tests.test_every_route_reads_only_the_study import _asked

    status, body = _asked(root, tmp_path, "/api/scenes", body={"name": name, "view": {
        "camera": {"position": [0, 0, 50], "target": [0, 0, 0], "up": [0, 1, 0]}}})
    assert status == 200, body[:300]
    return json.loads(body)


@pytest.mark.parametrize("planted", PLANTED)
def test_a_scene_is_not_written_through_a_temporary_name_planted(tmp_path, planted):
    root = _with_its_structure(tmp_path)
    victim = _outside(tmp_path, planted)
    _link(root / "scenes" / ".S1.mvsx.tmp", victim)

    said = _scene(root, tmp_path, "S1")

    assert said["ok"], said
    _unchanged(victim, planted)
    assert _regular(root / "scenes" / "S1.mvsx")


@pytest.mark.parametrize("planted", PLANTED)
def test_a_scene_linked_out_is_not_written_over(tmp_path, planted):
    root = _with_its_structure(tmp_path)
    victim = _outside(tmp_path, planted)
    _link(root / "scenes" / "S1.mvsx", victim)

    said = _scene(root, tmp_path, "S1")

    assert said["ok"] is False, said
    _unchanged(victim, planted)
    assert (root / "scenes" / "S1.mvsx").is_symlink()


def _views(root: Path) -> None:
    from fastmdxplora.gui.saved_views import _write

    _write(root, [])


def _selections(root: Path) -> None:
    from fastmdxplora.gui.viewer_selections import _write

    _write(root, [])


def _frames_topology(root: Path) -> None:
    from fastmdxplora.gui.trajectory_frames import _write_text

    _write_text(root / "simulation" / "frames_topology.pdb", "END\n")


def _frames(root: Path) -> None:
    import mdtraj as md

    from fastmdxplora.gui.trajectory_frames import _write_dcd

    simulation = root / "simulation"
    frames = md.load_dcd(str(simulation / "production.dcd"),
                         top=str(simulation / "trajectory_topology.pdb"))
    _write_dcd(frames, simulation / "frames.dcd")


#: The writers whose temporary name was the process's and the thread's
#: numbers, and the record each saves.
BY_THREAD = {"views": (_views, "viewer_views.json"),
             "selections": (_selections, "viewer_selections.json"),
             "frames' topology": (_frames_topology, "simulation/frames_topology.pdb"),
             "frames": (_frames, "simulation/frames.dcd")}


@pytest.mark.parametrize("planted", PLANTED)
@pytest.mark.parametrize("writer", sorted(BY_THREAD))
def test_a_writer_named_by_its_thread_writes_through_no_link(tmp_path, writer, planted):
    """The name was hard to guess from outside, not impossible: a link at
    it is now never followed either."""
    root = _study(tmp_path)
    write, record = BY_THREAD[writer]
    target = root / record
    victim = _outside(tmp_path, planted)
    _link(target.with_name(f".{target.name}.{os.getpid()}.{threading.get_ident()}.tmp"),
          victim)

    write(root)

    _unchanged(victim, planted)
    assert _regular(target)


# --------------------------------------------------------------------------
# The saving itself (`record_access.save`)
# --------------------------------------------------------------------------

def _held(root: Path):
    from fastmdxplora.gui.server import _public_records
    from fastmdxplora.record_access import confined_records

    return confined_records(root, _public_records(root.resolve()))


@pytest.mark.parametrize("planted", PLANTED)
def test_a_folder_linked_out_is_not_saved_into_in_a_held_read(tmp_path, planted):
    from fastmdxplora import record_access
    from fastmdxplora.refusals import OutsideWorkspace

    root = tmp_path / "study"
    root.mkdir()
    victim = _outside(tmp_path, planted)
    _link(root / "simulation", victim.parent)

    with _held(root) as scope, pytest.raises(OutsideWorkspace):
        record_access.save(root / "simulation" / "victim.txt", "written\n")

    _unchanged(victim, planted)
    assert "simulation" in scope.denied


@pytest.mark.parametrize("planted", PLANTED)
def test_a_record_linked_out_is_not_saved_over_in_a_held_read(tmp_path, planted):
    from fastmdxplora import record_access
    from fastmdxplora.refusals import OutsideWorkspace

    root = tmp_path / "study"
    root.mkdir()
    victim = _outside(tmp_path, planted)
    _link(root / "record.json", victim)

    with _held(root) as scope, pytest.raises(OutsideWorkspace):
        record_access.save(root / "record.json", "written\n")

    _unchanged(victim, planted)
    assert (root / "record.json").is_symlink() and "record.json" in scope.denied
    assert [p.name for p in root.iterdir()] == ["record.json"]


def test_a_link_saved_over_outside_a_held_read_is_replaced_not_followed(tmp_path):
    from fastmdxplora import record_access

    victim = _outside(tmp_path, "to a file")
    target = tmp_path / "record.json"
    _link(target, victim)

    record_access.save(target, "written\n")

    _unchanged(victim, "to a file")
    assert _regular(target) and target.read_text(encoding="utf-8") == "written\n"


def test_a_record_is_saved_as_any_file_written_here_would_be(tmp_path):
    """Not `mkstemp`'s owner-only mode: a lab's shared study is read by the
    group, and a failed write leaves no temporary file."""
    from fastmdxplora import record_access

    mask = os.umask(0o022)
    os.umask(mask)
    target = tmp_path / "record.json"
    record_access.save(target, b"{}")
    assert stat.S_IMODE(os.lstat(target).st_mode) == 0o666 & ~mask

    def broken(stream) -> None:
        stream.write(b"half")
        raise RuntimeError("the writer failed")

    with pytest.raises(RuntimeError):
        record_access.save(target, broken)
    assert target.read_bytes() == b"{}"
    assert [p.name for p in tmp_path.iterdir()] == ["record.json"]
