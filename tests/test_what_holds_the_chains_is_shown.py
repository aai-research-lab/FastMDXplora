"""What holds the protein's chains together is shown frame by frame.

The hydrogen bonds and salt bridges between chains in the frames the Viewer
plays, by the criteria the interactions analysis applies to a ligand: a
donor and acceptor in different chains within 3.5 A with the angle at the
hydrogen above 120 degrees; charged groups' centres within 4.5 A. Each is
given with the frames it was present in and its atoms by the Viewer's
numbering, listed in the panel and shown in the structure for the frame
shown.

Two short chains placed by hand, so what is present in each frame is known:
a backbone N-H of chain A against a carbonyl of chain B (frame 0 and 1
bonded; frame 2 too far; frame 3 near enough but bent to 112 degrees), and
a lysine of A against an aspartate of B (frames 0 to 2), with an aspartate
of A beside the same lysine, which joins nothing between chains.
"""

from __future__ import annotations

import json
import os
import urllib.request

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")

from fastmdxplora.gui.chain_contacts import chain_contacts  # noqa: E402
from fastmdxplora.gui.trajectory_frames import frames_info  # noqa: E402

ATOMS = {
    # chain, residue, number: the atoms and where they are in frame 0 (nm)
    ("A", "GLY", 1): {"N": (0.0, 0.0, 0.0), "H": (0.1, 0.0, 0.0), "CA": (-0.1, 0.1, 0.0),
                      "C": (-0.2, 0.1, 0.1), "O": (-0.3, 0.1, 0.2)},
    ("A", "LYS", 2): {"N": (-0.2, 0.3, 0.6), "CA": (-0.2, 0.4, 0.7), "C": (-0.2, 0.5, 0.8),
                      "O": (-0.2, 0.6, 0.9), "NZ": (2.0, 0.0, 0.0)},
    ("A", "ASP", 3): {"N": (-0.2, 0.7, 1.3), "CA": (-0.2, 0.8, 1.4), "C": (-0.2, 0.9, 1.5),
                      "O": (-0.2, 1.0, 1.6), "OD1": (1.75, 0.1, 0.0), "OD2": (1.75, -0.1, 0.0)},
    ("B", "GLY", 1): {"N": (0.6, -0.4, -0.5), "CA": (0.5, -0.3, -0.4), "C": (0.4, -0.2, -0.3),
                      "O": (0.29, 0.0, 0.0)},
    ("B", "ASP", 2): {"N": (2.6, -0.6, -0.6), "CA": (2.6, -0.5, -0.7), "C": (2.6, -0.4, -0.8),
                      "O": (2.6, -0.3, -0.9), "OD1": (2.3, 0.1, 0.0), "OD2": (2.3, -0.1, 0.0)},
}
ELEMENTS = {"N": "nitrogen", "H": "hydrogen", "C": "carbon", "O": "oxygen"}


def _system(atoms_of: dict | None = None) -> tuple[md.Topology, np.ndarray, dict]:
    topology = md.Topology()
    chains = {}
    xyz, where = [], {}
    for (chain_id, name, number), atoms in (atoms_of or ATOMS).items():
        if chain_id not in chains:
            chains[chain_id] = topology.add_chain(chain_id=chain_id)
        residue = topology.add_residue(name, chains[chain_id], resSeq=number)
        for atom, position in atoms.items():
            added = topology.add_atom(atom, getattr(md.element, ELEMENTS[atom[0]]), residue)
            where[(chain_id, number, atom)] = added.index
            xyz.append(position)
    topology.create_standard_bonds()
    return topology, np.array(xyz), where


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    root = tmp_path_factory.mktemp("chains") / "study"
    (root / "simulation").mkdir(parents=True)
    topology, base, where = _system()
    oxygen = where[("B", 1, "O")]
    acid = [where[("B", 2, name)] for name in ("OD1", "OD2")]
    frames = []
    for frame in range(4):
        moved = base.copy()
        if frame == 2:
            moved[oxygen] = (0.6, 0.0, 0.0)              # too far: 6 A
        if frame == 3:
            moved[oxygen] = (0.2, 0.25, 0.0)             # 3.2 A but bent at the H
            moved[acid] += (0.7, 0.0, 0.0)               # the bridge broken
        frames.append(moved)
    trajectory = md.Trajectory(np.array(frames, dtype=np.float32), topology)
    trajectory[0].save_pdb(str(root / "simulation" / "trajectory_topology.pdb"))
    trajectory.save_dcd(str(root / "simulation" / "production.dcd"))
    (root / "simulation" / "live_status.json").write_text(json.dumps(
        {"status": "completed", "stage": "production"}), encoding="utf-8")
    assert frames_info(root)["available"]
    return root, where


def test_a_hydrogen_bond_and_a_salt_bridge_between_the_chains(built):
    study, where = built
    said = chain_contacts(study)
    assert said["ok"] and said["chains"] == 2 and said["n_frames"] == 4 and said["total"] == 2
    played = md.load_topology(str(study / "simulation" / "frames_topology.pdb"))
    by_kind = {c["kind"]: c for c in said["contacts"]}
    bond = by_kind["hydrogen_bond"]
    donor, acceptor = (played.atom(i) for i in bond["atoms"])
    assert (donor.name, donor.residue.resSeq, acceptor.name, acceptor.residue.resSeq) == \
        ("N", 1, "O", 1)
    assert donor.residue.chain.index != acceptor.residue.chain.index
    assert bond["residues"] == ["A:GLY1", "B:GLY1"]
    assert bond["episodes"] == [[0, 1]] and bond["occupancy"] == 0.5
    bridge = by_kind["salt_bridge"]
    plus, minus = (played.atom(i) for i in bridge["atoms"])
    assert (plus.residue.name, plus.name, minus.residue.name, minus.residue.chain.index) == \
        ("LYS", "NZ", "ASP", 1)
    assert bridge["episodes"] == [[0, 2]] and bridge["occupancy"] == 0.75
    # The most often present first; the aspartate in the lysine's own chain
    # joins nothing between chains.
    assert [c["kind"] for c in said["contacts"]] == ["salt_bridge", "hydrogen_bond"]
    assert said["notes"] == []
    assert "3.5" in said["criteria"]["hydrogen_bond"] and "4.5" in said["criteria"]["salt_bridge"]


def test_written_once_and_again_after_the_frames(built):
    study, _ = built
    target = study / "simulation" / "chain_contacts.json"
    chain_contacts(study)
    written = target.stat().st_mtime_ns
    assert chain_contacts(study, most=1)["contacts"] == chain_contacts(study)["contacts"][:1]
    assert target.stat().st_mtime_ns == written
    frames = study / "simulation" / "frames.dcd"
    later = written + 10_000_000_000
    os.utime(frames, ns=(later, later))
    chain_contacts(study)
    assert target.stat().st_mtime_ns > written


def test_one_chain_no_hydrogens_and_no_frames_are_said(tmp_path):
    assert chain_contacts(tmp_path) == {
        "ok": False, "reason": "There are no frames to look between chains in yet."}
    root = tmp_path / "one"
    (root / "simulation").mkdir(parents=True)
    topology, base, _ = _system()
    single = topology.subset(topology.select("chainid 0"))
    trajectory = md.Trajectory(np.array([base[topology.select("chainid 0")]] * 2,
                                        dtype=np.float32), single)
    trajectory[0].save_pdb(str(root / "simulation" / "trajectory_topology.pdb"))
    trajectory.save_dcd(str(root / "simulation" / "production.dcd"))
    (root / "simulation" / "live_status.json").write_text(json.dumps(
        {"status": "completed", "stage": "production"}), encoding="utf-8")
    assert chain_contacts(root) == {"ok": False, "reason": "The protein has one chain, so "
                                    "nothing holds chains together."}
    dry = tmp_path / "dry"
    (dry / "simulation").mkdir(parents=True)
    heavy = topology.select("not element H")
    trajectory = md.Trajectory(np.array([base[heavy]] * 2, dtype=np.float32),
                               topology.subset(heavy))
    trajectory[0].save_pdb(str(dry / "simulation" / "trajectory_topology.pdb"))
    trajectory.save_dcd(str(dry / "simulation" / "production.dcd"))
    (dry / "simulation" / "live_status.json").write_text(json.dumps(
        {"status": "completed", "stage": "production"}), encoding="utf-8")
    said = chain_contacts(dry)
    assert said["ok"] and [c["kind"] for c in said["contacts"]] == ["salt_bridge"]
    assert said["notes"] == ["The frames have no hydrogens on their donors, so no hydrogen "
                             "bond can be found by its angle."]


def test_a_record_that_cannot_be_read_is_written_again(built):
    study, _ = built
    target = study / "simulation" / "chain_contacts.json"
    chain_contacts(study)
    target.write_text("{not json", encoding="utf-8")
    # Newer than the frames, so it is read rather than passed over.
    later = (study / "simulation" / "frames.dcd").stat().st_mtime_ns + 1_000_000_000
    os.utime(target, ns=(later, later))
    assert chain_contacts(study)["total"] == 2
    assert json.loads(target.read_text(encoding="utf-8"))["ok"]


def test_a_hydrogen_bonded_to_nothing_is_said_and_the_bridges_kept(tmp_path):
    """A hydrogen the topology does not bond cannot be seen to donate, and
    the interactions analysis refuses to count one direction as both."""
    root = tmp_path / "orphan"
    (root / "simulation").mkdir(parents=True)
    atoms = {key: dict(value) for key, value in ATOMS.items()}
    atoms[("A", "GLY", 1)]["HX"] = (0.0, -0.5, -0.5)
    topology, base, _ = _system(atoms)
    trajectory = md.Trajectory(np.array([base] * 2, dtype=np.float32), topology)
    trajectory[0].save_pdb(str(root / "simulation" / "trajectory_topology.pdb"))
    trajectory.save_dcd(str(root / "simulation" / "production.dcd"))
    (root / "simulation" / "live_status.json").write_text(json.dumps(
        {"status": "completed", "stage": "production"}), encoding="utf-8")
    said = chain_contacts(root)
    assert said["ok"] and [c["kind"] for c in said["contacts"]] == ["salt_bridge"]
    assert len(said["notes"]) == 1 and "bonded to nothing" in said["notes"][0]


def test_the_viewer_lists_them_and_shows_those_of_the_frame(built):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    study, _ = built
    (study / "setup").mkdir(exist_ok=True)
    (study / "setup" / "topology.pdb").write_text(
        (study / "simulation" / "trajectory_topology.pdb").read_text(encoding="utf-8"),
        encoding="utf-8")
    state = "window.FastMDXMoleculeViewer.STATE"
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        sent = json.loads(urllib.request.urlopen(
            session.url.rstrip("/") + "/api/chain-contacts", timeout=60).read())
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#viewer", wait_until="domcontentloaded")
            if not page.evaluate("() => !!document.createElement('canvas').getContext('webgl')"):
                pytest.skip("this browser has no WebGL, so the viewer cannot render")
            page.wait_for_function(f"() => window.FastMDXMoleculeViewer && {state}.model")
            page.evaluate("async () => window.FastMDXMoleculeViewer.loadPlayback("
                          "await (await fetch('/api/frames-info')).json())")
            page.wait_for_function(f"() => {state}.model && {state}.model.of === 'frames'")
            seen = {}
            for frame in (0, 2, 3):
                page.evaluate(f"() => window.FastMDXMoleculeViewer.movie.showFrame({frame})")
                page.wait_for_function(
                    f"() => document.getElementById('chain-contacts-now').textContent"
                    f".endsWith('frame {frame}')")
                page.wait_for_function(
                    "(n) => window.FastMDXMoleculeViewer.STATE.engine"
                    ".interactionsHeld('chains') === n",
                    arg={0: 2, 2: 1, 3: 0}[frame])
                seen[frame] = {
                    "said": page.text_content("#chain-contacts-now"),
                    "present": page.eval_on_selector_all(
                        "#chain-contacts-list .chain-contact.is-present",
                        "rows => rows.map(r => r.textContent)"),
                }
            rows = page.eval_on_selector_all("#chain-contacts-list .chain-contact",
                                              "rows => rows.map(r => r.textContent)")
            visible = page.is_visible("#side-chains")
            # Taken out of the structure, and the ligand's lines are kept apart.
            page.evaluate("() => window.FastMDXMoleculeViewer.movie.showFrame(0)")
            page.uncheck("#chain-contacts-shown")
            page.wait_for_function("() => window.FastMDXMoleculeViewer.STATE.engine"
                                   ".interactionsHeld('chains') === 0")
            ligand = page.evaluate("() => window.FastMDXMoleculeViewer.STATE.engine"
                                   ".interactionsHeld('ligand')")
            browser.close()
    finally:
        session.server.shutdown()
    assert sent["ok"] and sent["total"] == 2
    assert visible
    assert rows == ["A:LYS2 ↔ B:ASP275%", "A:GLY1 ↔ B:GLY150%"]
    assert seen[0] == {"said": "2 of 2 present in frame 0", "present": rows}
    assert seen[2] == {"said": "1 of 2 present in frame 2", "present": rows[:1]}
    assert seen[3] == {"said": "0 of 2 present in frame 3", "present": []}
    assert ligand == 0
    assert errors == []
