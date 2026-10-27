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


def test_the_file_creation_mask_is_read_without_changing_it(tmp_path):
    """Read from the system's own record of it where there is one: setting
    it to read it changed it for every thread for a moment. In a process of
    its own, every change of the mask counted from before the package is
    imported to after a record is saved."""
    import subprocess
    import sys

    import fastmdxplora

    if not Path("/proc/self/status").is_file():
        pytest.skip("no /proc on this system")
    script = (
        "import os, sys\n"
        "from pathlib import Path\n"
        "real, set_to = os.umask, []\n"
        "os.umask = lambda mask: (set_to.append(mask), real(mask))[1]\n"
        "from fastmdxplora import record_access\n"
        "record_access.save(Path(sys.argv[1]), b'{}')\n"
        "print(len(set_to), oct(os.stat(sys.argv[1]).st_mode & 0o777))\n")
    source = Path(fastmdxplora.__file__).resolve().parent.parent
    done = subprocess.run([sys.executable, "-c", script, str(tmp_path / "record.json")],
                          capture_output=True, text=True, timeout=120,
                          env={**os.environ, "PYTHONPATH": str(source)})
    mask = os.umask(0o022)
    os.umask(mask)
    assert done.returncode == 0, done.stderr
    assert done.stdout.split() == ["0", oct(0o666 & ~mask)], done.stdout


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


def _ready_to_run_again(tmp_path: Path, monkeypatch) -> Path:
    """A study that can be run again, its start stood in for."""
    from fastmdxplora.gui import exploration

    monkeypatch.setattr(exploration.subprocess, "Popen", _Started)
    monkeypatch.setattr(exploration.DashboardRuntime, "_others_running", lambda self, **_: None)
    _Started.started = []
    root = _with_its_structure(tmp_path)
    (root / "resolved_config.yml").write_text("analysis:\n  include: [rmsf]\n",
                                              encoding="utf-8")
    (root / "report").mkdir(exist_ok=True)
    (root / "report" / "report.md").write_text("# Report\n", encoding="utf-8")
    return root


def _asked_again(root: Path, tmp_path: Path) -> tuple[dict, list[dict]]:
    from tests.test_every_route_reads_only_the_study import _asked

    offered = json.loads(_asked(root, tmp_path, "/api/again")[1])
    asked = [json.loads(_asked(root, tmp_path, route, body=body)[1])
             for route, body in STARTS.items()]
    return offered, asked


#: What a run again reads or writes in a study, each linked out of it: the
#: folders its phases write, where `--rerun` sets aside what it replaces,
#: the study's manifest, its file log and process record, an analysis's
#: record the report reads, and the trajectory the analysis reads.
RUN_THROUGH = ("simulation", "analysis", "report", "previous", "manifest.json",
               "fastmdxplora.log", ".fastmdxplora_run.json", "analysis/rmsf/rmsf.dat",
               "simulation/production.dcd")


@pytest.mark.parametrize("relative", RUN_THROUGH)
def test_no_run_is_started_in_a_study_holding_a_link_out(tmp_path, monkeypatch, relative):
    """The run's own process follows what is in the study's folder, as a run
    started at the terminal does, so the GUI starts none where a link
    leads out of it: neither phase is offered, each says the link and the
    command that runs it at the terminal, and asked anyway, nothing is
    started; nothing outside changes."""
    root = _ready_to_run_again(tmp_path, monkeypatch)
    outside = tmp_path / "outside"
    target = outside / relative
    target.parent.mkdir(parents=True)
    if (root / relative).exists():
        (root / relative).rename(target)
    elif relative == "previous":
        (target / "analysis").mkdir(parents=True)
        (target / "analysis" / "notes.txt").write_text("the person's own\n", encoding="utf-8")
    else:
        target.write_text("the person's own\n", encoding="utf-8")
    _link(root / relative, target)
    before = _tree(outside)

    offered, asked = _asked_again(root, tmp_path)

    named = relative + ("/" if target.is_dir() else "")
    # The command the GUI would start, quoted: the study has a report, so
    # its analysis run again writes the report again too.
    for phase, command in (
            ("analysis", f"fastmdx explore --output {root} --include-phase analysis report "
                         "--rerun"),
            ("report", f"fastmdx report --output {root} --rerun")):
        assert offered[phase]["can"] is False, offered
        why = offered[phase]["why"]
        assert f"{named} is a link out of the study: the run would read or write through " \
               "it" in why, why
        assert offered[phase]["terminal"] == command and why.endswith(command), why
    assert all(said["ok"] is False for said in asked) and _Started.started == [], asked
    assert _tree(outside) == before


def test_no_run_is_started_in_a_study_of_several_with_a_run_linked_out(tmp_path, monkeypatch):
    root = _ready_to_run_again(tmp_path, monkeypatch)
    (root / "batch_manifest.json").write_text(json.dumps({"runs": []}), encoding="utf-8")
    (root / "runs").mkdir()
    for name in ("r1", "r2"):
        import shutil

        shutil.copytree(root / "simulation", root / "runs" / name / "simulation")
        (root / "runs" / name / "manifest.json").write_text("{}", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "runs" / "r2").rename(outside / "r2")
    _link(root / "runs" / "r2", outside / "r2")
    before = _tree(outside)

    offered, asked = _asked_again(root, tmp_path)

    assert offered["analysis"]["can"] is False and offered["report"]["can"] is False, offered
    assert "runs/r2/ is a link out of the study" in offered["analysis"]["why"], offered
    assert all(said["ok"] is False for said in asked) and _Started.started == [], asked
    assert _tree(outside) == before


def test_a_study_whose_links_stay_inside_it_is_run_again(tmp_path, monkeypatch):
    """A link that leads to another place in the study is not one out of
    it: the study is offered, and run, as before."""
    root = _ready_to_run_again(tmp_path, monkeypatch)
    (root / "notes").mkdir()
    _link(root / "notes" / "the_trajectory.dcd", root / "simulation" / "production.dcd")
    _link(root / "report" / "latest.md", root / "report" / "report.md")

    offered, asked = _asked_again(root, tmp_path)

    assert offered["analysis"]["can"] and offered["report"]["can"], offered
    assert all(said["ok"] for said in asked) and len(_Started.started) == 2, asked


def test_a_dangling_link_out_is_refused(tmp_path, monkeypatch):
    root = _ready_to_run_again(tmp_path, monkeypatch)
    _link(root / "previous", tmp_path / "nowhere" / "previous")

    offered, asked = _asked_again(root, tmp_path)

    assert offered["analysis"]["can"] is False, offered
    assert "previous is a link out of the study" in offered["analysis"]["why"], offered
    assert all(said["ok"] is False for said in asked) and _Started.started == [], asked
    assert not (tmp_path / "nowhere").exists()


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


#: What the Viewer computes from the frames it plays.
COMPUTED = ("/api/frames-superposed?on=backbone", "/api/motion", "/api/backbone-angles",
            "/api/chain-contacts", "/api/contact-map", "/api/contact-pair?a=0&b=3",
            "/api/pocket-volume?ligand=LIG",
            "/api/secondary-structure?of=frames",
            "/api/viewer-atoms?of=frames&expression=protein", "/api/beside?path=elsewhere",
            "/api/state-difference?a=0&b=3", "/api/occupancy?of=ligand&ligand=LIG")


@pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])
@pytest.mark.parametrize("route", sorted(COMPUTED))
def test_what_is_computed_from_the_frames_is_given_with_their_index_linked_out(
        tmp_path, route, public):
    """The frames' index linked out of the study is not read and not saved
    over: the frames are the study's own, so what the Viewer computes from
    them is given, the index said not saved (G14 M1); never that the frames
    are not the study's own."""
    if public and "beside" in route:
        pytest.skip("not answered beyond loopback")
    root = _study(tmp_path)
    if "beside" in route:
        route = route.replace("elsewhere", str(_study(tmp_path / "elsewhere")))
    victim = _outside(tmp_path, "to a file")
    index = root / "simulation" / "frames_index.json"
    victim.write_text(index.read_text(encoding="utf-8"), encoding="utf-8")
    mine = victim.read_text(encoding="utf-8")
    index.unlink()
    _link(index, victim)

    status, body = _ask(root, tmp_path, route, public=public)

    said = json.loads(body)
    assert status == 200 and "not this study's own records" not in body.decode("utf-8"), said
    assert said.get("ok") is not False, said
    assert "simulation/frames_index.json was not saved" in said["not_read"], said
    assert victim.read_text(encoding="utf-8") == mine and index.is_symlink()


# --------------------------------------------------------------------------
# A writer's own error, through each route that saves (G13 L2, L4)
# --------------------------------------------------------------------------

#: Each route that saves what it computes or is given, and its body if a POST.
WRITERS = {
    "/api/frames-superposed?on=backbone": None,
    "/structure/frames.dcd?superposed=backbone": None,
    "/structure/frames.dcd?span=0:5:1&between=1": None,
    "/structure/frames.dcd?as=xtc": None,
    "/api/frames-pieces": None,
    "/api/occupancy?of=ligand&ligand=LIG": None,
    "/api/backbone-angles": None,
    "/api/views": {"action": "save", "name": "one", "view": {"camera": {
        "position": [1.0, 2.0, 30.0], "target": [1.0, 2.0, 3.0], "up": [0.0, 1.0, 0.0]}}},
    "/api/viewer-selections": {"action": "save", "name": "one",
                               "selection": {"kind": "expression", "expression": "protein"}},
}


@pytest.mark.parametrize("route", sorted(WRITERS))
def test_a_writer_s_own_error_is_logged_and_said_without_its_text(tmp_path, monkeypatch,
                                                                  caplog, route):
    """A writer failing with an error of its own (not the system's, and with
    room on the disk) is said as not saved, without the Python text of its
    error, and logged on the server; nothing is written, and the server
    does not fail."""
    import logging

    from fastmdxplora import record_access
    from tests.test_every_route_reads_only_the_study import _asked

    root = _study(tmp_path)

    def broken(name, target):
        raise RuntimeError("MARKER-WRITER-TEXT")

    monkeypatch.setattr(record_access, "_into_place", broken)
    monkeypatch.setattr(record_access, "_little_room", lambda target: False)
    before = _tree(root)
    caplog.set_level(logging.WARNING, logger="fastmdxplora.record_access")

    status, body = _asked(root, tmp_path, route, body=WRITERS[route])

    text = body.decode("utf-8", "replace")
    assert status != 500 and "MARKER-WRITER-TEXT" not in text, text[:300]
    if route.startswith("/api/"):
        said = json.loads(body)
        assert said["ok"] is False and "could not be saved: its writer failed" in json.dumps(
            said), said
    assert any("MARKER-WRITER-TEXT" in (record.exc_text or "") or (
        record.exc_info and "MARKER-WRITER-TEXT" in str(record.exc_info[1]))
        for record in caplog.records), caplog.records
    assert {name: data for name, data in _tree(root).items() if name in before} == before
    assert not [p for p in root.rglob(".*.tmp")]


def test_the_study_beside_says_a_record_not_saved_on_a_full_disk(tmp_path, monkeypatch):
    from tests.test_every_route_reads_only_the_study import _asked
    from tests.test_every_route_reads_only_the_study import _study as written

    root = written(tmp_path)
    other = written(tmp_path / "other")
    # This study's frames to be written again, on a disk with no room.
    for name in ("frames.dcd", "frames_topology.pdb", "frames_index.json"):
        (root / "simulation" / name).unlink()
    _no_room(monkeypatch)

    status, body = _asked(root, tmp_path, f"/api/beside?path={other}")

    said = json.loads(body)
    assert status == 200 and said["ok"] is False, said
    assert "could not be saved: no room" in said["reason"], said


def _index_linked_out(root: Path, tmp_path: Path) -> Path:
    victim = _outside(tmp_path, "to a file")
    index = root / "simulation" / "frames_index.json"
    victim.write_text(index.read_text(encoding="utf-8"), encoding="utf-8")
    index.unlink()
    _link(index, victim)
    return victim


@pytest.fixture(scope="module")
def two_states(tmp_path_factory) -> Path:
    """Trypsin in two states, the cluster analysis run over them."""
    from tests.test_the_states_a_study_visited import _two_states

    return _two_states(tmp_path_factory.mktemp("states") / "study")


@pytest.fixture(scope="module")
def water_sites(tmp_path_factory) -> Path:
    """Trypsin with its crystal waters, the water sites analysis run."""
    from tests.test_where_the_ligand_and_water_go import trypsin

    return trypsin.__wrapped__(tmp_path_factory)


#: The routes whose readers are `states.py` and `occupancy.py`, each with
#: the study it reads.
BY_STATES = {"/api/states": "two_states", "/api/state-difference?a=2&b=25": "two_states",
             "/api/contact-map?first=0&second=1": "two_states",
             "/api/occupancy?of=water&ligand=BEN": "water_sites",
             "/api/water-sites": "water_sites"}


@pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])
@pytest.mark.parametrize("route", sorted(BY_STATES))
def test_the_states_and_water_are_given_with_the_frames_index_linked_out(
        tmp_path, request, route, public):
    """The states, two of them compared, the contact map of two states, where
    the water went and its sites: given with the frames' index linked out of
    the study, which is said not saved, and the file outside unchanged."""
    import shutil

    root = tmp_path / "study"
    shutil.copytree(request.getfixturevalue(BY_STATES[route]), root, symlinks=True)
    status, body = _ask(root, tmp_path, route, public=public)
    assert status == 200 and json.loads(body)["ok"], body[:300]
    victim = _index_linked_out(root, tmp_path)
    mine = victim.read_text(encoding="utf-8")

    status, body = _ask(root, tmp_path, route, public=public)

    said = json.loads(body)
    assert status == 200 and said["ok"], said
    assert "simulation/frames_index.json was not saved" in said["not_read"], said
    assert victim.read_text(encoding="utf-8") == mine
    assert (root / "simulation" / "frames_index.json").is_symlink()


@pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])
def test_the_frames_secondary_structure_is_of_the_frames_played_with_their_index_linked_out(
        tmp_path, public):
    """The frames' DSSP is applied in the Viewer only where it was computed
    for the frames it plays: with their index linked out, it carries the
    signature `/api/frames-info` gives, the index remembered (G15 M1)."""
    root = _study(tmp_path)
    _index_linked_out(root, tmp_path)

    played = json.loads(_ask(root, tmp_path, "/api/frames-info", public=public)[1])
    dssp = json.loads(_ask(root, tmp_path, "/api/secondary-structure?of=frames",
                           public=public)[1])

    assert played["source_signature"] and dssp["signature"] == played["source_signature"], dssp


@pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])
def test_an_atom_picked_in_the_frames_is_its_own_with_their_index_linked_out(
        tmp_path, public):
    """An atom picked in the frames played is the topology's atom the index
    says it is, with the index linked out: chain B's second alanine, though
    the request names no chain and both chains have one (G15 M1)."""
    from tests.test_every_route_reads_only_the_study import _study as analysed

    root = analysed(tmp_path)
    _index_linked_out(root, tmp_path)
    # Chain A's four alanines of five atoms each, then chain B's: its second
    # residue's alpha carbon is atom 26 of the frames.
    route = "/api/selection?chain=&resseq=2&resname=ALA&atom=CA&frames_atom=26"
    # The frames played, as the Viewer asks for them before an atom is picked.
    assert json.loads(_ask(root, tmp_path, "/api/frames-info",
                           public=public)[1])["playback_available"]

    said = json.loads(_ask(root, tmp_path, route, public=public)[1])

    assert said["ok"] and said["residue"]["selection"] == "chainid 1 and resSeq 2", said
    assert "simulation/frames_index.json was not saved" in said["not_read"], said


# --------------------------------------------------------------------------
# A fix started from the GUI, a hard link, a study in a folder named runs
# (G17 H1 and M1, G18 M1)
# --------------------------------------------------------------------------

def _stopped(root: Path) -> None:
    """The study stopped in its run: the Problems panel offers to resume it."""
    (root / "simulation" / "live_status.json").write_text(json.dumps(
        {"status": "stopped", "stage": "production", "current_step": 10,
         "total_planned_steps": 100}), encoding="utf-8")


@pytest.mark.parametrize("relative", ["fastmdxplora.log", "previous", "manifest.json", None])
def test_no_fix_is_started_in_a_study_holding_a_link_out(tmp_path, monkeypatch, relative):
    """`POST /api/fix` refuses as Analyze again does: with a link out of the
    study it starts nothing, names the link and gives the fix's own command
    to run at the terminal; nothing outside changes. A study whose links
    stay inside is still fixed."""
    from tests.test_every_route_reads_only_the_study import _asked

    root = _ready_to_run_again(tmp_path, monkeypatch)
    _stopped(root)
    outside = tmp_path / "outside"
    outside.mkdir()
    if relative is None:
        _link(root / "notes.log", root / "report" / "report.md")
    elif relative == "previous":
        (outside / "previous" / "analysis").mkdir(parents=True)
        (outside / "previous" / "analysis" / "notes.txt").write_text("mine\n", encoding="utf-8")
        _link(root / "previous", outside / "previous")
    else:
        target = outside / relative
        if (root / relative).exists():
            (root / relative).rename(target)
        else:
            target.write_text("the person's own\n", encoding="utf-8")
        _link(root / relative, target)
    before = _tree(outside)

    said = json.loads(_asked(root, tmp_path, "/api/fix", body={"index": 0})[1])

    assert _tree(outside) == before
    if relative is None:
        assert said["ok"] and len(_Started.started) == 1, said
        return
    assert said["ok"] is False and _Started.started == [], said
    assert f"{relative}{'/' if relative == 'previous' else ''} is a link out of the study" \
        in said["error"], said
    if relative == "manifest.json":
        # The fixes are read held to the study, its manifest refused there:
        # no fix is known from its own records, so no command is given.
        assert said["terminal"] is None and said["error"].endswith(
            "so it is not run from here."), said
        return
    assert said["terminal"] == f"fastmdx resume {root}" and said["error"].endswith(
        said["terminal"]), said


@pytest.mark.parametrize("record", ["manifest.json", "fastmdxplora.log", "resolved_config.yml",
                                    "members.json", "comparison/comparison_summary.csv",
                                    None])
def test_no_run_is_started_where_a_record_it_writes_in_place_is_a_hard_link(
        tmp_path, monkeypatch, record):
    """A study copied with hard links (`cp -al`), or one file of it linked
    so by a person: a run rewrites some records by name, in place (its
    Config at each start and end, the table of a study's members and its
    comparison), so into the other copy too. Any file of the study that is
    a hard link refuses, not a list of names. Neither phase is offered,
    nor is the fix, and nothing outside changes. A study with none is
    offered as before (G19 H2)."""
    import os

    from tests.test_every_route_reads_only_the_study import _asked

    root = _ready_to_run_again(tmp_path, monkeypatch)
    _stopped(root)
    outside = tmp_path / "outside"
    outside.mkdir()
    if record is not None:
        (root / record).parent.mkdir(parents=True, exist_ok=True)
        if not (root / record).exists():
            (root / record).write_text("the person's own\n", encoding="utf-8")
        os.link(root / record, outside / Path(record).name)
    before = _tree(outside)

    offered, asked = _asked_again(root, tmp_path)
    fixed = json.loads(_asked(root, tmp_path, "/api/fix", body={"index": 0})[1])

    if record is None:
        assert offered["analysis"]["can"] and offered["report"]["can"], offered
        assert all(said["ok"] for said in [*asked, fixed]), (asked, fixed)
        return
    assert offered["analysis"]["can"] is False and offered["report"]["can"] is False, offered
    assert f"{record} is a hard link: the run could write into another copy of it" \
        in offered["analysis"]["why"], offered
    assert all(said["ok"] is False for said in [*asked, fixed]), (asked, fixed)
    assert _Started.started == [] and _tree(outside) == before


def test_a_folder_that_cannot_be_listed_is_named_not_passed_over(tmp_path, monkeypatch):
    """What the run would read or write in a folder the walk cannot list is
    not known: the folder is named, and no run is offered (G17 L3)."""
    import os

    from fastmdxplora.gui import server

    root = _with_its_structure(tmp_path)
    scandir = os.scandir

    def closed(path):
        if Path(path).name == "analysis":
            raise PermissionError(13, "Permission denied", str(path))
        return scandir(path)

    monkeypatch.setattr(server.os, "scandir", closed)

    found = server._links_out(root)
    said = server._again_answer(root)

    assert found == [("analysis/", "unlisted")], found
    assert said["analysis"]["can"] is False and "analysis/ cannot be listed" in said["refused"]


def test_a_study_kept_in_a_folder_named_runs_is_run_again_as_itself(tmp_path, monkeypatch):
    """A person's project folder, its studies in its own `runs/` beside its
    notes and a link to data elsewhere: the study is not one of a study of
    several (no batch manifest), so the project's link is not its."""
    import shutil

    root = _ready_to_run_again(tmp_path / "made", monkeypatch)
    project = tmp_path / "project"
    (project / "runs").mkdir(parents=True)
    study = project / "runs" / "s1"
    shutil.move(str(root), str(study))
    (project / "analysis").mkdir()
    (project / "analysis" / "notes.md").write_text("mine\n", encoding="utf-8")
    (tmp_path / "data").mkdir()
    _link(project / "data", tmp_path / "data")
    del root

    offered, asked = _asked_again(study, tmp_path)

    assert offered["analysis"]["can"] and offered["report"]["can"], offered
    assert all(said["ok"] for said in asked), asked


@pytest.mark.parametrize("asked", ["DO: analyze again rmsd", "DO: write the report again"])
def test_the_agent_offers_no_run_in_a_study_holding_a_link_out(tmp_path, monkeypatch, asked):
    """The Agent's `DO: analyze again` and `DO: write the report again`
    say the refusal and the command, with nothing to say Yes to, where the
    page refuses (G18 L3)."""
    import fastmdxplora.agent as agent_mod
    from fastmdxplora.gui.agent_panel import propose_endpoint
    from tests.test_the_agent_analyses_a_study_again import _analysed, _runtime

    root = _analysed(tmp_path / "study")
    (tmp_path / "outside").mkdir()
    _link(root / "previous", tmp_path / "outside")
    monkeypatch.setattr(agent_mod, "completion_for", lambda *a, **k: (lambda prompt: asked))

    answer = propose_endpoint({"request": "again"}, _runtime(root))

    assert answer["fix"] is None, answer
    assert "previous/ is a link out of the study" in answer["refused"], answer
    # The command the GUI would have started, the analyses named in it.
    if asked.endswith("report again"):
        command = f"fastmdx report --output {root} --rerun"
    elif (root / "report").is_dir():
        command = (f"fastmdx explore --output {root} --include-phase analysis report --rerun "
                   "--analyze-analyses rmsd")
    else:
        command = f"fastmdx analyze --output {root} --rerun --analyses rmsd"
    assert answer["refused"].endswith("To run it anyway, at the terminal: " + command), answer


#: Paths opened while a request is answered, each as the file it reaches.
_OPENED: list[str] = []


def _audited(event: str, args: tuple) -> None:
    if event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
        try:
            _OPENED.append(os.path.realpath(os.fsdecode(args[0])))
        except (TypeError, ValueError):
            pass


_HOOKED: list[bool] = []


def _opened_while(ask) -> tuple[object, list[str]]:
    """What ``ask()`` answers, and every file opened meanwhile, followed to
    where it leads: a file opened through a link by the link's own name
    is said as the file outside (`sys.addaudithook`, added once)."""
    import sys

    if not _HOOKED:
        sys.addaudithook(_audited)
        _HOOKED.append(True)
    _OPENED.clear()
    answer = ask()
    opened, _OPENED[:] = list(_OPENED), []
    return answer, opened


@pytest.mark.parametrize("body", [{"index": 0}, {"windows": [42]}, {"windows": [3]}],
                         ids=["fix", "no such window", "a window"])
@pytest.mark.parametrize("record", ["manifest.json", "batch_manifest.json"])
def test_a_fix_asked_for_reads_nothing_through_a_link_out(tmp_path, monkeypatch, record, body):
    """`POST /api/fix` walks the study before it reads a record of it: with
    its manifest or batch manifest linked out, nothing outside is opened,
    nothing of it is said (an outside batch manifest planning seven
    windows was said as "numbered 0 to 6"), nothing starts, and the link
    is named (G19 H1)."""
    from tests.test_every_route_reads_only_the_study import _asked

    root = _ready_to_run_again(tmp_path, monkeypatch)
    _stopped(root)
    outside = tmp_path / "outside"
    outside.mkdir()
    if record == "manifest.json":
        (outside / record).write_text(json.dumps({"phases": [
            {"name": "simulation", "status": "failed", "error": "MARKER-SECRET"}]}),
            encoding="utf-8")
    else:
        planned = [{"run_id": f"w{at}", "system": "x", "options": {"simulation": {
            "umbrella": {"index": at, "collective_variable": "MARKER-SECRET"}}}}
            for at in range(7)]
        (outside / record).write_text(json.dumps({"planned": planned, "runs": []}),
                                      encoding="utf-8")
    (root / record).unlink(missing_ok=True)
    _link(root / record, outside / record)
    before = _tree(outside)

    (status, text), opened = _opened_while(
        lambda: _asked(root, tmp_path, "/api/fix", body=dict(body, force_constant=5)))

    said = json.loads(text)
    reached = [path for path in opened if path.startswith(str(outside.resolve()))]
    assert reached == [], reached
    assert said["ok"] is False and _Started.started == [], said
    assert f"{record} is a link out of the study" in said["error"], said
    assert "MARKER" not in text.decode() and "0 to 6" not in text.decode(), said
    assert _tree(outside) == before


@pytest.mark.parametrize("route", sorted(STARTS))
def test_a_run_again_asked_for_opens_nothing_through_a_link_out(tmp_path, monkeypatch, route):
    """`POST /api/again` and `/api/report/write` walk the study before they
    read a record of it, held or not: with its analysis manifest linked
    out, nothing outside is opened, even by the name of the link, what
    would run is not read even held, and nothing starts (G19 H1)."""
    from fastmdxplora.gui import server
    from tests.test_every_route_reads_only_the_study import _asked

    held: list[object] = []
    confined = server._confined
    monkeypatch.setattr(server, "_confined", lambda root, read, *a, **k: (
        held.append(read), confined(root, read, *a, **k))[1])
    root = _ready_to_run_again(tmp_path, monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "analysis" / "analysis_manifest.json").rename(outside / "analysis_manifest.json")
    _link(root / "analysis" / "analysis_manifest.json", outside / "analysis_manifest.json")

    (status, text), opened = _opened_while(
        lambda: _asked(root, tmp_path, route, body=STARTS[route]))

    said = json.loads(text)
    reached = [path for path in opened if path.startswith(str(outside.resolve()))]
    assert reached == [] and server._again_offered not in held, (reached, held)
    assert said["ok"] is False and _Started.started == [], said
    assert "analysis/analysis_manifest.json is a link out of the study" in said["error"], said


def test_the_agent_offers_no_fix_in_a_study_holding_a_link_out(tmp_path, monkeypatch):
    """The Agent's `DO: run the fix` is walked first as the POST is: no fix
    is offered to say Yes to, and the refusal names the link."""
    import fastmdxplora.agent as agent_mod
    from fastmdxplora.gui.agent_panel import propose_endpoint
    from tests.test_the_agent_analyses_a_study_again import _runtime

    root = _ready_to_run_again(tmp_path, monkeypatch)
    _stopped(root)
    (tmp_path / "outside").mkdir()
    _link(root / "fastmdxplora.log", tmp_path / "outside" / "fastmdxplora.log")
    monkeypatch.setattr(agent_mod, "completion_for",
                        lambda *a, **k: (lambda prompt: "DO: run the fix"))

    answer = propose_endpoint({"request": "resume it"}, _runtime(root))

    assert answer["fix"] is None, answer
    assert "fastmdxplora.log is a link out of the study" in answer["refused"], answer


def test_no_study_is_continued_in_place_holding_a_link_out(tmp_path, monkeypatch):
    """A config carrying a study on (`simulation.resume_from`) runs in that
    study's folder: walked first, as a fix is, nothing is written for it
    and nothing starts."""
    from fastmdxplora.gui.exploration import DashboardRuntime

    root = _ready_to_run_again(tmp_path, monkeypatch)
    (tmp_path / "outside").mkdir()
    _link(root / "fastmdxplora.log", tmp_path / "outside" / "fastmdxplora.log")
    runtime = DashboardRuntime(workspace_root=tmp_path / "space",
                               exploration_root=tmp_path / "space")

    said = runtime.launch_from_config(None, config={
        "simulation": {"resume_from": str(root), "extra_ns": 0.01}})

    assert said["ok"] is False and _Started.started == [], said
    assert "fastmdxplora.log is a link out of the study" in said["error"], said
    assert not (tmp_path / "space" / "continuations").exists()


def _several_with_reports(tmp_path: Path, monkeypatch) -> Path:
    """A study of two runs, each with its trajectory and a report, its own
    top holding none."""
    import shutil

    root = _ready_to_run_again(tmp_path, monkeypatch)
    shutil.rmtree(root / "report")
    (root / "batch_manifest.json").write_text(json.dumps({"runs": []}), encoding="utf-8")
    for name in ("r1", "r2"):
        run = root / "runs" / name
        shutil.copytree(root / "simulation", run / "simulation")
        (run / "manifest.json").write_text("{}", encoding="utf-8")
        (run / "report").mkdir()
        (run / "report" / "report.md").write_text("# Report\n", encoding="utf-8")
    return root


@pytest.mark.parametrize("case", ["study of several", "analyses named"])
def test_the_command_given_is_the_run_the_gui_would_start(tmp_path, monkeypatch, case):
    """The command said to run at the terminal is `again.plan`'s: a study of
    several whose runs have reports, though its top has none, writes them
    again too, and the analyses asked for are named (G19 L1, G20 L3)."""
    import shlex

    from fastmdxplora import again
    from fastmdxplora.gui import server

    root = _several_with_reports(tmp_path, monkeypatch)
    analyses = ["rmsd"] if case == "analyses named" else None

    planned = again.plan(root, ["analysis"], analyses)
    said = server._terminal(root, ["analysis"], analyses)

    assert said == shlex.join(["fastmdx", *planned.command()]), said
    assert "--include-phase analysis report" in said and ("rmsd" in said) == bool(analyses)


def test_a_run_of_a_study_of_several_is_said_to_build_its_comparison_again(
        tmp_path, monkeypatch):
    """Its command builds the comparison of the study's runs again after it,
    as the run from here would, and the refusal says so."""
    root = _several_with_reports(tmp_path, monkeypatch)
    (tmp_path / "outside").mkdir()
    _link(root / "notes", tmp_path / "outside")
    from fastmdxplora.gui import server

    refused = server._again_refused(root / "runs" / "r1")

    assert refused["error"].endswith(
        f"It builds the comparison of {root.name}'s runs again after it, as the run from "
        f"here would. To run it anyway, at the terminal: {refused['terminal']}"), refused
