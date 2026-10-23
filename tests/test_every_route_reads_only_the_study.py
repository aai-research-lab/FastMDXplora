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
