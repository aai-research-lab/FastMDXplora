"""The viewer's cartoon is DSSP, for the structure and each frame played.

No PDB the viewer is sent has HELIX or SHEET records, so the viewer shaped
the cartoon from its own estimate, and it could disagree with the study's
secondary structure plot about the same frame: 3Dmol's was not DSSP at all,
and Mol*'s is DSSP chain by chain, with no strand paired across chains.
DSSP is computed for what the viewer was sent (gui/by_residue.py), from the
trajectory's own coordinates where the frames came from it, and the
Structure tab says which the cartoon is.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import mdtraj as md
import numpy as np
import pandas as pd
import pytest

from fastmdxplora.gui.by_residue import residue_runs, secondary_structure

ASSEMBLIES = Path(__file__).parent / "data" / "assemblies"
FRAMES = 6


def _helical_study(root: Path, *, analyse: bool = True) -> Path:
    """Haemoglobin's alpha and beta chains (1HHO), helical, with chain A
    stretched out of its helices in the second half of the frames; analysed
    for RMSF, secondary structure and per-residue SASA where asked."""
    (root / "setup").mkdir(parents=True)
    (root / "simulation").mkdir()
    deposited = root / "setup" / "input.pdb"
    deposited.write_bytes(gzip.decompress((ASSEMBLIES / "1HHO.pdb.gz").read_bytes()))
    whole = md.load_pdb(str(deposited))
    protein = whole.atom_slice(whole.topology.select("protein"))
    protein.save_pdb(str(root / "setup" / "topology.pdb"))
    xyz = np.repeat(protein.xyz, FRAMES, axis=0)
    chain_a = protein.topology.select("chainid 0")
    centre = xyz[0, chain_a].mean(axis=0)
    for frame in range(FRAMES // 2, FRAMES):
        xyz[frame, chain_a] = centre + 1.6 * (xyz[frame, chain_a] - centre)
    xyz += np.random.default_rng(0).normal(0, 0.005, xyz.shape).astype(np.float32)
    trajectory = md.Trajectory(xyz, protein.topology)
    trajectory[0].save_pdb(str(root / "simulation" / "trajectory_topology.pdb"))
    trajectory.save_dcd(str(root / "simulation" / "production.dcd"))
    (root / "simulation" / "live_status.json").write_text(json.dumps(
        {"status": "completed", "stage": "production", "current_step": 2500,
         "total_steps": 2500}), encoding="utf-8")
    (root / "simulation" / "simulation_parameters.json").write_text(json.dumps(
        {"parameters": {"trajectory_interval_steps": 500, "timestep_fs": 2}}),
        encoding="utf-8")
    if analyse:
        from fastmdxplora.analysis import AnalysisOrchestrator

        AnalysisOrchestrator(str(root / "simulation" / "production.dcd"),
                             str(root / "simulation" / "trajectory_topology.pdb"),
                             output_dir=str(root / "analysis")).run(
            include=["rmsf", "ss", "sasa"], options={"sasa": {"mode": "residue"}})
    return root


@pytest.fixture(scope="module")
def study(tmp_path_factory) -> Path:
    return _helical_study(tmp_path_factory.mktemp("helical") / "study")


def _plotted(study: Path) -> list[str]:
    """Each frame's codes as the study's secondary structure analysis wrote them."""
    table = pd.read_csv(study / "analysis" / "ss" / "ss.dat")
    return ["".join(table.iloc[row, 1:].tolist()) for row in range(len(table))]


def test_the_frames_are_what_the_analysis_plotted(study):
    from fastmdxplora.gui.trajectory_frames import frames_info

    assert frames_info(study)["frame_indices"] == list(range(FRAMES))
    said = secondary_structure(study, "frames")
    assert said["available"] and said["n_frames"] == FRAMES
    # Every residue of every frame, read from the frames' binary coordinates
    # as the analysis read the trajectory: from a PDB's rounded coordinates
    # one hydrogen bond on DSSP's threshold fell the other way in one frame.
    assert said["frames"] == _plotted(study)
    chain_a = [code for code, residue in zip(said["frames"][0], said["residues"])
               if residue[0] == "A"]
    assert chain_a.count("H") > 80
    assert "H" not in said["frames"][-1][:len(chain_a)]
    assert said["residues"][0] == ["A", 1, "", "VAL", 0]
    assert said["residues"][141] == ["B", 1, "", "VAL", 0]


def _dssp_of(path: Path) -> str:
    """DSSP of a structure file as MDTraj gives it, protein residues only."""
    codes = md.compute_dssp(md.load_pdb(str(path)), simplified=True)[0]
    return "".join(code for code in codes if code != "NA")


def test_the_structure_and_the_live_frame_too(study):
    said = secondary_structure(study, "structure")
    assert said["available"] and said["n_frames"] == 1
    assert said["frames"][0] == _dssp_of(study / "setup" / "topology.pdb")
    assert secondary_structure(study, "live")["available"] is False
    live = study / "simulation" / "live_frame.pdb"
    live.write_text((study / "simulation" / "trajectory_topology.pdb")
                    .read_text(encoding="utf-8"), encoding="utf-8")
    try:
        assert secondary_structure(study, "live")["frames"][0] == _dssp_of(live)
    finally:
        live.unlink()


def test_why_there_is_none(tmp_path):
    root = tmp_path / "study"
    (root / "simulation").mkdir(parents=True)
    assert "no such structure" in secondary_structure(root, "frames")["reason"]
    assert "No structure called" in secondary_structure(root, "elsewhere")["reason"]
    ligand = "".join(
        f"HETATM{n:5d}  C{n}  LIG A   1    {n:8.3f}   0.000   0.000  1.00  0.00           C\n"
        for n in range(1, 4))
    (root / "simulation" / "live_frame.pdb").write_text(ligand + "END\n", encoding="utf-8")
    assert secondary_structure(root, "live")["reason"] == "It has no protein."
    uneven = ("MODEL        1\n" + ligand + "ENDMDL\nMODEL        2\n"
              + ligand.splitlines(keepends=True)[0] + "ENDMDL\nEND\n")
    from fastmdxplora.gui import by_residue

    by_residue._CACHE.clear()
    (root / "simulation" / "live_frame.pdb").write_text(uneven, encoding="utf-8")
    assert "same atoms" in secondary_structure(root, "live")["reason"]


def test_a_residue_is_found_by_its_occurrence_where_its_code_is_lost():
    """MDTraj writes no insertion codes, so trypsin's 184A and 184 can both
    arrive as 184. Two residues of one name and number are still two."""
    def atom(serial: int, name: str, resname: str, number: int, code: str = " ") -> str:
        return (f"ATOM  {serial:5d}  {name:<3} {resname} A{number:4d}{code}   "
                f"{serial:8.3f}   0.000   0.000  1.00  0.00           C")

    # Side by side, the second is known by its atom names coming round again.
    lines = [atom(1, "N", "GLY", 184, "A"), atom(2, "CA", "GLY", 184, "A"),
             atom(3, "N", "GLY", 184), atom(4, "CA", "GLY", 184),
             atom(5, "N", "GLY", 184), atom(6, "CA", "GLY", 184),
             atom(7, "N", "TYR", 184)]
    assert [(*run[:5], len(run[5])) for run in residue_runs(lines)] == [
        ("A", 184, "A", "GLY", 0, 2), ("A", 184, "", "GLY", 0, 2),
        ("A", 184, "", "GLY", 1, 2), ("A", 184, "", "TYR", 0, 1)]
    lines = [atom(1, "CA", "GLY", 184), atom(2, "CA", "ALA", 185), atom(3, "CA", "GLY", 184)]
    assert [run[4] for run in residue_runs(lines)] == [0, 0, 1]


def _open(study: Path, then) -> object:
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900},
                                    accept_downloads=True)
            page.set_default_timeout(90000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#viewer", wait_until="domcontentloaded")
            page.wait_for_function(
                "() => window.FastMDXMoleculeViewer"
                " && window.FastMDXMoleculeViewer.STATE.secondaryStructure")
            said = then(page)
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    return said


#: Each frame's codes for chain A's residues, as the cartoon has them.
CHAIN_A = """async () => {
    const engine = window.FastMDXMoleculeViewer.STATE.engine;
    const codes = [];
    for (let frame = 0; frame < engine.frameCount(); frame++) {
        await engine.setFrame(frame);
        codes.push(engine.secondaryStructureShown().A);
    }
    return codes;
}"""


def test_the_viewer_draws_the_cartoon_from_it(study):
    pytest.importorskip("playwright.sync_api")

    def look(page):
        said = {"structure": page.evaluate(
                    "() => window.FastMDXMoleculeViewer.STATE.secondaryStructure"),
                "line": page.text_content("#viewer-ss-said")}
        page.evaluate("() => window.FastMDXMoleculeViewer.loadPlayback()")
        page.wait_for_function(
            "() => window.FastMDXMoleculeViewer.STATE.secondaryStructure.of === 'frames'")
        said["playback"] = page.evaluate(
            "() => window.FastMDXMoleculeViewer.STATE.secondaryStructure")
        said["frames"] = page.evaluate(CHAIN_A)
        said["played"] = page.text_content("#viewer-ss-said")
        return said

    said = _open(study, look)
    assert said["structure"]["applied"] is True
    assert "DSSP, computed for the structure shown" in said["line"]
    assert said["playback"]["applied"] is True
    assert "each of the 6 frames played" in said["played"]
    plotted = _plotted(study)
    assert [codes[:141] for codes in said["frames"]] == [codes[:141] for codes in plotted]
    assert said["frames"][0] != said["frames"][-1]


def test_a_playback_rewritten_since_it_was_drawn_keeps_the_estimate(study):
    """A run rewrites its frames as it goes, and DSSP can be computed from
    newer frames than the ones rendered: that is not DSSP of what is rendered, and
    the cartoon keeps the viewer's own, saying so."""
    pytest.importorskip("playwright.sync_api")

    def look(page):
        def newer(route):
            said = route.fetch().json()
            said["signature"] = "a later version of the frames"
            route.fulfill(json=said)

        page.route("**/api/secondary-structure?of=frames*", newer)
        page.evaluate("() => window.FastMDXMoleculeViewer.loadPlayback()")
        page.wait_for_function(
            "() => window.FastMDXMoleculeViewer.STATE.secondaryStructure.of === 'frames'")
        return {"state": page.evaluate(
                    "() => window.FastMDXMoleculeViewer.STATE.secondaryStructure"),
                "line": page.text_content("#viewer-ss-said")}

    said = _open(study, look)
    assert said["state"]["applied"] is False
    assert "Mol*'s own DSSP" in said["line"]
    assert "not the ones shown" in said["line"]


def test_an_extended_study_is_read_from_its_joined_trajectory(tmp_path):
    """A study extended or resumed plays its joined trajectory against the
    topology its record names, and DSSP is read from the same frames."""
    import shutil

    from fastmdxplora.gui.trajectory_frames import frames_info

    root = _helical_study(tmp_path / "study", analyse=False)
    (root / "joined").mkdir()
    shutil.move(str(root / "simulation" / "production.dcd"), str(root / "joined"))
    named = root / "joined" / "topology.pdb"
    shutil.move(str(root / "simulation" / "trajectory_topology.pdb"), str(named))
    (root / "joined" / "joined.json").write_text(json.dumps({"topology": str(named)}),
                                                 encoding="utf-8")
    assert frames_info(root)["source_signature"].startswith("joined/production.dcd")
    said = secondary_structure(root, "frames")
    trajectory = md.load_dcd(str(root / "joined" / "production.dcd"), top=str(named))
    expected = md.compute_dssp(trajectory, simplified=True)
    assert said["frames"] == ["".join(code for code in row if code != "NA") for row in expected]


def test_what_cannot_be_read_is_said(tmp_path):
    root = tmp_path / "study"
    (root / "simulation").mkdir(parents=True)
    live = root / "simulation" / "live_frame.pdb"

    def said(text: str) -> str:
        live.write_text(text, encoding="utf-8")
        from fastmdxplora.gui import by_residue

        by_residue._CACHE.clear()
        return secondary_structure(root, "live")["reason"]

    assert said("REMARK nothing here\nEND\n") == "The structure has no atoms."
    alanine = ("ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00  0.00           N\n"
               "ATOM      2  CA  ALA A   1       1.458   0.000   0.000  1.00  0.00           C\n")
    # Two residues of one number side by side, as a file written without
    # insertion codes has them: MDTraj reads them as one, and it is said.
    again = ("ATOM      3  N   ALA A   1       3.000   0.000   0.000  1.00  0.00           N\n"
             "ATOM      4  CA  ALA A   1       4.458   0.000   0.000  1.00  0.00           C\n")
    assert "one to one" in said(alanine + again + "END\n")
    assert "could not convert" in said(alanine.replace("1.458", "x.xxx") + "END\n")
    # MDTraj reads the first model; a later one is read here.
    assert said("MODEL        1\n" + alanine + "ENDMDL\nMODEL        2\n"
                + alanine.replace("1.458", "x.xxx") + "ENDMDL\nEND\n") == (
        "A coordinate could not be read.")
    # No structure at all for the structure the viewer asks for.
    assert "no such structure" in secondary_structure(tmp_path / "empty", "structure")["reason"]
