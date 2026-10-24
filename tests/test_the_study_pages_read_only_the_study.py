"""The Overview and Analysis pages read only the study's own records.

A study folder can hold a link that leads out of it (a study shared as a
download, say). What the link leads to is never read for a page, on this
computer or on a public bind: the record is said to be unavailable instead,
and the pages keep what the study's own records say.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fastmdxplora.statistics import MEAN_RULES

#: Frames the study's analyses read.
N = 40
#: What the records outside the study say, which no page may show.
TEXT = "OUTSIDE-TEXT-not-for-any-page"
VALUE = -4242.42
#: How a page could say the value: as recorded, to four figures, with its error.
VALUE_SAID = ("-4242.4", "-4,242", "-4242 ")

#: A record outside the study, in the form the analyses write
#: (`findings.mean`, `statistics.mean_record`): one a mean was determined
#: of, and one whose mean was withheld for a reason.
OUTSIDE = {
    "determined": {"findings": {"mean": {
        "mean": VALUE, "standard_error": 4.2, "standard_deviation": 30.0,
        "effective_samples": 30.0, "discard": 4, "n_frames": N, "rules": MEAN_RULES,
        "unit": TEXT}}},
    "withheld": {"findings": {"mean": {
        "mean": VALUE, "standard_deviation": 30.0, "effective_samples": 3.0,
        "discard": 4, "n_frames": N, "rules": MEAN_RULES, "unit": TEXT,
        "not_a_measurement": TEXT}}},
}

#: The routes the Overview and the Analysis page read the study through.
ROUTES = ("/api/overview", "/api/results", "/api/series", "/api/series?analysis=rmsd",
          "/api/analysis-overview", "/api/convergence?analysis=rmsd", "/api/files-page")


def _write(root: Path, relative: str, value: object) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")
    return path


def _link(path: Path, target: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.symlink_to(target)
    except OSError:
        pytest.skip("this system cannot create symbolic links")


def _series(start: float) -> str:
    return "# whitespace-delimited\n" + "\n".join(
        f"{start + 0.01 * (i % 7):.6f}" for i in range(N)) + "\n"


def _study(tmp_path: Path) -> Path:
    """A study analysed for RMSD and Rg, each with its data file, Rg with
    its record, recorded on a 10 ps clock."""
    root = tmp_path / "study"
    _write(root, "manifest.json", {"phases": [{"name": "analysis", "status": "ok"}]})
    _write(root, "analysis/analysis_manifest.json", {
        "phase": "analysis", "n_frames": N,
        "load_kwargs": {"stride": None, "first": None, "saving_interval_ps": 10.0},
        "results": {"rmsd": {"status": "ok"}, "rg": {"status": "ok"}}})
    _write(root, "analysis/rmsd/rmsd.dat", _series(0.1))
    _write(root, "analysis/rg/rg.dat", _series(1.0))
    _write(root, "analysis/rg/options.json", {"analysis": "rg", "findings": {"mean": {
        "mean": 1.03, "standard_error": 0.004, "standard_deviation": 0.02,
        "effective_samples": 30.0, "discard": 4, "n_frames": N, "rules": MEAN_RULES,
        "unit": "nm"}}})
    return root


def _ask(root: Path, workspace: Path, route: str, *, public: bool) -> tuple[int, bytes]:
    """``route`` as a page asks it, on loopback or as a public bind
    (`--host 0.0.0.0`) answers it."""
    from fastmdxplora.gui.exploration import DashboardRuntime
    from tests.test_a_public_dashboard_answers_only_what_it_lists import _ask as ask
    from tests.test_a_public_dashboard_answers_only_what_it_lists import _serving

    runtime = DashboardRuntime(workspace_root=root, exploration_root=workspace,
                               active_root=root)
    with _serving(runtime, allow_control=not public) as url:
        return ask(url + route)


def _shows_nothing_from_outside(body: bytes) -> None:
    text = body.decode("utf-8")
    assert TEXT not in text
    for said in VALUE_SAID:
        assert said not in text, said

    def numbers(value: object):
        if isinstance(value, dict):
            for inner in value.values():
                yield from numbers(inner)
        elif isinstance(value, list):
            for inner in value:
                yield from numbers(inner)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            yield value

    assert all(abs(number - VALUE) > 1e-6 for number in numbers(json.loads(text)))


REFUSED = "analysis/rmsd/options.json was not read"


@pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])
@pytest.mark.parametrize("route,kind", [
    (route, kind) for route in ROUTES for kind in sorted(OUTSIDE)
    # The Files page gives a value only where it was determined.
    if not (route == "/api/files-page" and kind == "withheld")])
def test_a_record_linked_out_of_the_study_reaches_no_page(tmp_path, route, kind, public):
    root = _study(tmp_path)
    outside = _write(tmp_path, "elsewhere/options.json", OUTSIDE[kind])
    _link(root / "analysis/rmsd/options.json", outside)

    status, body = _ask(root, tmp_path, route, public=public)

    assert status == 200
    _shows_nothing_from_outside(body)
    answer = json.loads(body)
    # Said as not read, where the route says anything of the record.
    if route == "/api/overview":
        tiles = {tile["analysis"]: tile for tile in answer["tiles"]}
        assert tiles["rmsd"]["said"] == "record unavailable"
        assert REFUSED in tiles["rmsd"]["why"]
        assert tiles["rg"]["determined"] is True
        assert answer["unavailable"]
    elif route == "/api/results":
        rows = {row["metric"]: row for row in answer["metric_rows"]}
        assert rows["RMSD"]["average"] == "record unavailable"
        assert REFUSED in rows["RMSD"]["why"]
        assert answer["unavailable"]
    elif route == "/api/series":
        assert answer["unavailable"]
    elif route == "/api/analysis-overview":
        rows = {row["analysis"]: row for row in answer["rows"]}
        rmsd = rows["rmsd"]["quantities"][0]
        assert rows["rmsd"]["kind"] == "mean"
        assert rmsd["said"] == "record unavailable" and rmsd["determined"] is False
        assert REFUSED in rmsd["why"]
        assert rows["rg"]["mean"]["determined"] is True
    elif route == "/api/files-page":
        # The study's own value is still given beside its analysis.
        assert "1.0300 ± 0.0040 nm" in answer["html"]
    else:
        assert answer["ok"] is False
        assert REFUSED in answer["reason"]
        assert answer["unavailable"] == ["analysis/rmsd/options.json"]


#: The run's records the time of each analysed frame is read from: the
#: analysis manifest's clock and the loader's own times, and the run's
#: record and config, which say whether the integrator chose its own step.
CLOCKS = {
    "analysis_manifest": ("analysis/analysis_manifest.json", {
        "results": {"rmsd": {"status": "ok"}, "rg": {"status": "ok"}},
        "load_kwargs": {"saving_interval_ps": 1.0},
        "frame_times_ps": [987654.0 + i for i in range(N)]}),
    "simulation_parameters": ("simulation/simulation_parameters.json", {
        "parameters": {"integrator": "variable_langevin"}}),
    "resolved_config": ("resolved_config.yml", "simulation:\n  integrator: variable_langevin\n"),
}


@pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])
@pytest.mark.parametrize("record", sorted(CLOCKS))
@pytest.mark.parametrize("route", ["/api/analysis-overview", "/api/overview",
                                   "/api/series?analysis=rg"])
def test_a_clock_linked_out_of_the_study_times_no_frame(tmp_path, route, record, public):
    """The frames' times come from the study's own records: a record linked
    out of it neither gives them nor takes them away."""
    root = _study(tmp_path)
    relative, content = CLOCKS[record]
    if record == "analysis_manifest":
        (root / relative).unlink()
    outside = _write(tmp_path, "elsewhere/" + Path(relative).name, content)
    _link(root / relative, outside)

    status, body = _ask(root, tmp_path, route, public=public)

    assert status == 200
    text = body.decode("utf-8")
    assert "987654" not in text and "987.65" not in text
    assert "variable_langevin" not in text
    answer = json.loads(body)
    if route == "/api/series?analysis=rg":
        times, start = answer["x"], answer["mean"]["from_x"]
        said = answer["x_label"]
    else:
        rows = answer["rows"] if route == "/api/analysis-overview" else answer["tiles"]
        rg = next(row for row in rows if row["analysis"] == "rg")
        mean = rg["mean"] if route == "/api/analysis-overview" else rg
        start, times, said = mean["from_ns"], None, None
    if record == "analysis_manifest":
        # Its own manifest refused, the study records no clock: said by
        # frame, not by time.
        assert start in (None, 4.0)
        if said is not None:
            assert said == "Frame" and times[0] == 0.0
    else:
        # Frame 4 of a trajectory saved every 10 ps, as the study's own
        # manifest says, the outside record not read.
        assert start == pytest.approx(0.05)
        if said is not None:
            assert said == "Time (ns)" and times[0] == pytest.approx(0.01)


#: Every route the Overview and the Analysis page read the study through.
EVERY_ROUTE = ("/api/overview", "/api/results", "/api/series", "/api/analysis-overview",
               "/api/series?analysis=rmsd", "/api/series?analysis=rg",
               "/api/convergence?analysis=rmsd", "/api/convergence?analysis=rg")


def _a_study_carried_on(tmp_path: Path) -> Path:
    """A study run once and carried on in `segment-001`, analysed, with the
    records each reader behind the pages reads, as the run writes them: its
    manifests and resolved configs, its simulation's records and cost, the
    system it prepared, and the piece's record of the system it simulated."""
    import hashlib

    root = _study(tmp_path)
    _write(root, "analysis/rmsd/options.json", {"analysis": "rmsd", "findings": {"mean": {
        "mean": 0.13, "standard_error": 0.002, "standard_deviation": 0.02,
        "effective_samples": 30.0, "discard": 4, "n_frames": N, "rules": MEAN_RULES,
        "unit": "nm"}}})
    _write(root, "manifest.json", {"phases": [
        {"name": "setup", "status": "ok"}, {"name": "simulation", "status": "ok"},
        {"name": "analysis", "status": "ok"}]})
    _write(root, "resolved_config.yml",
           "systems: [{system: ala3.pdb}]\nsimulation:\n  integrator: langevin\n")
    for name in ("system.xml", "state.xml", "topology.pdb"):
        _write(root, f"setup/{name}", f"<{name}/>\n")
    _write(root, "setup/setup_parameters.json", {"phase": "setup", "n_atoms_solvated": 1403})
    done = {"status": "completed", "stage": "production", "current_step": 10000,
            "total_planned_steps": 10000, "last_update_timestamp": "2026-10-01T00:00:00+00:00"}
    for piece in ("", "segment-001/"):
        _write(root, f"{piece}simulation/live_status.json", done)
        _write(root, f"{piece}simulation/cost.json", {
            "particles": 1403, "steps": 10000, "seconds": 100.0, "platform": "CPU",
            "timestep_fs": 2.0})
        _write(root, f"{piece}simulation/checkpoint.chk.json", {
            "stage": "production", "step": 10000, "finished": True,
            "trajectory_interval_steps": 100})
        _write(root, f"{piece}simulation/production.dcd", "frames")
    _write(root, "simulation/simulation_parameters.json", {
        "phase": "simulation", "parameters": {"integrator": "langevin"}})
    _write(root, "segment-001/resolved_config.yml",
           f"systems: [{{system: ala3.pdb}}]\nsimulation:\n  setup_from: {root}\n")
    _write(root, "segment-001/manifest.json", {"phases": [
        {"name": "setup", "status": "skipped", "taken_from": str(root / "setup")},
        {"name": "simulation", "status": "ok"}]})
    digest = hashlib.sha256((root / "setup" / "system.xml").read_bytes()).hexdigest()
    _write(root, "segment-001/simulation/simulation_parameters.json", {
        "phase": "simulation", "parameters": {"integrator": "langevin", "setup_from": str(root)},
        "prepared_system": {"given": str(root), "resolved": str(root / "setup"),
                            "relative_to_run": "../setup", "system_xml_sha256": digest}})
    return root


def _every_record_linked_out(root: Path, tmp_path: Path, *, named: str = "") -> Path:
    """Each regular file of the study moved outside it, a link left in its
    place: what the link leads to is the study's own record, so a page that
    followed one would answer as before, and only its reads can tell.
    Given ``named``, only the files of that name."""
    outside = tmp_path / "outside"
    for path in sorted(root.rglob("*")):
        if named and path.name != named:
            continue
        if path.is_file() and not path.is_symlink():
            target = outside / path.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            path.rename(target)
            _link(path, target)
    return outside


#: Files and folders opened outside the study while a route is asked, as an
#: audit hook sees them (`sys.addaudithook`, installed once).
_OPENED: list[str] = []
_WATCHED: list[str] = []


def _watch(outside: Path) -> None:
    import os
    import sys

    if not _WATCHED:
        def hook(event: str, args: tuple) -> None:
            if not _WATCHED or event not in ("open", "os.listdir", "os.scandir") or not args:
                return
            name = args[0]
            if isinstance(name, (str, bytes, os.PathLike)):
                name = os.fsdecode(os.fspath(name))
                if os.path.isabs(name) and os.path.realpath(name).startswith(_WATCHED[0]):
                    _OPENED.append(name)

        sys.addaudithook(hook)
    _WATCHED[:] = [str(outside.resolve()) + "/"]
    _OPENED.clear()


@pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])
@pytest.mark.parametrize("route", EVERY_ROUTE)
def test_no_record_of_the_study_is_read_through_a_link_out(tmp_path, route, public):
    """Every record any reader behind these routes reads (an audit of the
    opens finds them: the run's cost, configs, manifests, the system it
    prepared and each piece's records), linked out of the study, is not
    opened where it leads."""
    root = _a_study_carried_on(tmp_path)
    outside = _every_record_linked_out(root, tmp_path)
    _watch(outside)
    try:
        status, body = _ask(root, tmp_path, route, public=public)
        opened = sorted({Path(name).relative_to(root).as_posix() if name.startswith(str(root))
                         else name for name in _OPENED})
    finally:
        _WATCHED.clear()
    assert status == 200
    assert opened == []
    if route in ("/api/overview", "/api/results"):
        assert json.loads(body)["unavailable"]


#: A state record of a piece, as OpenMM writes it, beside which a page
#: could say a mean temperature.
ENERGY = ('#"Step","Time (ps)","Potential Energy (kJ/mole)","Temperature (K)"\n'
          + "".join(f"{100 * i},{0.2 * i:.1f},-1000.0,300.0\n" for i in range(1, 11)))


def _a_study_joined(tmp_path: Path) -> Path:
    """A study carried on in `segment-001` and joined, with the join's
    record as `simulation.resume` writes it and each piece's state record."""
    root = _a_study_carried_on(tmp_path)
    _write(root, "simulation/trajectory_topology.pdb", "MODEL\nENDMDL\n")
    _write(root, "joined/production.dcd", "frames")
    _write(root, "joined/joined.json", {
        "trajectory": str(root / "joined" / "production.dcd"), "segments": [0, 1],
        "frames": 2 * N, "trimmed": {}, "joins": [N],
        "topology": str(root / "simulation" / "trajectory_topology.pdb"),
        "joined_from_segments": True, "ran_through": False})
    for piece in ("", "segment-001/"):
        _write(root, f"{piece}simulation/energy.csv", ENERGY)
    return root


@pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])
@pytest.mark.parametrize("route,named", [
    pytest.param(route, named, id=f"{route}-{'state-records' if named else 'every-record'}")
    for route in EVERY_ROUTE for named in ("", "energy.csv")
    # The pieces' state records are read for the Overview and Results only
    # (`state_record_pieces`); the other routes never read them.
    if not named or route in ("/api/overview", "/api/results")])
def test_a_joined_study_s_record_is_not_read_through_a_link_out(tmp_path, route, named,
                                                                public):
    """A study carried on and joined: its record of the join, the joined
    trajectory's topology and each piece's state record, linked out of the
    study, are not opened where they lead; nor, with every other record kept
    in the study, are the pieces' state records the join leads to."""
    root = _a_study_joined(tmp_path)
    outside = _every_record_linked_out(root, tmp_path, named=named)
    _watch(outside)
    try:
        status, body = _ask(root, tmp_path, route, public=public)
        opened = sorted({Path(name).relative_to(root).as_posix() if name.startswith(str(root))
                         else name for name in _OPENED})
    finally:
        _WATCHED.clear()
    assert status == 200
    assert opened == []
    if route in ("/api/overview", "/api/results"):
        assert json.loads(body)["unavailable"]


def test_a_joined_study_is_read_as_its_pieces_where_it_holds_them(tmp_path):
    """Outside a held read (the analysis phase, the command line), and inside
    one where the records are the study's own, a joined study is still its
    joined trajectory and its pieces' state records."""
    from fastmdxplora import record_access
    from fastmdxplora.analysis.analyze import study_trajectory
    from fastmdxplora.analysis.thermodynamics import state_record_pieces

    root = _a_study_joined(tmp_path)
    topology = root / "simulation" / "trajectory_topology.pdb"
    expected = (root / "joined" / "production.dcd", topology)
    assert study_trajectory(root) == expected
    pieces = state_record_pieces(root)
    assert [piece["path"] for piece in pieces] == [
        root / "simulation" / "energy.csv", root / "segment-001" / "simulation" / "energy.csv"]
    with record_access.confined_records(root, lambda path: True) as scope:
        assert study_trajectory(root) == expected
        assert len(state_record_pieces(root)) == 2
    assert scope.denied == set()


def _a_study_of_runs_its_manifest_linked_out(tmp_path: Path) -> Path:
    """Two runs of a study whose batch manifest is a link to one outside
    it, giving each run a value of its own."""
    from tests.test_a_study_running_until_it_knows_is_drawn import _linked_out
    from tests.test_the_overview_of_a_study_of_runs import _a_study_of_runs

    root = _a_study_of_runs(tmp_path, "simulation.random_seed", [1, 2])

    def outside_values(manifest):
        for entry in [*(manifest.get("planned") or []), *(manifest.get("runs") or [])]:
            entry["sweep_values"] = {"simulation.random_seed": "OUTSIDE-" + entry["run_id"][-1]}
    _linked_out(root, "batch_manifest.json", tmp_path / "elsewhere" / "batch.json",
                outside_values)
    return root


@pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])
def test_a_batch_manifest_linked_out_names_no_run(tmp_path, public):
    """`/api/runs-compared` and `/api/app-state` read a study's batch
    manifest linked out of it, beyond loopback too: the runs table and the
    breadcrumb said "seed OUTSIDE-1" beside a line saying it was not read."""
    from fastmdxplora.gui.runs_compared import runs_compared

    root = _a_study_of_runs_its_manifest_linked_out(tmp_path)
    runs = sorted(folder.name for folder in (root / "runs").iterdir())
    # What the link leads to, read without holding the read to the study.
    assert "OUTSIDE-1" in json.dumps(runs_compared(root))
    for served, named in ((root, "batch_manifest.json"),
                          (root / "runs" / runs[1], "../../batch_manifest.json")):
        code, body = _ask(served, tmp_path, "/api/app-state", public=public)
        state = json.loads(body)
        assert code == 200 and "OUTSIDE" not in body.decode()
        assert state["unavailable_why"] == {named: "outside"}, state.get("unavailable_why")
        assert f"{named} was not read" in state["not_read"]
        # Its runs named by their folders.
        listed = state["runs"] if served == root else state["run_of"]["runs"]
        assert [run["label"] for run in listed] == runs
        if served != root:
            assert (state["run_of"]["study"], state["run_of"]["label"]) == (root.name, runs[1])
    code, body = _ask(root, tmp_path, "/api/runs-compared", public=public)
    page = json.loads(body)
    assert code == 200 and "OUTSIDE" not in body.decode()
    assert page["ok"] and [run["label"] for run in page["runs"]] == runs
    assert page["unavailable_why"] == {"batch_manifest.json": "outside"}
    assert "batch_manifest.json was not read" in page["not_read"]


@pytest.mark.skipif(not Path("/proc/self/stat").is_file(), reason="reads /proc")
@pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])
def test_a_process_record_linked_out_is_never_adopted(tmp_path, public):
    """The adoption read the study's process record through a link out of
    it: a live process named there was taken for the study's run, said
    running, and was what Stop signalled."""
    import shutil
    import subprocess
    import sys
    from datetime import datetime, timezone

    from fastmdxplora.gui.exploration import DashboardRuntime
    from fastmdxplora.orchestrator import RUN_PROCESS_FILE, this_machine

    root = _study(tmp_path)
    stand_in = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)",
                                 "fastmdx", "run", str(root)])
    try:
        outside = _write(tmp_path, "elsewhere/run.json", {
            "pid": stand_in.pid, "argv": ["-c", "fastmdx", "run", str(root)],
            "started_at": datetime.now(timezone.utc).isoformat(), **this_machine()})
        _link(root / RUN_PROCESS_FILE, outside)
        code, body = _ask(root, tmp_path, "/api/app-state", public=public)
        state = json.loads(body)
        assert code == 200 and state["process_running"] is False
        assert state["status"] != "running"
        runtime = DashboardRuntime(workspace_root=root, exploration_root=tmp_path,
                                   active_root=root)
        assert runtime.process is None
        assert runtime.stop()["stopped"] is False and stand_in.poll() is None
        # The same record in the study names its run.
        (root / RUN_PROCESS_FILE).unlink()
        shutil.copyfile(outside, root / RUN_PROCESS_FILE)
        runtime = DashboardRuntime(workspace_root=root, exploration_root=tmp_path,
                                   active_root=root)
        assert runtime.process is not None and runtime.process.pid == stand_in.pid
    finally:
        stand_in.kill()
        stand_in.wait()
