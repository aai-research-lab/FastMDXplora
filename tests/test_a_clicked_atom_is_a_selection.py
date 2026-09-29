"""A clicked atom is given as a selection a Config can use.

Clicking an atom in the viewer said its residue, chain and name and left the
selection to be written by hand, in a language where `resid 189` and
`resSeq 189` name different residues in most deposited structures. The
Selection tab now gives the selection for the residue and for the atom, by
`resSeq` and MDTraj's chain index, each checked against the topology the
analyses read, with a button to copy it.
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")

from fastmdxplora.gui.selection import selection_for, topology_the_analyses_read  # noqa: E402


def _topology(path: Path, *, chains=(("A", [("ALA", 1), ("GLY", 2)]),
                                     ("B", [("LIG", 1)]))) -> Path:
    topology = md.Topology()
    xyz = []
    for letter, residues in chains:
        chain = topology.add_chain(chain_id=letter)
        for name, number in residues:
            residue = topology.add_residue(name, chain, resSeq=number)
            for atom in (("CA", "O5'") if name == "LIG" else ("N", "CA", "C")):
                topology.add_atom(atom, md.element.carbon, residue)
                xyz.append([len(xyz) * 0.15, 0.0, 0.0])
    path.parent.mkdir(parents=True, exist_ok=True)
    md.Trajectory(np.array(xyz)[None], topology).save_pdb(str(path))
    return path


@pytest.fixture
def study(tmp_path) -> Path:
    _topology(tmp_path / "simulation" / "trajectory_topology.pdb")
    return tmp_path


class TestTheSelection:
    def test_a_residue_and_its_atom_by_number_and_chain(self, study):
        found = selection_for(study, chain="A", resseq=2, resname="GLY", atom="CA")
        assert found == {"ok": True, "against": "trajectory_topology.pdb",
                         "residue": {"selection": "chainid 0 and resSeq 2", "atoms": 3},
                         "atom": {"selection": "chainid 0 and resSeq 2 and name CA", "atoms": 1}}
        for check in (found["residue"], found["atom"]):
            topology = md.load_topology(str(study / "simulation" / "trajectory_topology.pdb"))
            assert len(topology.select(check["selection"])) == check["atoms"]

    def test_the_chain_is_found_by_name_where_the_letter_does_not(self, study):
        """The ligand is residue 1 of its own chain, as the protein's first
        residue is of its: the chain comes from the residue's name."""
        found = selection_for(study, chain="Q", resseq=1, resname="LIG", atom="CA")
        assert found["residue"]["selection"] == "chainid 1 and resSeq 1"

    def test_one_chain_needs_no_chain(self, tmp_path):
        _topology(tmp_path / "simulation" / "trajectory_topology.pdb",
                  chains=(("A", [("ALA", 1), ("GLY", 2)]),))
        found = selection_for(tmp_path, chain="A", resseq=1, resname="ALA", atom="N")
        assert found["residue"]["selection"] == "resSeq 1"
        assert found["atom"]["selection"] == "resSeq 1 and name N"

    @pytest.mark.filterwarnings("ignore:WARNING. two consecutive residues with same number")
    def test_a_shared_number_in_one_chain_takes_the_name(self, tmp_path):
        _topology(tmp_path / "simulation" / "trajectory_topology.pdb",
                  chains=(("A", [("ALA", 1), ("LIG", 1)]),))
        found = selection_for(tmp_path, chain="A", resseq=1, resname="LIG", atom="CA")
        assert found["residue"]["selection"] == "resSeq 1 and resname LIG"
        assert found["residue"]["atoms"] == 2

    def test_a_name_with_a_prime_is_quoted(self, study):
        found = selection_for(study, chain="B", resseq=1, resname="LIG", atom="O5'")
        assert found["atom"] == {"selection": "chainid 1 and resSeq 1 and name \"O5'\"", "atoms": 1}

    def test_what_the_analyses_do_not_read_is_said(self, study):
        water = selection_for(study, chain="C", resseq=4, resname="HOH", atom="O")
        assert not water["ok"] and "simulation.save_selection" in water["reason"]
        missing = selection_for(study, chain="A", resseq=9, resname="TRP", atom="CA")
        assert missing == {"ok": False, "against": "trajectory_topology.pdb",
                           "reason": "TRP 9 is not in trajectory_topology.pdb, the topology "
                                     "the analyses read"}

    def test_nothing_that_is_not_a_name_is_used(self, study):
        assert not selection_for(study, chain="A", resseq="x", resname="ALA", atom="CA")["ok"]
        assert not selection_for(study, chain="A", resseq=1, resname="ALA or all",
                                 atom="CA")["ok"]
        assert not selection_for(study, chain="A", resseq=1, resname="ALA",
                                 atom="CA or all")["ok"]

    def test_the_topology_the_analysis_recorded_comes_first(self, study, tmp_path):
        other = _topology(tmp_path / "elsewhere" / "top.pdb")
        (study / "analysis").mkdir()
        (study / "analysis" / "analysis_manifest.json").write_text(
            json.dumps({"resolved": {"topology": str(other)}}), encoding="utf-8")
        assert topology_the_analyses_read(study) == other
        assert topology_the_analyses_read(tmp_path / "nothing") is None


def test_it_is_served_and_answered_beyond_loopback(study) -> None:
    from fastmdxplora.gui.server import GETS_ANSWERED_BEYOND_LOOPBACK, start_dashboard_session

    assert "/api/selection" in GETS_ANSWERED_BEYOND_LOOPBACK
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        answer = json.loads(urllib.request.urlopen(
            session.url + "/api/selection?chain=A&resseq=2&resname=GLY&atom=CA",
            timeout=10).read())
    finally:
        session.server.shutdown()
    assert answer["atom"]["selection"] == "chainid 0 and resSeq 2 and name CA"


def test_a_click_in_the_viewer_gives_it(tmp_path) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_the_drawing_scripts_run_in_a_browser import _write_study

    viewer = "window.FastMDXMoleculeViewer.STATE"
    session = start_dashboard_session(output=str(_write_study(tmp_path / "study")),
                                      host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.set_default_timeout(60000)
            page.goto(session.url + "#viewer", wait_until="domcontentloaded")
            if not page.evaluate("() => !!document.createElement('canvas').getContext('webgl')"):
                pytest.skip("this browser has no WebGL, so 3Dmol cannot draw")
            page.wait_for_function(f"() => window.FastMDXMoleculeViewer && {viewer}.model")
            page.click('.info-tab[data-tab="selection"]')
            # As 3Dmol calls it on a click: the atom's own callback.
            page.evaluate(f"""() => {{
                const atom = {viewer}.viewer.getModel().selectedAtoms({{resi: 3, atom: 'CA'}})[0];
                atom.callback(atom, {viewer}.viewer);
            }}""")
            page.wait_for_selector('#selection-strings .selection-string[data-of="atom"]')
            residue = page.text_content('#selection-strings [data-of="residue"] code')
            atom = page.text_content('#selection-strings [data-of="atom"] code')
            note = page.text_content("#selection-strings p")
            browser.close()
    finally:
        session.server.shutdown()
    assert residue == "chainid 0 and resSeq 3"
    assert atom == "chainid 0 and resSeq 3 and name CA"
    assert note == "Checked against trajectory_topology.pdb, the topology the analyses read."
