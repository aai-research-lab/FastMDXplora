"""A chart's residues are named from the study's own records.

An RMSF or order-parameter chart names each point's residue by its chain,
number and name, read from the profile's table and the structure the
analyses selected atoms in, as do the bars of a ligand's contacts and
interactions and the rows of the secondary-structure map. Read for a page,
each is held to the study: a structure or a table linked out of it, or a
structure the manifest names outside it, is not opened where it leads, on
this computer or on a public bind; the points are left value-only and the
answer says the structure was not read.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_the_study_pages_read_only_the_study import _OPENED, _WATCHED, _ask, _link, _watch

pytest.importorskip("mdtraj")

PROFILE = "chain,residue,insertion,rmsf_nm\nA,1,,0.11\nA,2,,0.12\nA,3,,0.13\n"


def _write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _study(tmp_path: Path) -> Path:
    """A study whose RMSF names three residues of chain A."""
    import mdtraj as md

    root = tmp_path / "study"
    _write(root, "manifest.json", json.dumps({"phases": [{"name": "analysis", "status": "ok"}]}))
    _write(root, "analysis/analysis_manifest.json", json.dumps({
        "phase": "analysis", "n_frames": 3,
        "load_kwargs": {"stride": None, "first": None, "saving_interval_ps": 10.0},
        "results": {"rmsf": {"status": "ok"}}}))
    _write(root, "analysis/rmsf/rmsf.dat", PROFILE)
    _write(root, "analysis/rmsf/options.json", json.dumps(
        {"analysis": "rmsf", "selection": None, "options": {}, "findings": {}}))
    topology = md.Topology()
    chain = topology.add_chain("A")
    for number in (1, 2, 3):
        topology.add_atom("CA", md.element.carbon, topology.add_residue("ALA", chain,
                                                                      resSeq=number))
    path = root / "simulation" / "trajectory_topology.pdb"
    path.parent.mkdir(parents=True, exist_ok=True)
    md.Trajectory([[[0.1 * k, 0.0, 0.0] for k in range(3)]], topology).save_pdb(str(path))
    return root


def _moved_out(root: Path, tmp_path: Path, relative: str) -> Path:
    """``relative`` moved out of the study, a link left where it was."""
    outside = tmp_path / "outside"
    target = outside / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    (root / relative).rename(target)
    _link(root / relative, target)
    return outside


def _series(root: Path, tmp_path: Path, *, public: bool) -> dict:
    status, body = _ask(root, tmp_path, "/api/series?analysis=rmsf", public=public)
    assert status == 200
    return json.loads(body)


@pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])
def test_a_chart_s_residues_are_named_from_the_study_s_structure(tmp_path, public):
    data = _series(_study(tmp_path), tmp_path, public=public)
    assert [entity and entity["resi"] for entity in data["entities"]] == [1, 2, 3]
    assert data["entities"][0] == {"kind": "residue", "chain": "A", "resi": 1, "icode": "",
                                   "resn": "ALA"}


@pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])
def test_a_structure_linked_out_of_the_study_names_no_residue(tmp_path, public):
    root = _study(tmp_path)
    outside = _moved_out(root, tmp_path, "simulation/trajectory_topology.pdb")
    _watch(outside)
    try:
        data = _series(root, tmp_path, public=public)
        opened = sorted(set(_OPENED))
    finally:
        _WATCHED.clear()
    assert opened == []
    assert data["entities"] == [None, None, None]
    assert data["y"] == [0.11, 0.12, 0.13]
    assert "simulation/trajectory_topology.pdb was not read" in data["not_read"]


@pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])
def test_a_structure_the_manifest_names_outside_the_study_is_not_read(tmp_path, public):
    root = _study(tmp_path)
    elsewhere = tmp_path / "outside" / "topology.pdb"
    elsewhere.parent.mkdir(parents=True)
    (root / "simulation" / "trajectory_topology.pdb").rename(elsewhere)
    manifest = root / "analysis" / "analysis_manifest.json"
    record = json.loads(manifest.read_text(encoding="utf-8"))
    record["resolved"] = {"topology": str(elsewhere)}
    manifest.write_text(json.dumps(record), encoding="utf-8")
    _watch(elsewhere.parent)
    try:
        data = _series(root, tmp_path, public=public)
        opened = sorted(set(_OPENED))
    finally:
        _WATCHED.clear()
    assert opened == []
    assert data["entities"] == [None, None, None]
    # Not a link: said as what it is.
    assert data["not_read"] == ("a file named outside the study was not read, so what it "
                                "says is not shown.")
    assert str(tmp_path) not in json.dumps(data)


@pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])
def test_a_study_copied_whole_names_its_residues_from_its_own_structure(tmp_path, public):
    """A study copied whole (`cp -a`) names the structure by the original's
    place, the original still there: the copy's own file in its place is
    read, and the original is not opened."""
    import subprocess

    original = _study(tmp_path / "first")
    manifest = original / "analysis" / "analysis_manifest.json"
    record = json.loads(manifest.read_text(encoding="utf-8"))
    record["resolved"] = {"topology": str(original / "simulation" / "trajectory_topology.pdb")}
    record["n_atoms"] = 3
    manifest.write_text(json.dumps(record), encoding="utf-8")
    copied = tmp_path / "copied"
    subprocess.run(["cp", "-a", str(original), str(copied)], check=True)
    _watch(original)
    try:
        data = _series(copied, tmp_path, public=public)
        opened = sorted(set(_OPENED))
    finally:
        _WATCHED.clear()
    assert opened == []
    assert [entity and entity["resi"] for entity in data["entities"]] == [1, 2, 3]
    assert "not_read" not in data


#: Each figure that names residues, with what it is read from: the name of
#: residue A:1 in each, as the analysis writes it.
FIGURES = {
    "pl_contacts": ("analysis/pl_contacts/pl_contacts_per_residue.csv",
                    "residue,contact_frequency\nALA1,0.5\nALA2,0.25\n"),
    "pl_interactions": ("analysis/pl_interactions/pl_interactions.dat",
                        "kind,residue,occupancy\nhbond,ALA1,0.5\nhbond,ALA2,0.25\n"),
    "ss": ("analysis/ss/ss.dat", "frame,ALA1,ALA2,ALA3\n0,H,H,C\n1,H,E,C\n"),
}


def _residues_of(answer: dict) -> list:
    """The residue each bar or row of a figure names, None where it names none."""
    return ([bar["entity"] for bar in answer["bars"]] if "bars" in answer
            else answer["entities"])


def _figure(root: Path, tmp_path: Path, name: str, *, public: bool) -> dict:
    status, body = _ask(root, tmp_path, f"/api/figure-data?figure=analysis/{name}/{name}.png",
                        public=public)
    assert status == 200
    return json.loads(body)


def _with_figure(tmp_path: Path, name: str) -> Path:
    root = _study(tmp_path)
    _write(root, *FIGURES[name])
    return root


@pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])
@pytest.mark.parametrize("name", sorted(FIGURES))
def test_a_figure_s_residues_are_named_from_the_study_s_structure(tmp_path, name, public):
    data = _figure(_with_figure(tmp_path, name), tmp_path, name, public=public)
    assert data["ok"] is True, data
    assert _residues_of(data)[0] == {"kind": "residue", "chain": "A", "resi": 1, "icode": "",
                                     "resn": "ALA"}
    assert "not_read" not in data


@pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])
@pytest.mark.parametrize("name", sorted(FIGURES))
def test_a_figure_s_structure_linked_out_of_the_study_is_not_read(tmp_path, name, public):
    root = _with_figure(tmp_path, name)
    outside = _moved_out(root, tmp_path, "simulation/trajectory_topology.pdb")
    _watch(outside)
    try:
        data = _figure(root, tmp_path, name, public=public)
        opened = sorted(set(_OPENED))
    finally:
        _WATCHED.clear()
    assert opened == []
    assert data["ok"] is True, data
    assert set(map(str, _residues_of(data))) == {"None"}
    assert "simulation/trajectory_topology.pdb was not read" in data["not_read"]
    assert data["not_read"] in data["note"]


@pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])
@pytest.mark.parametrize("name", sorted(FIGURES))
def test_a_figure_s_structure_the_manifest_names_outside_is_not_read(tmp_path, name, public):
    root = _with_figure(tmp_path, name)
    elsewhere = tmp_path / "outside" / "topology.pdb"
    elsewhere.parent.mkdir(parents=True)
    (root / "simulation" / "trajectory_topology.pdb").rename(elsewhere)
    manifest = root / "analysis" / "analysis_manifest.json"
    record = json.loads(manifest.read_text(encoding="utf-8"))
    record["resolved"] = {"topology": str(elsewhere)}
    manifest.write_text(json.dumps(record), encoding="utf-8")
    _watch(elsewhere.parent)
    try:
        data = _figure(root, tmp_path, name, public=public)
        opened = sorted(set(_OPENED))
    finally:
        _WATCHED.clear()
    assert opened == []
    assert set(map(str, _residues_of(data))) == {"None"}
    assert "was not read" in data["not_read"]
    assert str(tmp_path) not in json.dumps(data)


def test_a_structure_the_manifest_names_that_is_nowhere_is_passed_over(tmp_path):
    """A study moved since it was analysed: the manifest's structure is
    nowhere, so the one written beside the trajectory names the residues,
    and nothing is said not read."""
    root = _study(tmp_path)
    manifest = root / "analysis" / "analysis_manifest.json"
    record = json.loads(manifest.read_text(encoding="utf-8"))
    record["resolved"] = {"topology": str(tmp_path / "moved" / "topology.pdb")}
    manifest.write_text(json.dumps(record), encoding="utf-8")
    data = _series(root, tmp_path, public=True)
    assert [entity and entity["resi"] for entity in data["entities"]] == [1, 2, 3]
    assert "not_read" not in data
