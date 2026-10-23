"""What the Viewer computes from the frames it plays keeps to the study.

The frames the browser plays (`simulation/frames.dcd` and its topology) are
read by more than the playback: the frames superposed, a motion, the
backbone angles, the contacts between chains and between residues, the
states, two states compared, the maps of the ligand and the water, the
water sites, the pocket's room, the secondary structure of the frames, a
typed selection in them and the interactions over them. Each is read held
to the study and only of the frames it plays: with the frames or their
topology linked out of it, none is read where it leads, on this computer or
on a public bind, and each answer says it was not read. MDTraj reads a DCD
in C, where the audit hook on `open` does not see it, so each answer is
checked as well.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from tests.test_the_study_pages_read_only_the_study import _OPENED, _WATCHED, _ask, _link, _watch

md = pytest.importorskip("mdtraj")

#: Each route a Viewer panel asks, as it asks it, and whether a public bind
#: answers it.
ASKED = {
    "/api/frames-superposed?on=backbone": True,
    "/api/motion": True,
    "/api/backbone-angles": True,
    "/api/chain-contacts": True,
    "/api/states": True,
    "/api/state-difference?a=0&b=3": True,
    "/api/contact-map": True,
    "/api/contact-pair?a=0&b=3": True,
    "/api/occupancy?of=ligand&ligand=LIG": True,
    "/api/water-sites": True,
    "/api/pocket-volume?ligand=LIG": True,
    "/api/secondary-structure?of=frames": True,
    "/api/viewer-atoms?of=frames&expression=protein": True,
    "/api/interactions-over-frames": True,
    "/api/beside?path=elsewhere": False,
}
#: What a page reads as bytes from what was computed so.
SENT = ["/structure/occupancy.dx?of=ligand&ligand=LIG&cutoff=5",
        "/structure/pocket.dx?ligand=LIG&cutoff=5&frame=0"]


def _study(tmp_path: Path) -> Path:
    """A finished study of two chains of alanines and a ligand, twelve
    frames of each atom moving about its place, its browser's frames
    written."""
    from fastmdxplora.gui.trajectory_frames import frames_info

    root = tmp_path / "study"
    simulation = root / "simulation"
    simulation.mkdir(parents=True)
    topology = md.Topology()
    elements = {"N": md.element.nitrogen, "CA": md.element.carbon, "C": md.element.carbon,
                "O": md.element.oxygen, "CB": md.element.carbon}
    for name in ("A", "B"):
        chain = topology.add_chain(name)
        for number in range(1, 5):
            residue = topology.add_residue("ALA", chain, resSeq=number)
            for atom, element in elements.items():
                topology.add_atom(atom, element, residue)
    ligand = topology.add_residue("LIG", topology.add_chain("C"), resSeq=1)
    for atom in ("C1", "C2", "C3"):
        topology.add_atom(atom, md.element.carbon, ligand)
    places = np.array([[0.15 * (k % 5), 0.38 * (k // 5 % 4), 0.5 * (k // 20)]
                       for k in range(topology.n_atoms)], dtype=np.float32)
    moved = np.random.default_rng(7).normal(0.0, 0.03, (12, topology.n_atoms, 3))
    frames = md.Trajectory((places[None] + moved).astype(np.float32), topology)
    frames[0].save_pdb(str(simulation / "trajectory_topology.pdb"))
    frames.save_dcd(str(simulation / "production.dcd"))
    (simulation / "live_status.json").write_text(json.dumps(
        {"status": "completed", "stage": "production"}), encoding="utf-8")
    (simulation / "simulation_parameters.json").write_text(json.dumps({
        "parameters": {"integrator": "verlet", "timestep_fs": 2.0},
        "resolved": {"trajectory_interval_steps": 500, "production_steps": 6000}}),
        encoding="utf-8")
    assert frames_info(root)["playback_available"]
    return root


def _moved_out(root: Path, tmp_path: Path, relative: str) -> Path:
    """``relative`` moved out of the study, a link to it left in its place:
    what it leads to is the study's own frames, as readable as they were."""
    target = tmp_path / "outside" / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    (root / relative).rename(target)
    _link(root / relative, target)
    return target


@pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])
@pytest.mark.parametrize("relative", ["simulation/frames.dcd", "simulation/frames_topology.pdb"])
@pytest.mark.parametrize("route", list(ASKED) + SENT)
def test_frames_linked_out_of_the_study_are_not_computed_from(tmp_path, route, relative,
                                                             public):
    """Each route answers from the study's own frames; with the frames or
    their topology linked out, it reads neither and says so."""
    if public and not ASKED.get(route, True):
        pytest.skip("not answered beyond loopback")
    root = _study(tmp_path)
    if route.startswith("/structure/occupancy"):
        # The map written from the study's own frames, before they were
        # linked out of it.
        assert json.loads(_ask(root, tmp_path, "/api/occupancy?of=ligand&ligand=LIG",
                               public=public)[1])["ok"]
    if "beside" in route:
        (tmp_path / "elsewhere").mkdir()
        route = route.replace("elsewhere", str(_study(tmp_path / "elsewhere")))
    # The study's own frames are computed from, and nothing is said not read.
    status, body = _ask(root, tmp_path, route, public=public)
    assert status == 200 and (route.startswith("/structure/")
                              or "not_read" not in json.loads(body)), body[:300]
    outside = _moved_out(root, tmp_path, relative)
    _watch(outside.parent)
    try:
        status, body = _ask(root, tmp_path, route, public=public)
        opened = sorted(set(_OPENED))
    finally:
        _WATCHED.clear()
    assert opened == []
    if route.startswith("/structure/"):
        assert status == 404
        return
    said = json.loads(body)
    assert status == 200 and not said.get("ok") and not said.get("available"), said
    assert f"{relative} was not read" in said["not_read"], said
    assert str(tmp_path / "outside") not in body.decode("utf-8")
