"""A residue clicked in the viewer offers its protonation states for a new study.

The builder's preview lists each histidine, aspartate, glutamate and lysine
with its states before a run (1132). After one, the residue a person is
looking at in the viewer is where the question arises: this histidine sits
by the ligand, would it bind as HIE? Clicking it now offers its states, each
with what it is, and choosing one opens the Config page with this study's
Config and that residue set: the same structure, found as setup builds it,
never the prepared system or the segment this study ran. Nothing runs.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import yaml

md = pytest.importorskip("mdtraj")

from fastmdxplora.gui.selection import states_for  # noqa: E402
from tests.test_a_structure_looked_at_says_what_setup_builds import CHAIN, DIMER  # noqa: E402
from tests import viewer_hooks as hooks  # noqa: E402


def _study(root: Path, structure_text: str, **extra) -> Path:
    root.mkdir(parents=True)
    structure = root.parent / f"{root.name}.pdb"
    structure.write_text(structure_text, encoding="utf-8")
    config = {"systems": [{"system": str(structure)}], "output": str(root),
              "agent": "assisted", "agent_model": "openai/m",
              "setup": {"forcefield": "amber14"},
              "simulation": {"duration_ns": 10, "setup_from": "elsewhere"}, **extra}
    (root / "resolved_config.yml").write_text(yaml.safe_dump(config), encoding="utf-8")
    return root


class TestTheStates:
    def test_a_histidine_its_states_and_the_config_to_start_from(self, tmp_path):
        study = _study(tmp_path / "s", CHAIN + "END\n",
                       setup={"forcefield": "amber14", "residue_states": {"A:2": "HID"}})
        found = states_for(study, chain="A", resseq=2, resname="HIE")
        assert found["ok"] and (found["key"], found["resname"]) == ("A:2", "HIS")
        assert found["current"] == "HID"
        assert [s["state"] for s in found["states"]] == ["HID", "HIE", "HIP"]
        assert found["states"][2]["meaning"] == "charged, hydrogens on both"
        config = found["config"]
        # This study's settings, and none of what ties it to this study.
        assert config["setup"]["forcefield"] == "amber14"
        assert config["simulation"] == {"duration_ns": 10}
        assert not {"output", "agent", "agent_model"} & set(config)

    def test_the_new_study_is_one_the_builder_opens(self, tmp_path):
        from fastmdxplora.gui.config_builder import state_from_config

        found = states_for(_study(tmp_path / "s", CHAIN + "END\n"),
                           chain="A", resseq=2, resname="HIS")
        config = found["config"]
        config["setup"]["residue_states"] = {found["key"]: "HIE"}
        loaded = state_from_config(config)
        assert loaded["ok"]
        assert loaded["state"]["phases"]["setup"]["residue_states"] == {"A:2": "HIE"}

    def test_found_in_the_assembly_setup_builds(self, tmp_path):
        study = _study(tmp_path / "s", DIMER + CHAIN + "END\n")
        assert states_for(study, chain="B", resseq=2, resname="HIS")["key"] == "B:2"
        # A chain the structure does not have does not say which copy.
        refused = states_for(study, chain="Q", resseq=2, resname="HIS")
        assert "more than one chain" in refused["reason"]

    @pytest.mark.parametrize("chain,resseq,resname,said", [
        ("A", 1, "GLY", "no protonation state to choose"),
        ("A", 9, "HIS", "is not in"),
        ("A", "x", "HIS", "no residue number"),
    ])
    def test_what_is_refused(self, tmp_path, chain, resseq, resname, said):
        study = _study(tmp_path / "s", CHAIN + "END\n")
        assert said in states_for(study, chain=chain, resseq=resseq, resname=resname)["reason"]

    def test_a_study_that_prepared_nothing(self, tmp_path):
        study = _study(tmp_path / "s", CHAIN + "END\n", include_phase=["analysis", "report"])
        assert "did not prepare a structure" in states_for(
            study, chain="A", resseq=2, resname="HIS")["reason"]
        assert "no Config" in states_for(tmp_path, chain="A", resseq=2,
                                         resname="HIS")["reason"]


def _viewer_study(root: Path) -> Path:
    """A study whose structure has a histidine, as a run leaves it."""
    (root / "setup").mkdir(parents=True)
    (root / "simulation").mkdir()
    topology = md.Topology()
    chain = topology.add_chain()
    xyz = []
    for index, name in enumerate(("ALA", "ALA", "HIS", "ALA", "ALA")):
        residue = topology.add_residue(name, chain, resSeq=index + 1)
        for offset, (atom, element) in enumerate((("N", md.element.nitrogen),
                                                 ("CA", md.element.carbon),
                                                 ("C", md.element.carbon),
                                                 ("O", md.element.oxygen))):
            topology.add_atom(atom, element, residue)
            xyz.append([0.38 * index + 0.1 * offset + 2.0, 2.0, 2.0])
    frames = np.repeat(np.array(xyz)[None], 3, axis=0)
    trajectory = md.Trajectory(frames, topology, unitcell_lengths=np.full((3, 3), 5.0),
                               unitcell_angles=np.full((3, 3), 90.0))
    trajectory[0].save_pdb(str(root / "setup" / "topology.pdb"))
    trajectory[0].save_pdb(str(root / "simulation" / "trajectory_topology.pdb"))
    trajectory.save_dcd(str(root / "simulation" / "production.dcd"))
    (root / "resolved_config.yml").write_text(yaml.safe_dump({
        "systems": [{"system": str(root / "setup" / "topology.pdb")}],
        "simulation": {"duration_ns": 2}}), encoding="utf-8")
    (root / "simulation" / "live_status.json").write_text(json.dumps(
        {"status": "completed", "stage": "production"}), encoding="utf-8")
    return root


def test_the_viewer_offers_them(tmp_path) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    viewer = "window.FastMDXMoleculeViewer.STATE"
    study = _viewer_study(tmp_path / "study")
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#viewer", wait_until="domcontentloaded")
            if not page.evaluate("() => !!document.createElement('canvas').getContext('webgl')"):
                pytest.skip("this browser has no WebGL, so the viewer cannot render")
            page.wait_for_function(f"() => window.FastMDXMoleculeViewer && {viewer}.model")
            page.click('.info-tab[data-tab="selection"]')
            hooks.click(page, resi=3, atom="CA")
            page.wait_for_selector(".residue-states [data-state='HIE']")
            offered = page.text_content(".residue-states")
            page.click(".residue-states [data-state='HIE']")
            page.wait_for_selector('.page[data-page="run"]:not([hidden])')
            built = page.evaluate("() => window.FastMDXRun.fetchConfig()")
            note = page.text_content("#run-note")
            browser.close()
    finally:
        session.server.shutdown()
    assert "with HIS A:3 as:" in offered and "left it to setup" in offered
    written = yaml.safe_load(built["yaml"])
    assert written["setup"]["residue_states"] == {"A:3": "HIE"}
    assert written["simulation"]["duration_ns"] == 2
    assert note.startswith("A new study of") and "HIS A:3 as HIE. Nothing has run." in note
    assert errors == []
