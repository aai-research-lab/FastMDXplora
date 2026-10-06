"""The states a study visited, shown and compared in the Viewer.

The cluster analysis labelled each frame with a state and plotted the labels
over time; which structure a state is, and how two differ, had to be found
frame by frame. Each state now has its share and a representative, its
medoid among the frames played, and two are compared: the second's
representative beside the first's, fitted on the alpha carbons, the protein
coloured by how far each residue moved between them.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import numpy as np
import pytest
from tests import viewer_hooks as hooks

md = pytest.importorskip("mdtraj")

DATA = Path(__file__).parent / "data" / "assemblies"
FRAMES = 30
SHIFT = 0.4


def _two_states(root: Path) -> Path:
    """Trypsin whose residues 16 to 65 sit 0.4 nm along x in the second half
    of the run, and where they started in the first."""
    from fastmdxplora.analysis import AnalysisOrchestrator
    from fastmdxplora.gui.trajectory_frames import frames_info

    (root / "simulation").mkdir(parents=True)
    raw = root / "3PTB.pdb"
    raw.write_bytes(gzip.decompress((DATA / "3PTB.pdb.gz").read_bytes()))
    whole = md.load_pdb(str(raw))
    protein = whole.atom_slice(whole.topology.select("protein"))
    moving = protein.topology.select("resSeq 16 to 65")
    xyz = np.repeat(protein.xyz, FRAMES, axis=0)
    xyz[FRAMES // 2:, moving, 0] += SHIFT
    xyz += np.random.default_rng(9).normal(0, 0.01, xyz.shape).astype(np.float32)
    trajectory = md.Trajectory(xyz, protein.topology)
    trajectory[0].save_pdb(str(root / "simulation" / "trajectory_topology.pdb"))
    trajectory.save_dcd(str(root / "simulation" / "production.dcd"))
    (root / "simulation" / "live_status.json").write_text(json.dumps(
        {"status": "completed", "stage": "production"}), encoding="utf-8")
    assert frames_info(root)["available"]
    AnalysisOrchestrator(str(root / "simulation" / "production.dcd"),
                         str(root / "simulation" / "trajectory_topology.pdb"),
                         output_dir=str(root / "analysis")).run(
        include=["cluster"], options={"cluster": {"n_clusters": 2,
                                                  "methods": ["kmeans", "hierarchical"]}})
    return root


@pytest.fixture(scope="module")
def study(tmp_path_factory) -> Path:
    return _two_states(tmp_path_factory.mktemp("states") / "study")


def test_each_state_and_its_representative(study):
    from fastmdxplora.gui.states import states_of

    said = states_of(study)
    assert said["ok"] and said["method"] == "kmeans"
    assert said["methods"] == ["kmeans", "hierarchical"]
    assert [s["share"] for s in said["states"]] == [0.5, 0.5]
    halves = sorted((s["representative"] < FRAMES // 2) for s in said["states"])
    assert halves == [False, True]
    for found in said["states"]:
        assert found["frames"] == found["played"] == FRAMES // 2
    assert said["said"].startswith("The 2 states the cluster analysis's kmeans found among "
                                   "the 30 frames it analysed")
    assert states_of(study, "hierarchical")["method"] == "hierarchical"


def test_two_states_compared(study):
    from fastmdxplora.gui.states import state_difference, states_of

    first, second = (s["representative"] for s in states_of(study)["states"])
    said = state_difference(study, first, second)
    assert said["ok"] and said["first"] == first and said["second"] == second
    rows = said["property"]["values"]
    moved = np.array([row[3] for row in rows])
    swinging = np.array([16 <= row[1] <= 65 for row in rows])
    share = swinging.mean()
    assert np.median(moved[swinging]) == pytest.approx(SHIFT * 10 * (1 - share), rel=0.15)
    assert np.median(moved[~swinging]) == pytest.approx(SHIFT * 10 * share, rel=0.25)
    assert said["property"]["label"] == "Moved between the states"
    assert said["property"]["unit"] == "Å" and rows[0][0] == "A"
    assert said["rmsd_angstrom"] == pytest.approx(float(np.sqrt((moved ** 2).mean())), abs=1e-3)
    topology = (study / "simulation" / "frames_topology.pdb").read_text()
    atoms = sum(1 for line in topology.splitlines() if line.startswith(("ATOM", "HETATM")))
    assert said["pdb"].count("\nATOM") + said["pdb"].startswith("ATOM") == atoms


def test_what_cannot_be_compared_is_said(study, tmp_path):
    from fastmdxplora.gui.states import state_difference, states_of

    assert states_of(tmp_path)["reason"].startswith("The study has no states")
    assert state_difference(study, 0, 1, "../x.dcd")["reason"] == (
        "The frames are named by the Viewer's own words.")
    assert state_difference(study, 0, 1, "frames_nothing.dcd")["reason"] == (
        "There are no such frames yet.")
    assert state_difference(study, "a", 1)["reason"] == "Two frames are compared by their numbers."
    assert state_difference(study, 0, 99)["reason"] == "The frames played are 0 to 29."


def test_the_server_gives_them(study):
    import urllib.request

    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)

    def get(path):
        with urllib.request.urlopen(session.url + path, timeout=120) as response:
            return json.loads(response.read())

    try:
        states = get("/api/states?method=hierarchical")
        assert states["ok"] and states["method"] == "hierarchical"
        said = get("/api/state-difference?a=0&b=29&on=backbone&to=first&smooth=1")
        assert said["ok"] and said["rmsd_angstrom"] > 1.0
        assert (study / "simulation" / "frames_superposed_backbone.dcd").is_file()
        refused = get("/api/state-difference?a=0&b=29&on=sideways")
        assert refused["ok"] is False and refused["reason"].startswith("Frames are superposed on")
    finally:
        session.server.shutdown()


def test_the_viewer_compares_them(study):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    state = "window.FastMDXMoleculeViewer.STATE"
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.set_default_timeout(120000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#viewer", wait_until="domcontentloaded")
            if not page.evaluate("() => !!document.createElement('canvas').getContext('webgl')"):
                pytest.skip("this browser has no WebGL, so the viewer cannot render")
            page.wait_for_function("() => !document.getElementById('side-states').hidden")
            hooks.tool(page, "side-states")
            rows = page.locator("#states-list li").all_inner_texts()
            later = page.locator("#states-list li").nth(1)
            frame = int(later.get_attribute("data-state") or 0)
            later.locator("button").click()
            page.wait_for_function(f"() => {state}.mode === 'playback'")
            shown_frame = page.evaluate(f"() => {state}.engine.frame()")
            colour_before = page.evaluate(f"() => {state}.colorMode")
            page.click("#states-compare")
            page.wait_for_function(f"() => {state}.engine.comparedShown()"
                                   f" && {state}.engine.comparedShown().rendered"
                                   f" && {state}.colorMode === 'result:state-difference'")
            page.wait_for_function("() => document.getElementById('states-note')"
                                   ".textContent.includes('in grey')")
            legend = page.inner_text("#viewer-legend").splitlines()[0]
            note = page.inner_text("#states-note")
            at = page.evaluate(f"() => {state}.engine.comparedShown().frame")
            page.evaluate("() => window.dispatchEvent(new CustomEvent("
                          f"'dashboard:trajectory-seek', {{detail: {{frame: {at} === 0 ? 1 : 0}}}}))")
            page.wait_for_function(f"() => !{state}.engine.comparedShown().rendered")
            page.click("#states-stop")
            page.wait_for_function(f"() => !{state}.engine.comparedShown()")
            colour_after = page.evaluate(f"() => {state}.colorMode")
            offered = page.eval_on_selector_all("#viewer-color option",
                                                "o => o.map((x) => x.value)")
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert len(rows) == 2 and all("(50%)" in row for row in rows)
    assert frame in (0, 1) and shown_frame >= 0
    assert legend == "Moved between the states (Å)"
    assert "in grey, beside" in note and "Coloured by how far each residue moved." in note
    assert colour_after == colour_before
    assert "result:state-difference" not in offered


def test_frames_in_no_state_and_states_that_cannot_be_shown(study, tmp_path):
    import shutil

    from fastmdxplora.gui.states import states_of

    copy = tmp_path / "copy"
    shutil.copytree(study, copy)
    folder = copy / "analysis" / "cluster"
    # The copy's analyses read the copy's trajectory, as a moved study's do.
    manifest = copy / "analysis" / "analysis_manifest.json"
    record = json.loads(manifest.read_text())
    record["resolved"] = {**(record.get("resolved") or {}),
                          "trajectory": str(copy / "simulation" / "production.dcd")}
    manifest.write_text(json.dumps(record))
    labels = (folder / "cluster_kmeans.dat").read_text().splitlines()
    noisy = [labels[0]] + [line if k % 7 else line.rsplit(",", 1)[0] + ",-1"
                           for k, line in enumerate(labels[1:])]
    (folder / "cluster_dbscan.dat").write_text("\n".join(noisy) + "\n")
    said = states_of(copy, "dbscan")
    assert said["method"] == "dbscan" and said["unclustered"] == 5
    assert said["said"].endswith(" 5 frames belonged to no state.")
    manifest = copy / "analysis" / "analysis_manifest.json"
    record = json.loads(manifest.read_text())
    record["resolved"] = {"trajectory": str(tmp_path / "another.dcd")}
    record.pop("trajectory_input", None)
    manifest.write_text(json.dumps(record))
    assert states_of(copy)["reason"] == ("The states were found in a trajectory other than "
                                        "the one played.")
