"""How much room a ligand's pocket has, frame by frame, in the Viewer.

The pocket's empty space is counted on a grid as POVME counts it: points
half an angstrom apart within 4 Å of the ligand's heavy atoms in the first
frame, each frame fitted on the pocket's backbone to the first; a point is
empty beyond every protein atom's van der Waals radius, inside the hull of
the pocket's residues, and joined to the ligand's place.

A known answer: a ligand atom at the centre of a closed shell of protein
atoms. While the shell's radius is 8 Å, nothing of the protein is within
4 Å of the centre, and the pocket is the whole region, a ball of radius 4 Å
(268 Å³). When the shell closes to 5 Å, the room is a ball whose radius is
the shell's less the atoms' radii (about 3.3 to 3.5 Å).
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")

FRAMES = 8
VIEWER = "window.FastMDXMoleculeViewer"


def _shell(n: int) -> np.ndarray:
    """``n`` points spread evenly over a unit sphere (a Fibonacci lattice)."""
    k = np.arange(n) + 0.5
    polar = np.arccos(1 - 2 * k / n)
    around = math.pi * (1 + 5 ** 0.5) * k
    return np.stack([np.cos(around) * np.sin(polar), np.sin(around) * np.sin(polar),
                     np.cos(polar)], axis=1)


def _pocket_study(root: Path) -> Path:
    from fastmdxplora.gui.trajectory_frames import frames_info

    (root / "simulation").mkdir(parents=True)
    (root / "setup").mkdir()
    top = md.Topology()
    chain = top.add_chain()
    points = _shell(600)
    for r in range(len(points) // 4):
        residue = top.add_residue("ALA", chain, resSeq=r + 1)
        for name in ("N", "CA", "C", "O"):
            top.add_atom(name, md.element.get_by_symbol(name[0]), residue)
    ligand = top.add_residue("BEN", top.add_chain(), resSeq=1)
    top.add_atom("C1", md.element.carbon, ligand)
    centre = np.array([3.0, 3.0, 3.0])
    xyz = np.zeros((FRAMES, top.n_atoms, 3), np.float32)
    for frame in range(FRAMES):
        radius = 0.8 if frame < FRAMES // 2 else 0.5
        xyz[frame, :-1] = centre + radius * points
        xyz[frame, -1] = centre
    trajectory = md.Trajectory(xyz, top, unitcell_lengths=np.full((FRAMES, 3), 6.0),
                               unitcell_angles=np.full((FRAMES, 3), 90.0))
    trajectory[0].save_pdb(str(root / "setup" / "topology.pdb"))
    trajectory[0].save_pdb(str(root / "simulation" / "trajectory_topology.pdb"))
    trajectory.save_dcd(str(root / "simulation" / "production.dcd"))
    (root / "simulation" / "live_status.json").write_text(json.dumps(
        {"status": "completed", "stage": "production"}), encoding="utf-8")
    assert frames_info(root)["available"]
    return root


@pytest.fixture(scope="module")
def study(tmp_path_factory) -> Path:
    return _pocket_study(tmp_path_factory.mktemp("pocket") / "study")


def test_the_room_a_closing_shell_leaves(study):
    from fastmdxplora.gui.pocket_volume import pocket_volume

    said = pocket_volume(study, "BEN", 10.0)
    assert said["ok"] and said["frames"] == FRAMES
    ball = 4 / 3 * math.pi * 4.0 ** 3
    # The region is the ball of 4 Å about the ligand, to the grid's grain.
    assert said["region"] == pytest.approx(ball, rel=0.03)
    open_, closed = said["volumes"][:FRAMES // 2], said["volumes"][FRAMES // 2:]
    assert all(v == pytest.approx(ball, rel=0.03) for v in open_)
    smallest, largest = (4 / 3 * math.pi * (5.0 - r) ** 3 for r in (1.7, 1.52))
    assert all(smallest * 0.95 <= v <= largest * 1.05 for v in closed)
    assert said["mean"] == pytest.approx(np.mean(said["volumes"]), abs=0.1)
    assert said["said"].startswith("The room the protein leaves in BEN's pocket, POVME's way")
    assert "not the error of the mean" in said["said"]
    assert pocket_volume(study, "BEN", 10.0) == said


def test_the_empty_points_of_a_frame(study):
    from fastmdxplora.gui.pocket_volume import GRID_ANGSTROM, pocket_points, pocket_volume

    volumes = pocket_volume(study, "BEN", 10.0)["volumes"]
    for frame in (0, FRAMES - 1):
        text, reason = pocket_points(study, "BEN", 10.0, frame)
        assert reason is None
        lines = text.splitlines()
        counts = [int(v) for v in lines[0].split()[-3:]]
        values = np.array([float(v) for line in lines[7:] if line[:1].isdigit()
                           for v in line.split()])
        assert values.size == np.prod(counts)
        assert values.sum() * GRID_ANGSTROM ** 3 == pytest.approx(volumes[frame], abs=0.006)
        # Placed about the ligand: the grid's middle is the centre, in Å.
        origin = np.array([float(v) for v in lines[1].split()[1:]])
        middle = origin + (np.array(counts) - 1) / 2 * GRID_ANGSTROM
        assert middle == pytest.approx([30.0, 30.0, 30.0], abs=GRID_ANGSTROM)


def test_what_has_no_pocket_says_so(study, tmp_path):
    from fastmdxplora.gui.pocket_volume import pocket_points, pocket_volume

    assert pocket_volume(study, None)["reason"] == "A pocket is the ligand's: no ligand was named."
    assert pocket_volume(study, "BEN", 30)["reason"] == "The pocket's cutoff is 1 to 20 Å."
    assert pocket_volume(tmp_path, "BEN")["reason"] == (
        "There are no frames to find the pocket in yet.")
    assert pocket_volume(study, "XYZ", 10.0)["reason"].startswith(
        "The pocket could not be found: There is no XYZ in the frames.")
    assert pocket_points(study, "BEN", 10.0, "x") == (None, "A frame is named by its number.")
    assert pocket_points(study, "BEN", 10.0, 99) == (None, "The frames played are 0 to 7.")
    assert pocket_points(study, "BE N", 10.0, 0)[1] == (
        "A pocket is the ligand's: no ligand was named.")


def test_the_viewer_plots_it_and_renders_the_pocket(study):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    pocket = "window.FastMDXPocketVolume"
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
            page.wait_for_function(f"() => {VIEWER} && {VIEWER}.STATE.engine"
                                   f" && {VIEWER}.STATE.playbackPayload")
            page.evaluate(f"async () => {{ await {VIEWER}.movie.frames(); }}")
            page.evaluate(f"() => {{ {VIEWER}.STATE.pocketCutoff = 10; }}")
            page.wait_for_function("() => !document.getElementById('side-pocket').hidden")
            page.click("#side-pocket > summary")
            page.wait_for_function(f"() => {pocket}.state.data")
            page.check("#pocket-shown")
            page.wait_for_function(f"() => {VIEWER}.STATE.engine.volumesShown()"
                                   ".some((v) => v.key === 'pocket' && v.rendered)")
            superposed = page.evaluate(f"() => {VIEWER}.STATE.superposed")
            page.evaluate("() => window.dispatchEvent(new CustomEvent("
                          "'dashboard:trajectory-seek', {detail: {frame: 6}}))")
            page.wait_for_function(f"() => {pocket}.state.shownFrame === 6")
            page.wait_for_function("() => document.getElementById('pocket-now')"
                                   ".textContent.startsWith('Frame 6:')")
            now = page.text_content("#pocket-now")
            note = page.text_content("#pocket-note")
            # A click on the plot shows that frame.
            page.locator("#pocket-canvas").scroll_into_view_if_needed()
            box = page.locator("#pocket-canvas").bounding_box()
            at = page.evaluate(f"() => {pocket}.xOfFrame(2)")
            page.mouse.click(box["x"] + at, box["y"] + box["height"] / 2)
            page.wait_for_function(f"() => {VIEWER}.movie.shownFrame() === 2")
            page.uncheck("#pocket-shown")
            page.wait_for_function(f"() => !{VIEWER}.STATE.engine.volumesShown()"
                                   ".some((v) => v.key === 'pocket')")
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert superposed == "pocket"
    assert now.startswith("Frame 6:") and now.endswith(" Å³.")
    assert note.startswith("The room the protein leaves in BEN's pocket")
