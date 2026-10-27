"""Every route a page asks for reads only inside the study.

A study folder can hold a link that leads out of it, and a study copied
whole names its original's files in its records. What a link leads to, or
a file named outside the study, is never read for a page, on this computer
or on a public bind: the answer says it was not read instead. MDTraj reads a
DCD in C, where the audit hook on `open` does not see it, so each answer is
checked as well as the hook.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pytest

from tests.test_the_study_pages_read_only_the_study import _OPENED, _WATCHED, _link, _watch

md = pytest.importorskip("mdtraj")


def _serve(root: Path, workspace: Path, *, public: bool):
    from fastmdxplora.gui.exploration import DashboardRuntime
    from tests.test_a_public_dashboard_answers_only_what_it_lists import _serving

    runtime = DashboardRuntime(workspace_root=root, exploration_root=workspace,
                               active_root=root)
    return _serving(runtime, allow_control=not public)


def _asked(root: Path, workspace: Path, route: str, *, public: bool = False,
           body: dict | None = None) -> tuple[int, bytes]:
    from tests.test_a_public_dashboard_answers_only_what_it_lists import _ask

    with _serve(root, workspace, public=public) as url:
        return _ask(url + route, body=body)


def _study(tmp_path: Path) -> Path:
    """A finished study of two chains of alanines and a ligand, twelve
    frames of each atom moving about its place, analysed (its manifest
    naming its own trajectory and structure, with an RMSF), its browser's
    frames written."""
    from tests.test_what_the_viewer_computes_from_the_frames_keeps_to_the_study import (
        _study as written)

    root = written(tmp_path)
    simulation = root / "simulation"
    # The system simulated, as the Viewer's structure.
    shutil.copy(simulation / "trajectory_topology.pdb", simulation / "topology.pdb")
    analysis = root / "analysis"
    (analysis / "rmsf").mkdir(parents=True)
    n_atoms = md.load_topology(str(simulation / "trajectory_topology.pdb")).n_atoms
    (analysis / "analysis_manifest.json").write_text(json.dumps({
        "phase": "analysis", "n_frames": 12, "n_atoms": n_atoms,
        "load_kwargs": {"saving_interval_ps": 1.0},
        "trajectory_input": str(simulation / "production.dcd"),
        "topology_input": str(simulation / "trajectory_topology.pdb"),
        "resolved": {"trajectory": str(simulation / "production.dcd"),
                     "topology": str(simulation / "trajectory_topology.pdb")},
        "results": {"rmsf": {"status": "ok"}}}), encoding="utf-8")
    (analysis / "rmsf" / "options.json").write_text(json.dumps({"analysis": "rmsf"}),
                                                     encoding="utf-8")
    (analysis / "rmsf" / "rmsf.dat").write_text(
        "# residue rmsf_nm\n" + "".join(f"{k} {0.05 + 0.01 * k:.4f}\n" for k in range(1, 5)),
        encoding="utf-8")
    (root / "manifest.json").write_text(json.dumps({"phases": [
        {"name": "simulation", "status": "ok"}, {"name": "analysis", "status": "ok"}]}),
        encoding="utf-8")
    return root


def _moved_out(root: Path, tmp_path: Path, relative: str, *, shift: float = 0.0) -> Path:
    """``relative`` moved out of the study, a link to it left in its place;
    a DCD moved with every atom shifted ``shift`` nm along x, so a read of
    it would show."""
    outside = tmp_path / "outside"
    target = outside / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    (root / relative).rename(target)
    if shift and target.suffix == ".dcd":
        frames = md.load_dcd(str(target), top=str(
            (root / relative).parent / ("frames_topology.pdb" if target.name == "frames.dcd"
                                        else "trajectory_topology.pdb")))
        frames.xyz[:, :, 0] += shift
        frames.save_dcd(str(target))
    _link(root / relative, target)
    return outside


def _opened_asking(outside: Path, ask) -> tuple[object, list[str]]:
    _watch(outside)
    try:
        answer = ask()
        opened = sorted(set(_OPENED))
    finally:
        _WATCHED.clear()
    return answer, opened


# --------------------------------------------------------------------------
# A scene is made of the study's own frames (G7 H1)
# --------------------------------------------------------------------------

SCENES = {"frame": {"frame": 3}, "source frame": {"frame": 7, "frame_source": True}}


@pytest.mark.parametrize("kind", sorted(SCENES))
@pytest.mark.parametrize("relative", ["simulation/production.dcd", "simulation/frames.dcd",
                                      "simulation/frames_topology.pdb"])
def test_a_scene_is_not_made_of_frames_linked_out_of_the_study(tmp_path, relative, kind):
    """The scene POST reads the frames, the trajectory and their topology
    held to the study: one linked out of it is refused with what was not
    read, no scene is written, and a public bind has no scene to send."""
    root = _study(tmp_path)
    view = {**SCENES[kind], "camera": {"position": [0, 0, 50], "target": [0, 0, 0],
                                       "up": [0, 1, 0]}}
    status, body = _asked(root, tmp_path, "/api/scenes", body={"name": "own", "view": view})
    assert status == 200 and json.loads(body)["ok"], body[:300]
    outside = _moved_out(root, tmp_path, relative, shift=5.0)

    (status, body), opened = _opened_asking(outside, lambda: _asked(
        root, tmp_path, "/api/scenes", body={"name": "s1", "view": view}))

    said = json.loads(body)
    assert status == 200 and said["ok"] is False, said
    assert f"{relative} was not read" in said["not_read"], said
    assert opened == []
    assert not (root / "scenes" / "s1.mvsx").exists()
    status, _ = _asked(root, tmp_path, "/scenes/s1/structure.pdb", public=True)
    assert status == 404


def test_a_scene_is_not_written_through_a_folder_linked_out(tmp_path):
    root = _study(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _link(root / "scenes", elsewhere)
    status, body = _asked(root, tmp_path, "/api/scenes",
                          body={"name": "s1", "view": {"camera": {
                              "position": [0, 0, 50], "target": [0, 0, 0], "up": [0, 1, 0]}}})
    said = json.loads(body)
    assert status == 200 and said["ok"] is False, said
    assert list(elsewhere.iterdir()) == []


# --------------------------------------------------------------------------
# Another study beside this one reads this one held to it (G7 H2)
# --------------------------------------------------------------------------

#: This study's records `/api/beside` reads, and what each says outside it.
BESIDE_RECORDS = {
    "simulation/simulation_parameters.json": json.dumps({
        "parameters": {"integrator": "verlet", "timestep_fs": 2.0},
        "duration_ns_actual": 777.0,
        "resolved": {"trajectory_interval_steps": 500, "production_steps": 6000}}),
    "analysis/analysis_manifest.json": None,
    "analysis/rmsf/rmsf.dat": "# residue rmsf_nm\n" + "".join(
        f"{k} 777.0\n" for k in range(1, 5)),
}


@pytest.mark.parametrize("relative", sorted(BESIDE_RECORDS))
def test_another_study_beside_reads_this_one_s_own_records(tmp_path, relative):
    root = _study(tmp_path)
    other = _study(tmp_path / "other")
    route = f"/api/beside?path={other}"
    status, body = _asked(root, tmp_path, route)
    said = json.loads(body)
    assert status == 200 and said["ok"] and "not_read" not in said, said
    assert said["property"]["high"] < 1.0
    shutil.rmtree(root / "viewer_beside")
    outside = _moved_out(root, tmp_path, relative)
    if BESIDE_RECORDS[relative] is not None:
        (outside / relative).write_text(BESIDE_RECORDS[relative], encoding="utf-8")

    (status, body), opened = _opened_asking(outside, lambda: _asked(root, tmp_path, route))

    said = json.loads(body)
    assert status == 200 and f"{relative} was not read" in said["not_read"], said
    assert opened == []
    assert "776.9" not in body.decode("utf-8") and "777.0" not in body.decode("utf-8")
    assert said.get("property") is None or said["property"]["high"] < 1.0


# --------------------------------------------------------------------------
# A residue or two atoms selected read the study's own structure (G7 H3, G8 H1)
# --------------------------------------------------------------------------

SELECTED = {
    "/api/selection?chain=A&resseq=2&resname=ALA&atom=CA&frames_atom=6": True,
    "/api/measure-over-frames?a=chainid+0+and+resSeq+2+and+name+CA"
    "&b=chainid+1+and+resSeq+3+and+name+CA": True,
    "/api/residue-states?chain=A&resseq=2&resname=HIS": False,
}


def _given(root: Path, tmp_path: Path) -> None:
    """The structure the study was given, a histidine its second residue:
    named by its Config outside the study, and placed in its setup."""
    text = (root / "simulation" / "trajectory_topology.pdb").read_text(encoding="utf-8")
    given = "".join(line.replace("ALA A   2", "HIS A   2") + "\n"
                    for line in text.splitlines() if line.startswith(("ATOM", "HETATM")))
    (tmp_path / "given.pdb").write_text(given + "END\n", encoding="utf-8")
    (root / "setup").mkdir(exist_ok=True)
    (root / "setup" / "input.pdb").write_text(given + "END\n", encoding="utf-8")
    (root / "resolved_config.yml").write_text(
        f"systems: [{{system: {tmp_path / 'given.pdb'}}}]\nsetup: {{forcefield: amber14}}\n",
        encoding="utf-8")


@pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])
@pytest.mark.parametrize("relative", ["simulation/trajectory_topology.pdb",
                                      "analysis/analysis_manifest.json",
                                      "simulation/frames_index.json", "resolved_config.yml",
                                      "setup/input.pdb"])
@pytest.mark.parametrize("route", sorted(SELECTED))
def test_a_selection_reads_no_structure_linked_out(tmp_path, route, relative, public):
    if public and not SELECTED[route]:
        pytest.skip("not answered beyond loopback")
    root = _study(tmp_path)
    _given(root, tmp_path)
    status, body = _asked(root, tmp_path, route, public=public)
    said = json.loads(body)
    assert status == 200 and said["ok"] and "not_read" not in said, said
    outside = _moved_out(root, tmp_path, relative)

    (status, body), opened = _opened_asking(outside, lambda: _asked(
        root, tmp_path, route, public=public))

    said = json.loads(body)
    assert opened == []
    read = {"/api/selection": ("simulation/trajectory_topology.pdb",
                               "analysis/analysis_manifest.json",
                               "simulation/frames_index.json"),
            "/api/measure-over-frames": ("simulation/trajectory_topology.pdb",
                                         "analysis/analysis_manifest.json"),
            "/api/residue-states": ("resolved_config.yml", "setup/input.pdb")}[
        route.split("?")[0]]
    if relative in read:
        assert f"{relative} was not read" in said["not_read"], said
    assert str(outside) not in body.decode("utf-8")


@pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])
@pytest.mark.parametrize("route", sorted(SELECTED))
def test_a_study_copied_whole_selects_in_its_own_structure(tmp_path, route, public):
    """A `cp -a` copy, its original still there: what it selects is read
    from the copy alone, and nothing of the original is opened or named."""
    if public and not SELECTED[route]:
        pytest.skip("not answered beyond loopback")
    original = _study(tmp_path / "first")
    _given(original, tmp_path)
    copy = tmp_path / "copy"
    shutil.copytree(original, copy, symlinks=True)

    (status, body), opened = _opened_asking(original, lambda: _asked(
        copy, tmp_path, route, public=public))

    said = json.loads(body)
    assert status == 200 and opened == [], (said, opened)
    assert str(original) not in body.decode("utf-8")
    if not route.startswith("/api/measure"):
        assert said["ok"], said
    if public:
        assert str(tmp_path) not in body.decode("utf-8")


# --------------------------------------------------------------------------
# A copied study is read as its own only where it is the same structure
# (G7 M1, G8 M2)
# --------------------------------------------------------------------------

def _chain(path: Path, residues: int, atoms: tuple[str, ...]) -> Path:
    topology = md.Topology()
    chain = topology.add_chain("A")
    for number in range(1, residues + 1):
        residue = topology.add_residue("ALA", chain, resSeq=number)
        for name in atoms:
            topology.add_atom(name, md.element.carbon, residue)
    path.parent.mkdir(parents=True, exist_ok=True)
    md.Trajectory(np.zeros((1, topology.n_atoms, 3), dtype=np.float32) + 0.1,
                  topology).save_pdb(str(path))
    return path


@pytest.mark.parametrize("original_there", [True, False], ids=["kept", "gone"])
@pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])
def test_a_structure_named_outside_of_other_atoms_is_not_read_as_the_study_s(
        tmp_path, public, original_there):
    """The manifest names a 29-atom structure outside the study by the last
    two parts of the study's own 54-atom file: that file is not the one the
    analyses read, so neither is read, and the name is said not read."""
    root = tmp_path / "study"
    named = _chain(tmp_path / "elsewhere" / "simulation" / "trajectory_topology.pdb", 29,
                   ("CA",))
    _chain(root / "simulation" / "trajectory_topology.pdb", 9,
           ("N", "CA", "C", "O", "CB", "H"))
    if not original_there:
        named.unlink()
    (root / "analysis" / "rmsf").mkdir(parents=True)
    (root / "analysis" / "analysis_manifest.json").write_text(json.dumps({
        "phase": "analysis", "n_frames": 3, "n_atoms": 29,
        "resolved": {"topology": str(named)}, "results": {"rmsf": {"status": "ok"}}}),
        encoding="utf-8")
    (root / "analysis" / "rmsf" / "rmsf.dat").write_text(
        "chain,residue,insertion,rmsf_nm\nA,1,,0.11\nA,2,,0.12\nA,3,,0.13\n", encoding="utf-8")
    (root / "analysis" / "rmsf" / "options.json").write_text(json.dumps(
        {"analysis": "rmsf", "selection": None, "options": {}, "findings": {}}),
        encoding="utf-8")
    (root / "manifest.json").write_text(json.dumps({"phases": [
        {"name": "analysis", "status": "ok"}]}), encoding="utf-8")

    for route in ("/api/series?analysis=rmsf",
                  "/api/selection?chain=A&resseq=2&resname=ALA&atom=CA"):
        (status, body), opened = _opened_asking(tmp_path / "elsewhere", lambda asked=route: (
            _asked(root, tmp_path, asked, public=public)))
        said = json.loads(body)
        assert status == 200 and opened == [], (route, opened)
        assert "a file named outside the study was not read" in said["not_read"], said
        if route.startswith("/api/series"):
            assert said["entities"] == [None, None, None], said
        else:
            assert said["ok"] is False, said


def _copied_study(tmp_path: Path) -> tuple[Path, Path]:
    """A study of eleven frames analysed for RMSD, its manifest naming its
    trajectory where it was, and a `cp -a` copy of it beside it; the
    original's trajectory then changed, every atom moved 5 nm along x."""
    from tests.test_a_graph_opens_exact_source_frame import _series
    from tests.test_a_graph_opens_exact_source_frame import _study as written

    original = written(tmp_path / "first")
    _series(original)
    manifest = original / "analysis" / "analysis_manifest.json"
    record = json.loads(manifest.read_text(encoding="utf-8"))
    record["n_atoms"] = 5
    record["resolved"]["topology"] = str(original / "simulation" / "trajectory_topology.pdb")
    manifest.write_text(json.dumps(record), encoding="utf-8")
    copy = tmp_path / "copy"
    shutil.copytree(original, copy, symlinks=True)
    production = original / "simulation" / "production.dcd"
    frames = md.load_dcd(str(production),
                         top=str(original / "simulation" / "trajectory_topology.pdb"))
    frames.xyz[:, :, 0] += 5.0
    frames.save_dcd(str(production))
    return original, copy


@pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])
def test_a_copied_study_s_time_point_opens_its_own_frame(tmp_path, public):
    from urllib.parse import quote

    original, copy = _copied_study(tmp_path)
    series = json.loads(_asked(copy, tmp_path, "/api/series?analysis=rmsd", public=public)[1])
    assert series["linked"] is True, series
    signature = json.loads(_asked(copy, tmp_path, "/api/frames-info",
                                  public=public)[1])["source_signature"]
    route = f"/structure/exact-frame.xtc?frame=7&signature={quote(signature, safe='')}"
    (status, kept), opened = _opened_asking(original, lambda: _asked(
        copy, tmp_path, route, public=public))
    assert status == 200 and opened == []
    shutil.rmtree(original)
    status, alone = _asked(copy, tmp_path, route, public=public)
    # The copy's own frame 7, with the original there or not.
    assert status == 200 and kept == alone


@pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])
def test_a_copied_study_measures_over_its_own_frames(tmp_path, public):
    original, copy = _copied_study(tmp_path)
    route = "/api/measure-over-frames?a=resSeq+1+and+name+N&b=resSeq+1+and+name+CB"
    (status, body), opened = _opened_asking(original, lambda: _asked(
        copy, tmp_path, route, public=public))
    said = json.loads(body)
    assert status == 200 and said["ok"] and opened == [], said
    assert str(original) not in body.decode("utf-8")
    if public:
        assert "simulation/production.dcd" in said["command"]
        assert str(tmp_path) not in body.decode("utf-8")
    else:
        assert str(copy / "simulation" / "production.dcd") in said["command"]


# --------------------------------------------------------------------------
# The structure and the live frame the Viewer renders (G7 M2)
# --------------------------------------------------------------------------

#: What a page asks of the structure, the live frame or the preview, the
#: record each reads, and whether it answers in JSON.
OF_THE_STRUCTURE = {
    "/structure/topology.pdb": ("simulation/topology.pdb", False),
    "/structure/topology.pdb?solvent=1": ("simulation/topology.pdb", False),
    "/api/structure-info": ("simulation/topology.pdb", True),
    "/api/ligands": ("simulation/topology.pdb", True),
    "/api/protein-preview": ("simulation/topology.pdb", True),
    "/api/viewer-atoms?of=structure&expression=protein": ("simulation/topology.pdb", True),
    "/api/secondary-structure?of=structure": ("simulation/topology.pdb", True),
    "/api/live-frame-index": ("simulation/live_frame_index.json", True),
    "/api/live-coordinates": ("simulation/live_frame_index.json", True),
    "/structure/live-frame.pdb": ("simulation/live_frame.pdb", False),
    "/structure/live-frame.dcd": ("simulation/live_frame.pdb", False),
    "/api/viewer-atoms?of=live&expression=protein": ("simulation/live_frame.pdb", True),
    "/api/secondary-structure?of=live": ("simulation/live_frame.pdb", True),
}


def _with_a_live_frame(root: Path) -> Path:
    simulation = root / "simulation"
    shutil.copy(simulation / "trajectory_topology.pdb", simulation / "live_frame.pdb")
    (simulation / "live_frame_index.json").write_text(json.dumps({
        "live_frame_available": True, "live_frame_index": 6000, "simulation_time_ns": 0.012}),
        encoding="utf-8")
    # A structure the Viewer could be sent in the system's place, were it.
    (root / "setup").mkdir(exist_ok=True)
    shutil.copy(simulation / "trajectory_topology.pdb", root / "setup" / "prepared.pdb")
    return root


@pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])
@pytest.mark.parametrize("route", sorted(OF_THE_STRUCTURE))
def test_the_structure_and_live_frame_are_read_from_the_study_alone(tmp_path, route, public):
    root = _with_a_live_frame(_study(tmp_path))
    relative, answers_json = OF_THE_STRUCTURE[route]
    status, body = _asked(root, tmp_path, route, public=public)
    assert status == 200, body[:300]
    if answers_json:
        assert "not_read" not in json.loads(body), body[:300]
    outside = _moved_out(root, tmp_path, relative)

    (status, body), opened = _opened_asking(outside, lambda: _asked(
        root, tmp_path, route, public=public))

    assert opened == []
    if not answers_json:
        # Neither the file linked out nor another in its place is sent.
        assert status == 404
        return
    said = json.loads(body)
    assert status == 200 and f"{relative} was not read" in said["not_read"], said
    assert str(outside) not in body.decode("utf-8")


@pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])
def test_the_structure_s_details_read_no_run_record_linked_out(tmp_path, public):
    root = _with_a_live_frame(_study(tmp_path))
    outside = _moved_out(root, tmp_path, "simulation/simulation_parameters.json")
    (status, body), opened = _opened_asking(outside, lambda: _asked(
        root, tmp_path, "/api/structure-info", public=public))
    assert status == 200 and opened == []


# --------------------------------------------------------------------------
# What else a page reads of the study: its views, selections, scenes,
# movies, figures, stopping record, Config and deposit
# --------------------------------------------------------------------------

#: Each route, the record of the study it reads, and whether a public bind
#: answers it.
OF_THE_STUDY = {
    "/api/stopping": ("stopping.json", True),
    "/api/views": ("viewer_views.json", True),
    "/api/viewer-selections": ("viewer_selections.json", True),
    "/api/scenes": ("scenes/s1.mvsx", True),
    "/scenes/s1/index.mvsj": ("scenes/s1.mvsx", True),
    "/api/movies": ("movies/m1.mp4", False),
    "/analysis-figures-svg.zip": ("analysis/rmsf/rmsf.svg", True),
    "/api/again": ("resolved_config.yml", False),
    "/api/files/deposit": ("manifest.json", False),
    "/api/timeline": ("stopping.json", False),
}


def _with_its_records(root: Path, tmp_path: Path) -> Path:
    """The study with a view, a selection, a scene, a movie, a figure, a
    stopping record and a Config, as the GUI and a run write them."""
    camera = {"position": [0, 0, 50], "target": [0, 0, 0], "up": [0, 1, 0]}
    for route, body in (("/api/views", {"name": "v1", "view": {"camera": camera}}),
                        ("/api/viewer-selections", {"name": "s1", "selection": {
                            "kind": "expression", "expression": "protein"}}),
                        ("/api/scenes", {"name": "s1", "view": {"camera": camera}})):
        status, said = _asked(root, tmp_path, route, body=body)
        assert status == 200 and json.loads(said)["ok"], said
    (root / "movies").mkdir()
    (root / "movies" / "m1.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")
    (root / "analysis" / "rmsf" / "rmsf.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")
    (root / "stopping.json").write_text(json.dumps({
        "targets": [{"analysis": "rmsd", "relative_error": 0.05}], "outcome": "running",
        "rounds": [{"production_ns": 0.012, "decision": "more"}]}), encoding="utf-8")
    (root / "resolved_config.yml").write_text(
        "systems: [{system: 1L2Y}]\ninclude_phase: [analysis]\n", encoding="utf-8")
    return root


@pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])
@pytest.mark.parametrize("route", sorted(OF_THE_STUDY))
def test_what_else_a_page_reads_is_the_study_s_own(tmp_path, route, public):
    relative, answered = OF_THE_STUDY[route]
    if public and not answered:
        pytest.skip("not answered beyond loopback")
    root = _with_its_records(_study(tmp_path), tmp_path)
    status, body = _asked(root, tmp_path, route, public=public)
    assert status == 200, body[:300]
    outside = _moved_out(root, tmp_path, relative)

    (status, body), opened = _opened_asking(outside, lambda: _asked(
        root, tmp_path, route, public=public))

    assert opened == []
    if not route.startswith("/api/"):
        assert status == 404
        return
    said = json.loads(body)
    assert status == 200 and f"{relative} was not read" in said["not_read"], said
    assert str(outside) not in body.decode("utf-8")


@pytest.mark.parametrize("route,relative,body", [
    ("/api/views", "viewer_views.json", {"name": "v2", "view": {"camera": {
        "position": [0, 0, 50], "target": [0, 0, 0], "up": [0, 1, 0]}}}),
    ("/api/views", "viewer_views.json", {"name": "v1", "action": "delete"}),
    ("/api/viewer-selections", "viewer_selections.json", {"name": "s2", "selection": {
        "kind": "expression", "expression": "protein"}}),
    ("/api/viewer-selections", "viewer_selections.json", {"name": "s1", "action": "delete"}),
])
def test_nothing_is_saved_over_views_or_selections_linked_out(tmp_path, route, relative,
                                                              body):
    root = _with_its_records(_study(tmp_path), tmp_path)
    outside = _moved_out(root, tmp_path, relative)
    before = (outside / relative).read_bytes()

    (status, answer), opened = _opened_asking(outside, lambda: _asked(
        root, tmp_path, route, body=body))

    said = json.loads(answer)
    assert status == 200 and said["ok"] is False and opened == [], said
    assert f"{relative} was not read" in said["not_read"]
    assert (outside / relative).read_bytes() == before
    assert (root / relative).is_symlink()


def test_no_movie_is_made_through_a_folder_linked_out(tmp_path):
    from fastmdxplora.movies import encoding

    if not encoding()["ok"]:
        pytest.skip("no movie encoder on this computer")
    root = _study(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _link(root / "movies", elsewhere)
    status, body = _asked(root, tmp_path, "/api/movies", body={
        "name": "m1", "fps": 30, "width": 64, "height": 64})
    said = json.loads(body)
    assert status == 200 and said["ok"] is False and "movies was not read" in said["not_read"]
    assert list(elsewhere.iterdir()) == []


# --------------------------------------------------------------------------
# The Viewer says what it did not read (G8 lows)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])
def test_a_run_linked_out_is_said_not_read_in_the_runs_panel(tmp_path, public):
    from tests.test_the_run_shown_is_read_held_to_its_study import _moved_out as moved
    from tests.test_the_run_shown_is_read_held_to_its_study import _runs

    root = _runs(tmp_path, 13, 7)
    moved(root, tmp_path, "runs/r1")
    info = json.loads(_asked(root, tmp_path, "/api/frames-info", public=public)[1])
    excluded = {run["run_id"]: run["reason"] for run in info["runs_together"]["excluded"]}
    assert excluded["r1"] == "It is linked out of the study, so it was not read."


def test_the_pocket_says_a_frame_s_time_in_ps_under_1_ns():
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("no node")
    script = (Path(__file__).parents[1] / "src" / "fastmdxplora" / "gui" / "static"
              / "viewer-pocket.js").read_text(encoding="utf-8")
    start = script.index("  function timeSaid(")
    function = script[start:script.index("\n  }\n", start) + 4]
    said = subprocess.run([node, "-e", function + "\nconsole.log(JSON.stringify("
                           "[timeSaid(0.12), timeSaid(0.0015), timeSaid(1), timeSaid(12.5)]));"],
                          capture_output=True, text=True, check=True)
    assert json.loads(said.stdout) == ["120 ps", "1.5 ps", "1.000 ns", "12.500 ns"]


@pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])
@pytest.mark.parametrize("route", ["/api/results", "/api/analysis-overview",
                                   "/api/convergence?analysis=rmsd"])
def test_the_analysis_pages_say_a_record_not_read_in_one_sentence(tmp_path, route, public):
    """Each answer held to the study says what it did not read in its
    ``not_read``, as every other does, beside its own way of saying it."""
    from tests.test_the_study_pages_read_only_the_study import OUTSIDE
    from tests.test_the_study_pages_read_only_the_study import _study as analysed
    from tests.test_the_study_pages_read_only_the_study import _write

    root = analysed(tmp_path)
    outside = _write(tmp_path, "elsewhere/options.json", OUTSIDE["determined"])
    _link(root / "analysis/rmsd/options.json", outside)
    status, body = _asked(root, tmp_path, route, public=public)
    said = json.loads(body)
    assert status == 200 and "analysis/rmsd/options.json was not read" in said["not_read"]


# --------------------------------------------------------------------------
# What can be run again is read from the study's own records (G10 H3)
# --------------------------------------------------------------------------

def _linked_out_saying(root: Path, tmp_path: Path, relative: str, change) -> Path:
    """``relative`` moved out of the study and changed there by ``change``,
    a link to it left in its place."""
    outside = _moved_out(root, tmp_path, relative)
    record = json.loads((outside / relative).read_text(encoding="utf-8"))
    change(record)
    (outside / relative).write_text(json.dumps(record), encoding="utf-8")
    return outside


def test_the_analyses_offered_again_are_not_read_through_a_link(tmp_path):
    """The manifest linked out names an analysis the study never ran: it is
    not offered ticked, the manifest is said not read, and nothing is
    offered to run from it (as the POST refuses)."""
    root = _study(tmp_path)
    (root / "resolved_config.yml").write_text("analysis:\n  include: [rg]\n",
                                              encoding="utf-8")
    said = json.loads(_asked(root, tmp_path, "/api/again")[1])
    assert said["recorded"] == ["rmsf"] and "not_read" not in said, said
    outside = _linked_out_saying(root, tmp_path, "analysis/analysis_manifest.json",
                                 lambda record: record.update(
                                     results={"marker_analysis": {"status": "ok"}}))

    (status, body), opened = _opened_asking(outside, lambda: _asked(
        root, tmp_path, "/api/again"))

    said = json.loads(body)
    assert status == 200 and opened == []
    assert said["recorded"] == [], said
    assert "marker_analysis" not in body.decode("utf-8")
    assert "analysis/analysis_manifest.json was not read" in said["not_read"], said
    assert said["analysis"]["can"] is False


# --------------------------------------------------------------------------
# The study played beside this one is held to itself (G10 H4)
# --------------------------------------------------------------------------

#: The other study's records `/api/beside` reads, and whether without it
#: nothing is played beside.
OTHER_RECORDS = {"simulation/frames.dcd": True, "simulation/frames_topology.pdb": True,
                 "analysis/rmsf/rmsf.dat": False}


@pytest.mark.parametrize("relative", sorted(OTHER_RECORDS))
def test_the_study_beside_is_not_read_through_its_own_links(tmp_path, relative):
    root = _study(tmp_path)
    other = _study(tmp_path / "other")
    route = f"/api/beside?path={other}"
    said = json.loads(_asked(root, tmp_path, route)[1])
    assert said["ok"] and "not_read" not in said and said["property"], said
    shutil.rmtree(root / "viewer_beside")
    outside = tmp_path / "outside_other"
    target = outside / relative
    target.parent.mkdir(parents=True)
    (other / relative).rename(target)
    if relative.endswith("rmsf.dat"):
        target.write_text("# residue rmsf_nm\n" + "".join(f"{k} 777.0\n" for k in range(1, 5)),
                          encoding="utf-8")
    _link(other / relative, target)

    (status, body), opened = _opened_asking(outside, lambda: _asked(root, tmp_path, route))

    said = json.loads(body)
    assert status == 200 and opened == [], (said, opened)
    assert f"{other.name}: {relative} was not read" in said["not_read"], said
    assert "777.0" not in body.decode("utf-8") and "776.9" not in body.decode("utf-8")
    if OTHER_RECORDS[relative]:
        assert said["ok"] is False and not (root / "viewer_beside").exists(), said
        # Said where the Viewer says why it shows nothing: not that it has
        # no frames, and what was not read.
        assert said["reason"].startswith(f"{other.name}'s frames are not its own records")
        assert f"{other.name}: {relative} was not read" in said["reason"], said
    else:
        assert said["ok"] and said["property"] is None, said
        assert f"{other.name}: {relative} was not read" in said["said"], said


def test_the_study_beside_is_paired_again_once_its_clock_is_not_read(tmp_path):
    """Paired by simulation time while both clocks are read; once the other
    study's clock is no longer read, by place in its run, not from the
    pairing kept beside the frames (G12 L4)."""
    root = _study(tmp_path)
    other = _study(tmp_path / "other")
    route = f"/api/beside?path={other}"
    said = json.loads(_asked(root, tmp_path, route)[1])
    assert said["ok"] and "nearest it in simulation time" in said["said"], said
    index = other / "simulation" / "frames_index.json"
    record = json.loads(index.read_text(encoding="utf-8"))
    record["frame_times_ns"] = [None] * len(record["frame_times_ns"])
    index.write_text(json.dumps(record), encoding="utf-8")
    (other / "simulation" / "simulation_parameters.json").unlink()
    manifest = other / "analysis" / "analysis_manifest.json"
    record = json.loads(manifest.read_text(encoding="utf-8"))
    record.pop("load_kwargs")
    manifest.write_text(json.dumps(record), encoding="utf-8")

    said = json.loads(_asked(root, tmp_path, route)[1])

    assert said["ok"] and "at the same place in its run" in said["said"], said


# --------------------------------------------------------------------------
# What a POST starts or removes is decided from the study's own records
# (G10 M1)
# --------------------------------------------------------------------------

#: The POSTs that run a study's phases again, and what each is asked.
RUN_AGAIN = {"/api/again": {"phases": ["analysis"]}, "/api/report/write": {}}


@pytest.mark.parametrize("relative", ["resolved_config.yml", "analysis/analysis_manifest.json"])
@pytest.mark.parametrize("route", sorted(RUN_AGAIN))
def test_nothing_is_run_again_from_a_record_linked_out(tmp_path, monkeypatch, route, relative):
    """As `GET /api/again` offers nothing from a Config linked out of the
    study, a POST sent without asking it starts nothing: refused, with the
    record said not read. The start is stood in for; nothing is run."""
    from fastmdxplora.gui.exploration import DashboardRuntime

    started = []

    def spawn(self, command, study, dashboard_url=None):
        started.append(command)
        return {"ok": True}

    monkeypatch.setattr(DashboardRuntime, "_spawn", spawn)
    monkeypatch.setattr(DashboardRuntime, "_others_running", lambda self, **_: None)
    root = _study(tmp_path)
    (root / "resolved_config.yml").write_text("analysis:\n  include: [rmsf]\n",
                                              encoding="utf-8")
    (root / "report").mkdir()
    status, body = _asked(root, tmp_path, route, body=RUN_AGAIN[route])
    assert status == 200 and json.loads(body)["ok"] and len(started) == 1, body[:300]
    started.clear()
    outside = _moved_out(root, tmp_path, relative)

    (status, body), opened = _opened_asking(outside, lambda: _asked(
        root, tmp_path, route, body=RUN_AGAIN[route]))

    said = json.loads(body)
    assert status == 200 and said["ok"] is False and started == [], said
    assert f"{relative} was not read" in said["error"] and opened == [], (said, opened)


def test_a_run_whose_record_is_linked_out_keeps_its_snapshots(tmp_path):
    """Clearing the scratch judges each run's snapshots by the run's own
    record, read held to the study: one linked out of it (saying the run
    completed) is not read, so its snapshots are not removed."""
    from tests.test_the_scratch_is_cleared_after_asking import _left, _post, _serving
    from tests.test_the_scratch_is_cleared_after_asking import _study as scratched

    root = scratched(tmp_path / "study")
    outside = _moved_out(root, tmp_path, "simulation/live_status.json")
    snapshots = {name for name in _left(root) if "live_frame" in name and name.count("/") > 0
                 and ("live_frames/" in name or name.endswith("live_frame_history.json"))}
    assert len(snapshots) == 4

    _watch(outside)
    try:
        with _serving(root) as url:
            status, done = _post(url + "/api/files/clear-scratch", {})
        opened = sorted(set(_OPENED))
    finally:
        _WATCHED.clear()

    assert status == 200 and done["ok"] and done["snapshots"] == 0 and opened == [], done
    assert "was not read" in done["kept"][0]["why"], done
    assert snapshots <= _left(root)
    assert "simulation/frames.dcd" not in _left(root)


# --------------------------------------------------------------------------
# With the manifest not read, no other structure is selected in (G10 L2)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])
@pytest.mark.parametrize("route", [route for route, public in SELECTED.items() if public])
def test_a_manifest_linked_out_selects_in_no_structure(tmp_path, route, public):
    """Which structure the analyses read is the manifest's to say: with it
    linked out of the study, the saved topology is not read in its place."""
    root = _study(tmp_path)
    _given(root, tmp_path)
    outside = _moved_out(root, tmp_path, "analysis/analysis_manifest.json")

    (status, body), opened = _opened_asking(outside, lambda: _asked(
        root, tmp_path, route, public=public))

    said = json.loads(body)
    assert status == 200 and opened == [] and said["ok"] is False, said
    assert "analysis/analysis_manifest.json was not read" in said["not_read"], said
    assert "against" not in said, said


@pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])
def test_a_copy_not_of_its_atoms_says_why_its_time_points_open_no_frame(tmp_path, public):
    """A copy whose manifest says other atoms than its own trajectory's is
    not re-rooted: its time chart opens no frame, and says the trajectory
    named outside the study was not read (G10 L1)."""
    original, copy = _copied_study(tmp_path)
    manifest = copy / "analysis" / "analysis_manifest.json"
    record = json.loads(manifest.read_text(encoding="utf-8"))
    record["n_atoms"] = 29
    manifest.write_text(json.dumps(record), encoding="utf-8")

    (status, body), opened = _opened_asking(original, lambda: _asked(
        copy, tmp_path, "/api/series?analysis=rmsd", public=public))

    said = json.loads(body)
    assert status == 200 and opened == [] and said["linked"] is False, said
    assert "a file named outside the study was not read" in said["not_read"], said
    assert str(original) not in body.decode("utf-8")


def test_a_structure_named_relative_to_the_study_is_read_in_it(tmp_path):
    """A Config naming its structure by a name relative to the study names a
    file of the study, not of the folder the GUI was started in (G10 L4)."""
    root = _study(tmp_path)
    _given(root, tmp_path)
    (root / "setup" / "input.pdb").rename(root / "given.pdb")
    (root / "resolved_config.yml").write_text(
        "systems: [{system: given.pdb}]\nsetup: {forcefield: amber14}\n", encoding="utf-8")

    (status, body), opened = _opened_asking(tmp_path / "outside", lambda: _asked(
        root, tmp_path, "/api/residue-states?chain=A&resseq=2&resname=HIS"))

    said = json.loads(body)
    assert status == 200 and said["ok"] and "not_read" not in said, said
    assert said["system"] == "given.pdb" and opened == []


# --------------------------------------------------------------------------
# What is offered to run again is what would be run (G12 M2)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("relative", ["analysis/analysis_manifest.json", "resolved_config.yml",
                                      "exploration.log"])
def test_nothing_is_offered_again_that_would_be_refused(tmp_path, relative):
    """With the study's Config, analysis manifest or log linked out of it,
    neither phase is offered: each says what was not read, or for the log
    that the run would write through it, as the POST refuses with it."""
    root = _study(tmp_path)
    (root / "resolved_config.yml").write_text("analysis:\n  include: [rg]\n",
                                              encoding="utf-8")
    (root / "exploration.log").write_text("", encoding="utf-8")
    said = json.loads(_asked(root, tmp_path, "/api/again")[1])
    assert said["analysis"]["can"] and said["report"]["can"], said
    _moved_out(root, tmp_path, relative)

    said = json.loads(_asked(root, tmp_path, "/api/again")[1])

    for phase in ("analysis", "report"):
        assert said[phase]["can"] is False, said
        assert (f"{relative} is a link out of the study, where running it again would write"
                if relative == "exploration.log" else f"{relative} was not read") \
            in said[phase]["why"], said
    assert said["recorded"] == [] and "recorded_said" not in said


def test_the_pages_offer_nothing_to_run_again_from_a_manifest_not_read(tmp_path):
    """Analyze again and Write it again say the manifest was not read, and
    offer nothing ticked to press."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    root = _study(tmp_path)
    (root / "resolved_config.yml").write_text("analysis:\n  include: [rg]\n",
                                              encoding="utf-8")
    _moved_out(root, tmp_path, "analysis/analysis_manifest.json")
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    posted: list[str] = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 1000})
            page.set_default_timeout(60000)
            page.on("request", lambda r: posted.append(r.url) if r.method == "POST" else None)
            page.goto(session.url + "#analysis", wait_until="domcontentloaded")
            page.wait_for_selector("#analysis-again:not([hidden])")
            page.click("#analysis-again")
            page.wait_for_selector("#analysis-again-ask:has-text('was not read')")
            analysis = page.text_content("#analysis-again-ask")
            ticked = page.eval_on_selector_all("#analysis-again-ask input:checked",
                                               "boxes => boxes.length")
            page.goto(session.url + "#report", wait_until="domcontentloaded")
            page.wait_for_selector("#report-write:not([hidden])")
            page.click("#report-write")
            page.wait_for_selector("#report-write-ask:has-text('was not read')")
            report = page.text_content("#report-write-ask")
            confirm = page.query_selector("#report-write-ask .fix-confirm")
            browser.close()
    finally:
        session.server.shutdown()
    assert "analysis/analysis_manifest.json was not read" in analysis and ticked == 0
    assert "analysis/analysis_manifest.json was not read" in report and confirm is None
    assert not any(url.endswith(("/api/again", "/api/report/write")) for url in posted)
