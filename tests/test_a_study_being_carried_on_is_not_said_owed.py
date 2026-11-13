"""A study a resume is carrying on is said running, never owed; an analysis
the person chose is not taken for an owed one.

While `fastmdx resume` joined a study's pieces and ran their analyses and
report, the GUI read the marker the join leaves until the report has run,
and the live record's "completed": it said the study stopped, "Its analysis
is owed", and offered `fastmdx resume`, which was refused as still going
beside the sidebar's "running". The study is said going while a resume or
an extension holds its lock.

A joined study with no marker (written by a version before it) whose
analysis was of another trajectory was owed whatever that trajectory was:
one the person chose (`fastmdx analyze --rerun --trajectory`) was said
"interrupted", and its analysis was replaced by resume's with nothing left
of it. The person's choice is in the study's config, written after the join.

The records are those of a real tri-alanine study extended once and killed
after its join (`/tmp/K3h/Z` and `/tmp/K3h/Tp` in the review).
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.test_a_study_owing_its_analysis_is_said_so import _said, _study

_HOLD = """
import sys
from pathlib import Path
from fastmdxplora.simulation.resume import _carrying_on
with _carrying_on(Path(sys.argv[1]).resolve()):
    print("held", flush=True)
    sys.stdin.read()
"""


@pytest.fixture
def held(tmp_path: Path):
    """The study's carrying-on lock, held by another process as a resume
    holds it through its join, analyses and report."""
    root = _study(tmp_path, marker=True)
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(
        [str(Path(__file__).resolve().parents[1] / "src"), os.environ.get("PYTHONPATH", "")])}
    process = subprocess.Popen([sys.executable, "-c", _HOLD, str(root)], env=env,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == "held"
        yield root, process
    finally:
        if process.poll() is None:
            process.stdin.close()
            process.wait(timeout=60)


def test_while_a_resume_holds_it_the_study_is_said_running(held) -> None:
    from fastmdxplora.remedies import _interrupted, remedies_of
    from fastmdxplora.simulation.resume import resume_study

    root, process = held
    status, health, state = _said(root)
    assert (status["status"], state) == ("running", "running"), (status, state)
    assert status.get("ended_by") is None
    assert health["state"] == "ok" and "owed" not in health["headline"]
    # The fix offered never contradicts it: none, and the resume it would
    # have offered is refused as still going, with no fix saying it stopped.
    assert _interrupted(root) is None
    answer = resume_study(root)
    assert (answer["ok"], answer["did"]) == (False, "nothing")
    assert not [r for r in remedies_of(root) if "stopped" in r.why.lower()]

    # Its lock let go with the study still owing its analysis: owed again.
    process.stdin.close()
    process.wait(timeout=60)
    status, health, state = _said(root)
    assert (status["status"], state) == ("interrupted", "interrupted")
    assert health["headline"] == "Its analysis is owed"


def test_the_owed_study_is_said_by_its_name_and_its_fix_by_what_it_does(
        tmp_path: Path) -> None:
    from fastmdxplora.remedies import _interrupted

    root = _study(tmp_path, marker=True)
    _, health, _ = _said(root)
    # `/api/status` answers beyond loopback: no path on this computer.
    assert str(root) not in health["message"], health["message"]
    assert f"({root.name})" in health["message"]
    remedy = _interrupted(root)
    assert remedy is not None and remedy.argv == ("resume", str(root))
    # Nothing is carried on from a checkpoint; the card names its subject
    # ("The study:") once.
    assert remedy.fix == "Run its analyses and report over the joined pieces."
    assert not remedy.why.startswith("The study")
    # There may be none of before the join to show.
    assert "any shown" in health["message"]


def test_a_piece_stopped_is_said_by_the_study_s_name(tmp_path: Path) -> None:
    from fastmdxplora.gui.telemetry import _stopped_as_one_run

    said = _stopped_as_one_run(tmp_path / "Zs", {})
    assert str(tmp_path) not in said and "(Zs)" in said


def _chosen(root: Path, trajectory: Path, *, after_the_join: bool = True) -> None:
    """The study's config names ``trajectory`` as its analysis's, and its
    analysis was begun after (or before) its join, by the times its records
    hold; the files' own times say the other way, as a copy leaves them."""
    config = root / "resolved_config.yml"
    config.write_text(config.read_text(encoding="utf-8")
                      + f"analysis:\n  trajectory: {trajectory}\n", encoding="utf-8")
    joined, manifest = root / "joined" / "joined.json", root / "analysis" / "analysis_manifest.json"
    study = root / "manifest.json"
    began, at = ("2026-11-01T10:05:00+00:00", "2026-11-01T10:00:00+00:00")
    if not after_the_join:
        began, at = at, began
    record = json.loads(study.read_text(encoding="utf-8"))
    for phase in record["phases"]:
        if phase["name"] == "analysis":
            phase["started_at"] = began
    study.write_text(json.dumps(record), encoding="utf-8")
    record = json.loads(joined.read_text(encoding="utf-8"))
    joined.write_text(json.dumps({**record, "joined_at": at}), encoding="utf-8")
    later, earlier = (joined, manifest) if after_the_join else (manifest, joined)
    now = time.time()
    os.utime(earlier, (now - 60, now - 60))
    os.utime(later, (now, now))


def test_an_analysis_the_person_chose_is_not_owed(tmp_path: Path) -> None:
    from fastmdxplora.simulation.resume import analysis_owed

    # Its first piece alone, chosen after the join.
    root = _study(tmp_path / "first", marker=False, analysed="simulation", frames=30)
    _chosen(root, root / "simulation" / "production.dcd")
    assert not analysis_owed(root)
    status, health, state = _said(root)
    assert (status["status"], state) == ("completed", "completed")

    # Another study's join, read whole, chosen after the join.
    other = _study(tmp_path / "other", marker=False, analysed="simulation", frames=30)
    root = _study(tmp_path / "own", marker=False, frames=12)
    manifest = root / "analysis" / "analysis_manifest.json"
    record = json.loads(manifest.read_text(encoding="utf-8"))
    record["trajectory_input"] = str(other / "joined" / "production.dcd")
    manifest.write_text(json.dumps(record), encoding="utf-8")
    _chosen(root, other / "joined" / "production.dcd")
    assert not analysis_owed(root)

    # The first run's own config names its first piece; the join came after,
    # and a version that wrote no marker was stopped before its analysis.
    root = _study(tmp_path / "older", marker=False, analysed="simulation", frames=30)
    _chosen(root, root / "simulation" / "production.dcd", after_the_join=False)
    assert analysis_owed(root)
    # A config naming the join chose nothing; a join read short is owed.
    root = _study(tmp_path / "join", marker=False, frames=30)
    _chosen(root, root / "joined" / "production.dcd")
    assert analysis_owed(root)


def test_a_cache_cleared_by_another_thread_is_read_again(tmp_path: Path,
                                                         monkeypatch) -> None:
    from fastmdxplora.simulation import resume

    class Cleared(dict):
        """As a clear by another of the GUI's threads leaves it between a
        test of the key and its read."""

        def __contains__(self, key: object) -> bool:
            return True

        def __getitem__(self, key: object) -> bool:
            raise KeyError(key)

    monkeypatch.setattr(resume, "_NOT_OF_THE_JOIN", Cleared())
    root = _study(tmp_path, marker=False, analysed="simulation", frames=30)
    assert resume.analysis_owed(root)


def test_a_fixed_step_is_known_without_reading_the_pieces_clocks(
        tmp_path: Path, monkeypatch) -> None:
    from fastmdxplora.gui.simulated_time import simulated_times
    from fastmdxplora.simulation import lengths

    calls: list[Path] = []
    monkeypatch.setattr(lengths, "recorded_stage_lengths",
                        lambda root: calls.append(root) or {})
    # A first piece stopped before it wrote its record: its config says.
    for integrator, read in (("langevin_middle", 0), ("variable_langevin", 1)):
        root = tmp_path / integrator
        root.mkdir()
        (root / "resolved_config.yml").write_text(
            f"simulation:\n  integrator: {integrator}\n", encoding="utf-8")
        simulated_times(root, {"status": "stopped", "stage": "production"})
        assert len(calls) == read, integrator


_HOLD_AS = """
import fcntl, json, os, sys
from fastmdxplora.orchestrator import this_machine
path = sys.argv[1]
handle = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
record = {"pid": os.getpid(), **this_machine()}
if sys.argv[2] != "here":
    record = {"pid": 4242, "host": sys.argv[2], "boot": "another boot"}
os.write(handle, json.dumps(record).encode())
print("held", flush=True)
sys.stdin.read()
"""


@contextlib.contextmanager
def _held_as(path: Path, host: str):
    """``path`` locked by another process, as a resume holds the study's
    lock, its record naming ``host`` ("here": this machine)."""
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(
        [str(Path(__file__).resolve().parents[1] / "src"), os.environ.get("PYTHONPATH", "")])}
    process = subprocess.Popen([sys.executable, "-c", _HOLD_AS, str(path), host], env=env,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == "held"
        yield process
    finally:
        process.stdin.close()
        process.wait(timeout=60)


@pytest.mark.skipif(os.name == "nt", reason="a lock is tried by flock")
def test_a_resume_on_another_machine_of_a_shared_file_system_is_seen(tmp_path: Path) -> None:
    from fastmdxplora.remedies import _interrupted
    from fastmdxplora.simulation.resume import CARRYING_ON_FILE, being_carried_on

    # A compute node carries the study on; the GUI reads it from another
    # machine over the same file system. Its process cannot be asked, its
    # lock can.
    root = _study(tmp_path, marker=True)
    with _held_as(root / CARRYING_ON_FILE, "compute-node-7"):
        assert being_carried_on(root)
        status, health, state = _said(root)
        assert (status["status"], state) == ("running", "running"), (status, state)
        assert "owed" not in health["headline"]
        assert _interrupted(root) is None
    # Let go, or a record from another machine with no lock held: owed.
    assert not being_carried_on(root)
    assert _said(root)[0]["status"] == "interrupted"


@pytest.mark.skipif(os.name == "nt", reason="a lock is tried by flock")
def test_a_resume_starting_as_the_gui_tries_the_lock_is_not_refused(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import fcntl
    import threading

    from fastmdxplora import runs_here
    from fastmdxplora.simulation.resume import CARRYING_ON_FILE, _carrying_on

    root = _study(tmp_path, marker=True)
    lock = root / CARRYING_ON_FILE
    lock.write_text("{}", encoding="utf-8")
    taken, refused = threading.Event(), threading.Event()
    letting_go, let_go = threading.Event(), threading.Event()
    tried = runs_here._lock

    def trying(path: Path) -> int | None:
        try:
            return tried(path)
        except runs_here.StartRefused:
            refused.set()
            # The next try comes once the probe has let go, however slow
            # this host is to run it.
            let_go.wait(10)
            raise

    monkeypatch.setattr(runs_here, "_lock", trying)

    def probe() -> None:
        # As `_lock_held` does: the lock taken shared for an instant, here
        # until the resume has found it taken once.
        handle = os.open(lock, os.O_RDONLY)
        try:
            fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
            taken.set()
            refused.wait(10)
        finally:
            # Set before the lock is let go: once the resume holds it, this
            # is set, whenever this thread runs again.
            letting_go.set()
            os.close(handle)
            let_go.set()

    thread = threading.Thread(target=probe)
    thread.start()
    try:
        assert taken.wait(10)
        # Refused once in that instant ("A run of this study is still
        # going" before it was tried again), then taken once the probe let go.
        with _carrying_on(root):
            assert refused.is_set()
            assert letting_go.is_set()
            assert let_go.is_set()
            # Held by the resume now: the GUI's probe finds it taken.
            other = os.open(lock, os.O_RDONLY)
            try:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(other, fcntl.LOCK_SH | fcntl.LOCK_NB)
            finally:
                os.close(other)
    finally:
        thread.join()


@pytest.mark.skipif(os.name == "nt", reason="a lock is tried by flock")
def test_a_lock_file_linked_out_of_the_study_is_not_read(tmp_path: Path) -> None:
    from fastmdxplora.record_access import confined_records
    from fastmdxplora.simulation.resume import CARRYING_ON_FILE, being_carried_on

    root = _study(tmp_path, marker=True)
    outside = tmp_path / "elsewhere.lock"
    (root / CARRYING_ON_FILE).symlink_to(outside)
    with _held_as(outside, "here"):
        # Read as the GUI reads a study's records: a link at the lock's name
        # is no lock of the study's, not opened, and not a record refused
        # (All studies said "not known" while its header said Completed).
        with confined_records(root, lambda path: True) as scope:
            assert not being_carried_on(root)
        assert CARRYING_ON_FILE not in scope.denied
        # Nor opened outside a held read (a link to a FIFO hung the CLI).
        assert not being_carried_on(root)


def test_the_study_s_lock_is_read_by_the_gui_s_own_rule(held) -> None:
    from fastmdxplora.gui.problems_view import admitted_for_problems
    from fastmdxplora.record_access import confined_records
    from fastmdxplora.simulation.resume import CARRYING_ON_FILE, being_carried_on

    root, _process = held
    # Read as the GUI's status reads it: the lock was refused, so a study a
    # resume holds read as not held, and every study a resume had carried on
    # said "Not read: .fastmdxplora-carrying-on" on its Overview.
    with confined_records(root.resolve(), admitted_for_problems(root.resolve())) as scope:
        assert being_carried_on(root)
    assert CARRYING_ON_FILE not in scope.denied
