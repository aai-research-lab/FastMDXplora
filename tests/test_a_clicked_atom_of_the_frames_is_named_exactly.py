"""An atom clicked in the frames played is named by its place in the topology.

The selection for a clicked atom went to the server as its chain's letter,
its residue's number and its name, and the server found the residue by
them. Where two chains share a letter, it could not say which and refused;
where two residues of one chain share a number (184 and 184A), it gave a
selection of both. The frames the viewer plays are atoms of the topology
the analyses read, in its order, so the atom clicked is now sent by its
index among them as well, and the residue is the one clicked: by its
chain's index, and by `resid` where its number and name would select
another residue too.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")

from fastmdxplora.gui.selection import selection_for  # noqa: E402
from fastmdxplora.gui.trajectory_frames import frames_info  # noqa: E402


def _atom(serial, name, resname, chain, number, code, x, record="ATOM  "):
    return (f"{record}{serial:5d} {name:<4} {resname:<3} {chain}{number:4d}{code}   "
            f"{x:8.3f}{0.0:8.3f}{0.0:8.3f}  1.00  0.00          {name[0]:>2}")


@pytest.fixture
def study(tmp_path) -> Path:
    """Chain A: GLY 184, 184A and 185; a second chain also lettered A with
    its own GLY 184; a ligand; a water. Played as three frames."""
    root = tmp_path / "study"
    (root / "simulation").mkdir(parents=True)
    lines = ["CRYST1   60.000   60.000   60.000  90.00  90.00  90.00 P 1           1"]
    serial, x = 1, 0.0
    for residues in (((184, " "), (184, "A"), (185, " ")), ((184, " "),)):
        for number, code in residues:
            for name in ("N", "CA", "C", "O"):
                lines.append(_atom(serial, name, "GLY", "A", number, code, x))
                serial, x = serial + 1, x + 1.3
        lines.append("TER")
    lines += [_atom(serial, "C1", "LIG", "B", 1, " ", 30.0, "HETATM"),
              _atom(serial + 1, "C2", "LIG", "B", 1, " ", 31.5, "HETATM"),
              _atom(serial + 2, "O", "HOH", "C", 1, " ", 40.0, "HETATM"), "END"]
    path = root / "simulation" / "trajectory_topology.pdb"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (root / "setup").mkdir()
    (root / "setup" / "topology.pdb").write_text(path.read_text(encoding="utf-8"),
                                                 encoding="utf-8")
    loaded = md.load_pdb(str(path))
    md.Trajectory(np.repeat(loaded.xyz, 3, axis=0), loaded.topology,
                  unitcell_lengths=np.full((3, 3), 6.0),
                  unitcell_angles=np.full((3, 3), 90.0)).save_dcd(
        str(root / "simulation" / "production.dcd"))
    said = frames_info(root)
    assert said["available"] and said["n_atoms"] == 18
    return root


def _selects(root: Path, selection: str) -> list[int]:
    topology = md.load_topology(str(root / "simulation" / "trajectory_topology.pdb"))
    return topology.select(selection).tolist()


def test_the_frames_say_which_atoms_of_which_topology_they_are(study):
    said = json.loads((study / "simulation" / "frames_index.json").read_text(encoding="utf-8"))
    assert said["source_topology"] == "simulation/trajectory_topology.pdb"
    assert said["shown"] == "not water"


def test_a_residue_sharing_its_number_in_its_chain_is_selected_by_its_place(study):
    found = selection_for(study, chain="A", resseq=184, resname="GLY", atom="CA",
                          frames_atom=5)
    assert found["ok"]
    assert found["residue"] == {"selection": "resid 1", "atoms": 4}
    assert _selects(study, found["atom"]["selection"]) == [5]
    # By letter and number alone it cannot be told, as before.
    assert not selection_for(study, chain="A", resseq=184, resname="GLY", atom="CA")["ok"]


def test_a_chain_sharing_its_letter_is_selected_by_its_index(study):
    found = selection_for(study, chain="A", resseq=184, resname="GLY", atom="CA",
                          frames_atom=13)
    assert found["residue"]["selection"] == "chainid 1 and resSeq 184"
    assert _selects(study, found["atom"]["selection"]) == [13]


def test_a_ligand_is_unchanged(study):
    found = selection_for(study, chain="B", resseq=1, resname="LIG", atom="C2", frames_atom=17)
    assert found["atom"]["selection"] == "chainid 2 and resSeq 1 and name C2"


@pytest.mark.parametrize("frames_atom", [4, 99, -1, "x"])
def test_an_index_that_is_not_the_atom_named_is_not_used(study, frames_atom):
    """Index 4 is 184A's N, not its CA: frames written from an older topology."""
    found = selection_for(study, chain="A", resseq=185, resname="GLY", atom="CA",
                          frames_atom=frames_atom)
    assert found["atom"]["selection"] == "chainid 0 and resSeq 185 and name CA"


def test_frames_of_another_topology_are_not_used(study):
    index = study / "simulation" / "frames_index.json"
    said = json.loads(index.read_text(encoding="utf-8"))
    index.write_text(json.dumps(dict(said, source_topology="setup/topology.pdb")),
                     encoding="utf-8")
    assert not selection_for(study, chain="A", resseq=184, resname="GLY", atom="CA",
                             frames_atom=5)["ok"]


def test_frames_written_before_they_said_whose_they_are_are_not_used(study):
    index = study / "simulation" / "frames_index.json"
    said = json.loads(index.read_text(encoding="utf-8"))
    index.write_text(json.dumps({k: v for k, v in said.items()
                                 if k not in ("source_topology", "shown")}), encoding="utf-8")
    assert not selection_for(study, chain="A", resseq=184, resname="GLY", atom="CA",
                             frames_atom=5)["ok"]


def test_a_topology_outside_the_study_is_named_by_its_whole_path(study, tmp_path):
    """A joined study's topology can be anywhere: it is the joined frames'."""
    elsewhere = tmp_path / "elsewhere" / "joined_topology.pdb"
    elsewhere.parent.mkdir()
    elsewhere.write_text((study / "simulation" / "trajectory_topology.pdb")
                         .read_text(encoding="utf-8"), encoding="utf-8")
    (study / "joined").mkdir()
    (study / "joined" / "production.dcd").write_bytes(
        (study / "simulation" / "production.dcd").read_bytes())
    (study / "joined" / "joined.json").write_text(json.dumps({"topology": str(elsewhere)}),
                                                 encoding="utf-8")
    said = frames_info(study)
    assert said["source_topology"] == str(elsewhere.resolve())
    (study / "analysis").mkdir()
    (study / "analysis" / "analysis_manifest.json").write_text(json.dumps(
        {"resolved": {"topology": str(elsewhere)}}), encoding="utf-8")
    found = selection_for(study, chain="A", resseq=184, resname="GLY", atom="CA",
                          frames_atom=5)
    assert found["atom"]["selection"] == "resid 1 and name CA"


def test_frames_of_water_alone_are_every_atom(tmp_path):
    root = tmp_path / "water"
    (root / "simulation").mkdir(parents=True)
    lines = [_atom(1 + 3 * i + k, name, "HOH", "W", i + 1, " ", 3.0 * i + 0.9 * k, "HETATM")
             for i in range(4) for k, name in enumerate(("O", "H1", "H2"))]
    path = root / "simulation" / "trajectory_topology.pdb"
    path.write_text("\n".join(lines) + "\nEND\n", encoding="utf-8")
    loaded = md.load_pdb(str(path))
    md.Trajectory(np.repeat(loaded.xyz, 2, axis=0), loaded.topology).save_dcd(
        str(root / "simulation" / "production.dcd"))
    said = frames_info(root)
    assert said["shown"] == "all" and said["n_atoms"] == 12


def test_the_server_and_a_click_in_the_frames_send_it(study):
    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    asked: list[str] = []
    try:
        query = urllib.parse.urlencode({"chain": "A", "resseq": 184, "resname": "GLY",
                                        "atom": "CA", "frames_atom": 5})
        served = json.loads(urllib.request.urlopen(
            f"{session.url.rstrip('/')}/api/selection?{query}", timeout=30).read())
        pytest.importorskip("playwright.sync_api")
        from playwright.sync_api import sync_playwright

        state = "window.FastMDXMoleculeViewer.STATE"
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.set_default_timeout(60000)
            page.on("request", lambda request: asked.append(request.url)
                    if "/api/selection" in request.url else None)
            page.goto(session.url + "#viewer", wait_until="domcontentloaded")
            if not page.evaluate("() => !!document.createElement('canvas').getContext('webgl')"):
                pytest.skip("this browser has no WebGL, so the viewer cannot render")
            page.wait_for_function(f"() => {state}.model")
            # The frames, as Play loads them.
            page.evaluate("async () => window.FastMDXMoleculeViewer.loadPlayback("
                          "await (await fetch('/api/frames-info')).json())")
            page.wait_for_function(f"() => {state}.model && {state}.model.of === 'frames'")
            page.click('.info-tab[data-tab="selection"]')
            page.evaluate(f"() => {state}.engine.click(5)")
            page.wait_for_selector('#selection-strings .selection-string[data-of="atom"]')
            atom = page.text_content('#selection-strings [data-of="atom"] code')
            browser.close()
    finally:
        session.server.shutdown()
    assert served["atom"]["selection"] == "resid 1 and name CA"
    assert asked and "frames_atom=5" in asked[-1]
    assert atom == "resid 1 and name CA"
