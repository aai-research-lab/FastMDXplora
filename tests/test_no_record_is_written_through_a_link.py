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
@pytest.mark.parametrize("at", ["temporary name", "record"])
@pytest.mark.parametrize("route", sorted(SAVED))
def test_a_link_planted_where_a_route_saves_is_not_written_through(tmp_path, route, at,
                                                                   planted, public):
    """At the temporary name the record was written through, the route saves
    the record all the same, and the link is left as it was; at the record
    itself, it is refused as read (`_frames_refused`) before any save."""
    root = _study(tmp_path)
    record, temporary = SAVED[route]
    victim = _outside(tmp_path, planted)
    _link(root / (temporary if at == "temporary name" else record), victim)

    status, body = _ask(root, tmp_path, route, public=public)

    said = json.loads(body)
    _unchanged(victim, planted)
    if at == "record":
        assert status == 200 and said["ok"] is False, said
        assert f"{record} was not read" in said["not_read"], said
        assert (root / record).is_symlink()
        return
    assert status == 200 and said["ok"], said
    assert _regular(root / record)
    # The link planted is left as it was, and no temporary file of the
    # route's own is left beside the record.
    assert (root / temporary).is_symlink()
    left = [p.name for p in (root / temporary).parent.iterdir() if p.name.endswith(".tmp")]
    assert left == ([Path(temporary).name] if temporary.endswith(".tmp") else [])


def _stamp(path: Path) -> tuple[int, int]:
    info = os.lstat(path)
    return info.st_ino, info.st_mtime_ns


@pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])
@pytest.mark.parametrize("planted", PLANTED)
def test_a_frames_index_linked_out_is_said_not_saved_and_the_frames_still_play(
        tmp_path, planted, public):
    """The index of the frames written for the Viewer, linked out of the
    study: not read, not saved over, and said not saved; the frames are
    played all the same, and not written again on the next request."""
    root = _study(tmp_path)
    victim = _outside(tmp_path, planted)
    index = root / "simulation" / "frames_index.json"
    index.unlink()
    _link(index, victim)

    status, body = _ask(root, tmp_path, "/api/frames-info", public=public)
    said = json.loads(body)
    assert status == 200 and said["playback_available"], said
    assert "simulation/frames_index.json was not saved" in said["not_read"], said
    frames = _stamp(root / "simulation" / "frames.dcd")
    status, body = _ask(root, tmp_path, "/api/frames-info", public=public)
    said = json.loads(body)
    assert status == 200 and said["playback_available"], said
    assert "simulation/frames_index.json was not saved" in said["not_read"], said

    _unchanged(victim, planted)
    assert index.is_symlink()
    assert _stamp(root / "simulation" / "frames.dcd") == frames
    status, _ = _ask(root, tmp_path, "/structure/frames.dcd", public=public)
    assert status == 200


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
@pytest.mark.parametrize("at", [".S1.mvsx.tmp", "S1.mvsx"])
def test_a_scene_is_not_written_through_a_link_planted(tmp_path, at, planted):
    root = _with_its_structure(tmp_path)
    victim = _outside(tmp_path, planted)
    _link(root / "scenes" / at, victim)

    said = _scene(root, tmp_path, "S1")

    _unchanged(victim, planted)
    if at == "S1.mvsx":
        assert said["ok"] is False and (root / "scenes" / "S1.mvsx").is_symlink(), said
        return
    assert said["ok"], said
    assert _regular(root / "scenes" / "S1.mvsx")


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
    # Said as not saved, not as a record not read.
    assert scope.unsaved == {"simulation/victim.txt": "it leads outside the study"}
    assert scope.denied == set()


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
    assert (root / "record.json").is_symlink() and scope.denied == set()
    assert scope.unsaved == {"record.json": "it leads outside the study"}
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

    with pytest.raises(OSError):
        record_access.save(target, broken)
    assert target.read_bytes() == b"{}"
    assert [p.name for p in tmp_path.iterdir()] == ["record.json"]


def test_the_file_creation_mask_is_read_without_changing_it(monkeypatch):
    """Read from the system's own record of it where there is one: setting
    it to read it changed it for every thread for a moment."""
    from fastmdxplora import record_access

    if not Path("/proc/self/status").is_file():
        pytest.skip("no /proc on this system")
    mask = os.umask(0o022)
    os.umask(mask)

    def changed(_):
        raise AssertionError("the mask was set to be read")

    monkeypatch.setattr(record_access, "_UMASK", None)
    monkeypatch.setattr(os, "umask", changed)
    assert record_access._umask() == mask


# --------------------------------------------------------------------------
# A writer given a name (MDTraj's), and a save that fails
# --------------------------------------------------------------------------

@pytest.mark.parametrize("fails", [False, True], ids=["saves", "fails partway"])
def test_a_writer_given_a_name_writes_in_a_folder_of_its_own(tmp_path, fails):
    """MDTraj opens the name it is given again, without ``O_EXCL``: the
    name is in a new folder only this user can enter, removed after, and a
    writer that fails partway leaves the record as it was."""
    from fastmdxplora import record_access

    target = tmp_path / "frames.dcd"
    target.write_bytes(b"BEFORE")
    given = []

    def write(name: str) -> None:
        given.append(Path(name))
        assert Path(name).parent != tmp_path
        assert stat.S_IMODE(os.lstat(Path(name).parent).st_mode) == 0o700
        Path(name).write_bytes(b"AFTER")
        if fails:
            raise TypeError("'dict' object is not callable")

    if fails:
        with pytest.raises(OSError):
            record_access.save_by_name(target, write)
    else:
        record_access.save_by_name(target, write)

    assert given and given[0].name == "frames.dcd"
    assert target.read_bytes() == (b"BEFORE" if fails else b"AFTER")
    assert [p.name for p in tmp_path.iterdir()] == ["frames.dcd"]


def test_frames_written_as_xtc_are_written_in_a_folder_of_their_own(tmp_path, monkeypatch):
    import mdtraj as md

    from fastmdxplora.gui.trajectory_frames import _write_xtc

    root = _study(tmp_path)
    simulation = root / "simulation"
    frames = md.load_dcd(str(simulation / "frames.dcd"),
                         top=str(simulation / "frames_topology.pdb"))
    given = []
    save_xtc = md.Trajectory.save_xtc
    monkeypatch.setattr(md.Trajectory, "save_xtc", lambda self, name, **kw: (
        given.append(Path(name)), save_xtc(self, name, **kw))[1])

    _write_xtc(frames, simulation / "piece_0.xtc")

    assert given[0].parent.parent == simulation and given[0].parent.name.startswith(".")
    assert not given[0].parent.exists()
    assert md.load_xtc(str(simulation / "piece_0.xtc"),
                       top=str(simulation / "frames_topology.pdb")).n_frames == frames.n_frames


def _no_room(monkeypatch) -> None:
    """A full disk, as a writer sees it: the rename into place refused, and
    as little room left as MDTraj's own errors are said for."""
    import errno

    from fastmdxplora import record_access

    def full(name, target):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(record_access, "_into_place", full)
    monkeypatch.setattr(record_access, "_little_room", lambda target: True, raising=False)


@pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])
def test_a_record_that_cannot_be_saved_is_said_so(tmp_path, monkeypatch, public):
    root = _study(tmp_path)
    _no_room(monkeypatch)

    status, body = _ask(root, tmp_path, "/api/backbone-angles", public=public)

    said = json.loads(body)
    assert status == 200 and said["ok"] is False, said
    assert said["reason"] == "simulation/backbone_angles.json could not be saved: no room.", said
    assert not list((root / "simulation").glob(".*.tmp"))


@pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])
def test_mdtraj_s_own_error_on_a_full_disk_is_said_not_saved(tmp_path, monkeypatch, public):
    """MDTraj reports a short write of a DCD in its own words (a
    `TypeError`): said as not saved, not an error of the server."""
    import mdtraj as md

    root = _study(tmp_path)
    monkeypatch.setattr("fastmdxplora.record_access._little_room", lambda target: True,
                        raising=False)

    def short(self, name, **kw):
        Path(name).write_bytes(b"half")
        raise TypeError("'dict' object is not callable")

    monkeypatch.setattr(md.Trajectory, "save_dcd", short)

    status, body = _ask(root, tmp_path, "/api/frames-superposed?on=backbone", public=public)

    said = json.loads(body)
    assert status == 200 and said["ok"] is False, said
    assert "could not be saved: no room" in said["reason"], said
    assert [p for p in (root / "simulation").iterdir() if p.name.startswith(".")] == []


@pytest.mark.parametrize("planted", PLANTED)
def test_the_study_beside_is_not_saved_through_a_link(tmp_path, planted):
    """The other study's frames, fitted beside this one's, are saved under
    this study (`viewer_beside/`): one of them linked out is not saved over,
    and the answer says so."""
    import hashlib

    from tests.test_every_route_reads_only_the_study import _asked
    from tests.test_every_route_reads_only_the_study import _study as written

    root = written(tmp_path)
    other = written(tmp_path / "other")
    key = hashlib.sha1(str(other.resolve()).encode("utf-8")).hexdigest()[:16]
    victim = _outside(tmp_path, planted)
    _link(root / "viewer_beside" / f"{key}.dcd", victim)

    status, body = _asked(root, tmp_path, f"/api/beside?path={other}")

    said = json.loads(body)
    assert status == 200 and said["ok"] is False, said
    assert f"viewer_beside/{key}.dcd was not saved: it leads outside the study" in said["reason"]
    _unchanged(victim, planted)
    assert (root / "viewer_beside" / f"{key}.dcd").is_symlink()
