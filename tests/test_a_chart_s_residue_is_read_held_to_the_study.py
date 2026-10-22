"""A chart's residues are named from the study's own records.

An RMSF or order-parameter chart names each point's residue by its chain,
number and name, read from the profile's table and the structure the
analyses selected atoms in. Read for a page, each is held to the study: a
structure or a table linked out of it is not opened where it leads, on this
computer or on a public bind, and the points are left value-only.
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
