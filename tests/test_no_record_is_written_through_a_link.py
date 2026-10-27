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


def test_a_record_linked_out_is_replaced_not_followed_outside_a_held_read(tmp_path):
    """Where no study is known (the run's own writer), a record that is
    itself a link is replaced by the rename into place, never written
    through."""
    from fastmdxplora import record_access

    victim = _outside(tmp_path, "to a file")
    target = tmp_path / "record.json"
    _link(target, victim)

    record_access.save(target, "written\n")

    _unchanged(victim, "to a file")
    assert _regular(target) and target.read_text(encoding="utf-8") == "written\n"


def test_a_run_writes_its_progress_into_a_simulation_folder_it_was_given_as_a_link(tmp_path):
    """A study whose `simulation/` is a link the person made (to scratch on a
    cluster): the run's own process, outside any held read, writes its
    status, phases, events and metrics there, as it always did."""
    from fastmdxplora.gui.telemetry import TelemetryWriter

    root = tmp_path / "study"
    root.mkdir()
    scratch = tmp_path / "scratch" / "simulation"
    scratch.mkdir(parents=True)
    _link(root / "simulation", scratch)
    writer = TelemetryWriter(root / "simulation", total_steps=100)

    writer.write_status(stage="production", status="running", current_step=40)
    writer.event("production began")
    writer.append_metric(step=40)
    TelemetryWriter(root / "simulation", phases_only=True).write_status(
        stage="analysis", status="running", stage_states={"analysis": "running"})

    status = json.loads((scratch / "live_status.json").read_text(encoding="utf-8"))
    assert status["current_step"] == 40 and status["stage_states"]["analysis"] == "running"
    assert "production began" in (scratch / "live_events.log").read_text(encoding="utf-8")
    assert (scratch / "live_metrics.csv").read_text(encoding="utf-8").count("\n") == 2
    assert (root / "simulation").is_symlink()


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


# --------------------------------------------------------------------------
# The run's own records the GUI writes: its log, its status (G11 H1)
# --------------------------------------------------------------------------

class _Started:
    """The run's process, stood in for: nothing is run. It writes a line to
    the log it is given, as a run would."""

    started: list = []

    def __init__(self, command, *args, **kwargs):
        _Started.started.append(command)
        self.pid, self.args, self.returncode = 999999, command, None
        out = kwargs.get("stdout")
        if hasattr(out, "write"):
            out.write("a line the run would log\n")

    def poll(self):
        return None

    def wait(self, timeout=None):
        return 0


#: The POSTs that start a run in the study open, and what each is asked.
STARTS = {"/api/again": {"phases": ["analysis"]}, "/api/report/write": {}}


@pytest.mark.parametrize("planted", PLANTED)
@pytest.mark.parametrize("route", sorted(STARTS))
def test_a_run_s_log_linked_out_is_not_written_to(tmp_path, monkeypatch, route, planted):
    from fastmdxplora.gui import exploration
    from tests.test_every_route_reads_only_the_study import _asked

    monkeypatch.setattr(exploration.subprocess, "Popen", _Started)
    monkeypatch.setattr(exploration.DashboardRuntime, "_others_running", lambda self, **_: None)
    _Started.started = []
    root = _with_its_structure(tmp_path)
    (root / "resolved_config.yml").write_text("analysis:\n  include: [rmsf]\n",
                                              encoding="utf-8")
    (root / "report").mkdir(exist_ok=True)
    victim = _outside(tmp_path, planted)
    _link(root / "exploration.log", victim)

    status, body = _asked(root, tmp_path, route, body=STARTS[route])

    said = json.loads(body)
    assert status == 200 and said["ok"] is False and _Started.started == [], said
    assert "exploration.log" in said["error"], said
    _unchanged(victim, planted)
    assert (root / "exploration.log").is_symlink()


def _going(root: Path) -> Path:
    status = root / "simulation" / "live_status.json"
    status.write_text(json.dumps({"status": "running", "stage": "production"}),
                      encoding="utf-8")
    return status


@pytest.mark.parametrize("planted", PLANTED)
@pytest.mark.parametrize("at", ["live_status.json.tmp", "live_status.json"])
def test_a_stop_writes_the_run_s_status_through_no_link(tmp_path, at, planted):
    """A run stopped here is recorded as stopped in its own status record: a
    link at its old temporary name is never followed. The record itself
    linked out of the study is not read (what it says is never copied in,
    `test_a_status_linked_out_is_not_copied_into_the_study`), so nothing is
    recorded and the link is left as it was."""
    from fastmdxplora.gui.exploration import _record_the_stop

    root = _study(tmp_path)
    status = _going(root)
    victim = _outside(tmp_path, planted)
    if at == "live_status.json":
        if planted == "to a file":
            victim.write_text(status.read_text(encoding="utf-8"), encoding="utf-8")
            mine = victim.read_text(encoding="utf-8")
        status.unlink()
        _link(status, victim)
    else:
        _link(status.with_name(at), victim)

    _record_the_stop(root)

    if at == "live_status.json" and planted == "to a file":
        assert victim.read_text(encoding="utf-8") == mine
        assert [p.name for p in victim.parent.iterdir()] == ["victim.txt"]
    else:
        _unchanged(victim, planted)
    if at == "live_status.json":
        # Nothing read says it was going: nothing is recorded.
        assert status.is_symlink()
        return
    assert _regular(status)
    assert json.loads(status.read_text(encoding="utf-8"))["status"] == "stopped"


@pytest.mark.parametrize("planted", PLANTED)
@pytest.mark.parametrize("record", ["live_events.log", "live_metrics.csv"])
def test_a_run_s_events_and_metrics_are_added_through_no_link(tmp_path, record, planted):
    from fastmdxplora.gui.telemetry import TelemetryWriter

    root = _study(tmp_path)
    victim = _outside(tmp_path, planted)
    _link(root / "simulation" / record, victim)
    writer = TelemetryWriter(root / "simulation")

    if record.endswith(".log"):
        writer.event("stopped here")
    else:
        writer.append_metric(step=1)

    _unchanged(victim, planted)
    assert (root / "simulation" / record).is_symlink()


# --------------------------------------------------------------------------
# What a run's status linked out says is never copied in (G13 H1, G14 H1)
# --------------------------------------------------------------------------

#: A status outside the study that says it is going, with keys and values
#: the study never wrote.
MARKED = {"status": "running", "stage": "production", "platform": "MARKERPLATFORM",
          "latest_warning": "MARKER-OUTSIDE-STOP", "current_step": 777777,
          "secret_token": "MARKER-SECRET"}
MARKERS = ("MARKERPLATFORM", "MARKER-OUTSIDE-STOP", "777777", "MARKER-SECRET")


def _marked_status(root: Path, tmp_path: Path, stage: str = "production") -> Path:
    """The study's status record moved out of it, a link to it left in its
    place, and the file outside saying it is going, in ``stage``, with
    markers."""
    outside = tmp_path / "outside"
    outside.mkdir(exist_ok=True)
    target = outside / "live_status.json"
    target.write_text(json.dumps({**MARKED, "stage": stage}), encoding="utf-8")
    status = root / "simulation" / "live_status.json"
    status.unlink(missing_ok=True)
    _link(status, target)
    return target


def _tree(folder: Path) -> dict[str, bytes]:
    return {p.relative_to(folder).as_posix(): p.read_bytes()
            for p in sorted(folder.rglob("*")) if p.is_file() and not p.is_symlink()}


def _no_marker_in(root: Path, tmp_path: Path) -> None:
    """No marker in any record of the study, nor in what `/api/status`
    sends on this computer or on a public bind."""
    for name, data in _tree(root).items():
        assert not any(marker.encode() in data for marker in MARKERS), name
    for public in (False, True):
        status, body = _ask(root, tmp_path, "/api/status", public=public)
        assert status == 200 and not any(m in body.decode("utf-8") for m in MARKERS), body[:300]


def _run_status_writers():
    from fastmdxplora.gui.exploration import DashboardRuntime, _record_the_stop
    from fastmdxplora.gui.telemetry import TelemetryWriter

    def stop(root: Path) -> None:
        _record_the_stop(root)

    def failed_checks(root: Path) -> None:
        runtime = DashboardRuntime(workspace_root=root.parent, exploration_root=root.parent)
        runtime.active_root = root
        runtime._record_completion_failure("Its checks failed after the run.")

    def phases(root: Path) -> None:
        TelemetryWriter(root / "simulation", phases_only=True).write_status(
            stage="analysis", status="running", stage_states={"analysis": "running"})

    return {"stop": stop, "completion failure": failed_checks, "phase writer": phases}


@pytest.mark.parametrize("writer", ["stop", "completion failure", "phase writer"])
def test_a_status_linked_out_is_not_copied_into_the_study(tmp_path, writer):
    root = _study(tmp_path)
    target = _marked_status(root, tmp_path, "analysis" if writer == "phase writer"
                            else "production")
    before = target.read_bytes()

    _run_status_writers()[writer](root)

    assert target.read_bytes() == before
    assert [p.name for p in target.parent.iterdir()] == ["live_status.json"]
    _no_marker_in(root, tmp_path)


@pytest.mark.parametrize("writer", ["stop", "completion failure"])
def test_a_simulation_folder_linked_out_is_not_written_into(tmp_path, writer):
    """The run's whole `simulation/` linked out of the study, saying it is
    going: what the GUI records of it, held to the study, changes and makes
    nothing outside, and no marker reaches the study. (The run's own
    process writes there, as `test_a_run_writes_its_progress_into_...`
    says.)"""
    root = _study(tmp_path)
    (root / "simulation" / "live_status.json").write_text(json.dumps(MARKED), encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "simulation").rename(outside / "simulation")
    _link(root / "simulation", outside / "simulation")
    before = _tree(outside)

    _run_status_writers()[writer](root)

    assert _tree(outside) == before
    _no_marker_in(root, tmp_path)


@pytest.mark.parametrize("folder", ["simulation", "analysis", "report"])
def test_nothing_is_run_again_into_a_folder_linked_out(tmp_path, monkeypatch, folder):
    """Running a phase again writes into `simulation/` (its status),
    `analysis/` or `report/`: with one linked out of the study, neither
    phase is offered, and asked anyway, nothing is started."""
    from fastmdxplora.gui import exploration
    from tests.test_every_route_reads_only_the_study import _asked

    monkeypatch.setattr(exploration.subprocess, "Popen", _Started)
    monkeypatch.setattr(exploration.DashboardRuntime, "_others_running", lambda self, **_: None)
    _Started.started = []
    root = _with_its_structure(tmp_path)
    (root / "resolved_config.yml").write_text("analysis:\n  include: [rmsf]\n",
                                              encoding="utf-8")
    (root / "report").mkdir(exist_ok=True)
    (root / "report" / "report.md").write_text("# Report\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / folder).rename(outside / folder)
    _link(root / folder, outside / folder)
    before = _tree(outside)

    offered = json.loads(_asked(root, tmp_path, "/api/again")[1])
    asked = [json.loads(_asked(root, tmp_path, route, body=body)[1])
             for route, body in STARTS.items()]

    assert offered["analysis"]["can"] is False and offered["report"]["can"] is False, offered
    assert f"{folder} is a link out of the study" in offered["analysis"]["why"], offered
    assert all(said["ok"] is False for said in asked) and _Started.started == [], asked
    assert _tree(outside) == before


@pytest.mark.parametrize("planted", PLANTED)
def test_the_agent_s_stop_writes_to_no_log_linked_out(tmp_path, monkeypatch, planted):
    """A run ended after it was asked to stop says so in its log
    (`stop_after.watch`): never through a link out of the study. Nothing is
    signalled: the run and its ending are stood in for."""
    from fastmdxplora import stop_after
    from fastmdxplora.gui import exploration

    monkeypatch.setattr(exploration, "_identify_run", lambda *args: True)
    monkeypatch.setattr(exploration, "_terminate_process", lambda *args, **kwargs: None)
    monkeypatch.setattr(exploration, "_end_what_is_left_of", lambda *args: None)
    root = _study(tmp_path)
    victim = _outside(tmp_path, planted)
    _link(root / "exploration.log", victim)

    assert stop_after.watch(2 ** 30, root, {}, 0.0) is True

    _unchanged(victim, planted)
    assert (root / "exploration.log").is_symlink()
