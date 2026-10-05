"""A study's main motion, shown on the structure.

The `dimred` analysis wrote each frame's projection onto the principal
components, a scatter that says when the study moved, not how. It now keeps
the motions, and the Viewer shows one as its atoms swinging along it and as
lines from each atom to where the motion takes it, placed on the first frame
played; a study analysed before is given the motion from its frames.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")

DATA = Path(__file__).parent / "data" / "assemblies"
FRAMES = 40
#: How far the first fifty residues are carried along x, in nm.
SWING = 0.3


def _a_hinge(root: Path) -> Path:
    """Trypsin whose first fifty residues swing along x, back and forth,
    while the rest stays, with a little noise on every atom."""
    from fastmdxplora.gui.trajectory_frames import frames_info

    (root / "simulation").mkdir(parents=True)
    whole = md.load_pdb(str(_unpacked(root)))
    protein = whole.atom_slice(whole.topology.select("protein"))
    moving = protein.topology.select("resSeq 16 to 65")
    rng = np.random.default_rng(5)
    xyz = np.repeat(protein.xyz, FRAMES, axis=0)
    for frame in range(FRAMES):
        xyz[frame, moving, 0] += SWING * np.sin(2 * np.pi * frame / FRAMES)
    xyz += rng.normal(0, 0.005, xyz.shape).astype(np.float32)
    trajectory = md.Trajectory(xyz, protein.topology)
    trajectory[0].save_pdb(str(root / "simulation" / "trajectory_topology.pdb"))
    trajectory.save_dcd(str(root / "simulation" / "production.dcd"))
    (root / "simulation" / "live_status.json").write_text(json.dumps(
        {"status": "completed", "stage": "production"}), encoding="utf-8")
    assert frames_info(root)["available"]
    return root


def _unpacked(root: Path) -> Path:
    raw = root / "3PTB.pdb"
    raw.write_bytes(gzip.decompress((DATA / "3PTB.pdb.gz").read_bytes()))
    return raw


@pytest.fixture(scope="module")
def hinge(tmp_path_factory) -> Path:
    from fastmdxplora.analysis import AnalysisOrchestrator

    root = _a_hinge(tmp_path_factory.mktemp("motion") / "study")
    AnalysisOrchestrator(str(root / "simulation" / "production.dcd"),
                         str(root / "simulation" / "trajectory_topology.pdb"),
                         output_dir=str(root / "analysis")).run(include=["dimred"])
    return root


def _arrows(said: dict) -> tuple[np.ndarray, np.ndarray, list[int]]:
    starts, ends, numbers = [], [], []
    for line in said["arrows"].splitlines():
        if not line.startswith("HETATM"):
            continue
        point = [float(line[30:38]), float(line[38:46]), float(line[46:54])]
        if line[12:16].strip() == "BEG":
            starts.append(point)
            numbers.append(int(line[22:26]))
        else:
            ends.append(point)
    return np.array(starts), np.array(ends), numbers


def test_the_analysis_keeps_its_motions(hinge):
    with np.load(hinge / "analysis" / "dimred" / "dimred_pca_modes.npz") as modes:
        assert modes["vectors"].shape == (2, len(modes["atoms"]), 3)
        assert modes["mean"].shape == (len(modes["atoms"]), 3)
        assert modes["ratio"][0] > 0.9 and int(modes["frames"]) == FRAMES
        norms = np.linalg.norm(modes["vectors"].reshape(2, -1), axis=1)
        assert norms == pytest.approx([1.0, 1.0])


def test_the_motion_is_the_hinge(hinge):
    from fastmdxplora.gui.motion import AMPLITUDE, motion

    said = motion(hinge, 1, 1)
    assert said["ok"], said
    assert said["from"] == "analysis" and said["frames"] == 40 and said["scale"] == 1
    assert said["said"].startswith("Motion 1: 9")
    assert said["said"].endswith("From the dimred analysis's principal components of "
                                 "`name CA` over the 40 frames it analysed.")
    starts, ends, numbers = _arrows(said)
    along = ends - starts
    lengths = np.linalg.norm(along, axis=1)
    swinging = np.array([16 <= n <= 65 for n in numbers])
    # Along x, for the residues that swing; the fit to every alpha carbon
    # takes their mean motion from all, so each moves its share.
    share = swinging.mean()
    assert np.abs(along[swinging, 0] / lengths[swinging]).min() > 0.95
    expected = AMPLITUDE * SWING / np.sqrt(2) * 10.0
    assert np.median(lengths[swinging]) == pytest.approx(expected * (1 - share), rel=0.1)
    assert np.median(lengths[~swinging]) == pytest.approx(expected * share, rel=0.15)
    # The swing passes through the mean and reaches the lines' ends.
    models = said["pdb"].split("ENDMDL")
    assert len(models) == 41 and models[0].startswith("MODEL        1")

    def atoms(model):
        return np.array([[float(l[30:38]), float(l[38:46]), float(l[46:54])]
                         for l in model.splitlines() if l.startswith("ATOM")])
    assert atoms(models[0]) == pytest.approx(starts, abs=2e-3)
    assert atoms(models[10]) == pytest.approx(ends, abs=2e-3)
    # Placed on the first frame played.
    first = md.load_dcd(str(hinge / "simulation" / "frames.dcd"),
                        top=str(hinge / "simulation" / "frames_topology.pdb"), frame=0)
    alphas = first.xyz[0, first.topology.select("name CA")] * 10.0
    assert np.abs(starts - alphas).max() < 2.0
    larger = motion(hinge, 1, 3)
    assert "made 3 times larger to be seen" in larger["said"]
    assert np.median(np.linalg.norm(np.subtract(*_arrows(larger)[1::-1]), axis=1)[swinging]) \
        == pytest.approx(3 * np.median(lengths[swinging]), rel=1e-3)


def test_a_study_analysed_before_finds_it_in_its_frames(hinge, tmp_path):
    import shutil

    from fastmdxplora.gui.motion import motion

    before = tmp_path / "before"
    shutil.copytree(hinge, before)
    (before / "analysis" / "dimred" / "dimred_pca_modes.npz").unlink()
    said = motion(before, 1, 1)
    assert said["ok"] and said["from"] == "frames"
    assert said["said"].endswith("From the principal components of the alpha carbons over "
                                 "the 40 frames played: the study's dimred analysis kept no "
                                 "motions to read.")
    kept = motion(hinge, 1, 1)
    # The same motion, up to its sign.
    a = np.subtract(*_arrows(said)[1::-1])
    b = np.subtract(*_arrows(kept)[1::-1])
    assert min(np.abs(a - b).max(), np.abs(a + b).max()) < 0.3
    # Found once.
    stamp = (before / "simulation" / "motion_modes.npz").stat().st_mtime_ns
    motion(before, 2, 1)
    assert (before / "simulation" / "motion_modes.npz").stat().st_mtime_ns == stamp


def test_what_is_not_a_motion_is_said(hinge, tmp_path):
    from fastmdxplora.gui.motion import motion

    assert motion(hinge, "first", 1)["reason"] == (
        "A motion is asked for by its number and a scale.")
    assert motion(hinge, 1, 5)["reason"] == (
        "A motion is shown as it is, or three times larger.")
    assert motion(hinge, 9, 1)["reason"] == "The study has 2 motions."
    assert motion(tmp_path, 1, 1)["reason"] == "There are no frames to find a motion in yet."


def test_the_viewer_swings_it(hinge):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    state = "window.FastMDXMoleculeViewer.STATE"
    session = start_dashboard_session(output=str(hinge), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            # The Playback starts closed; these drive what is in it.
            page.add_init_script("try { localStorage.setItem('fmx.viewerPlaybackOpen', '1'); } catch (e) {}")
            page.set_default_timeout(120000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#viewer", wait_until="domcontentloaded")
            if not page.evaluate("() => !!document.createElement('canvas').getContext('webgl')"):
                pytest.skip("this browser has no WebGL, so the viewer cannot render")
            page.wait_for_function("() => !document.getElementById('side-motion').hidden")
            page.check("#motion-swing")
            page.wait_for_function(f"() => {state}.engine.motionShown()"
                                   f" && {state}.engine.motionShown().frame > 2")
            options = page.locator("#motion-mode option").all_inner_texts()
            note = page.inner_text("#motion-note")
            page.check("#motion-arrows")
            page.wait_for_function(f"() => {state}.engine.motionShown().arrows")
            page.select_option("#traj-superpose", "none")
            page.wait_for_function(f"() => !{state}.engine.motionShown().rendered")
            page.uncheck("#motion-swing")
            page.uncheck("#motion-arrows")
            page.wait_for_function(f"() => !{state}.engine.motionShown()")
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert options[0].startswith("Motion 1 (9") and len(options) == 2
    assert note.startswith("Motion 1: 9")
