"""A residue's backbone dihedrals over the frames played, in the Viewer.

The `dihedrals` analysis plots φ and ψ of the trajectory it analysed; a
residue was not followed through the frames the Viewer plays. Backbone
angles gives every residue's φ and ψ in each frame played (MDTraj, from the
frames as written), plotted on a Ramachandran plot tied to the frame shown:
the residue chosen's path over the frames, a ring at the frame shown, a
click on the path showing that frame and a click on a residue following it.
"""

from __future__ import annotations

import base64
import gzip
import json
from pathlib import Path

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")

DATA = Path(__file__).parent / "data" / "assemblies"
FRAMES = 12
VIEWER = "window.FastMDXMoleculeViewer"


def _trypsin(root: Path) -> Path:
    from fastmdxplora.gui.trajectory_frames import frames_info

    (root / "simulation").mkdir(parents=True)
    raw = root / "3PTB.pdb"
    raw.write_bytes(gzip.decompress((DATA / "3PTB.pdb.gz").read_bytes()))
    whole = md.load_pdb(str(raw))
    protein = whole.atom_slice(whole.topology.select("protein"))
    xyz = np.repeat(protein.xyz, FRAMES, axis=0)
    xyz += np.random.default_rng(3).normal(0, 0.01, xyz.shape).astype(np.float32)
    trajectory = md.Trajectory(xyz, protein.topology)
    trajectory[0].save_pdb(str(root / "simulation" / "trajectory_topology.pdb"))
    trajectory.save_dcd(str(root / "simulation" / "production.dcd"))
    (root / "simulation" / "live_status.json").write_text(json.dumps(
        {"status": "completed", "stage": "production"}), encoding="utf-8")
    assert frames_info(root)["available"]
    return root


@pytest.fixture(scope="module")
def study(tmp_path_factory) -> Path:
    return _trypsin(tmp_path_factory.mktemp("rama") / "study")


def _unpacked(text: str, frames: int) -> np.ndarray:
    values = np.frombuffer(base64.b64decode(text), dtype="<i2").astype(float)
    values[values == -32768] = np.nan
    return (values / 10).reshape(frames, -1)


def test_each_residue_s_angles_in_each_frame(study):
    from fastmdxplora.gui.backbone_angles import ANGLES_FILE, backbone_angles

    said = backbone_angles(study)
    assert said["ok"] and said["frames"] == FRAMES
    frames = md.load_dcd(str(study / "simulation" / "frames.dcd"),
                         top=str(study / "simulation" / "frames_topology.pdb"))
    residues = said["residues"]
    assert len(residues) == len([r for r in frames.topology.residues if r.is_protein])
    phi, psi = _unpacked(said["phi"], FRAMES), _unpacked(said["psi"], FRAMES)
    # Each against MDTraj's own, residue by residue.
    atoms, values = md.compute_phi(frames)
    for column, quartet in enumerate(atoms):
        residue = frames.topology.atom(int(quartet[2])).residue
        k = said["atoms"].index(next(a.index for a in residue.atoms if a.name == "CA"))
        assert phi[:, k] == pytest.approx(np.degrees(values[:, column]), abs=0.051)
    atoms, values = md.compute_psi(frames)
    for column, quartet in enumerate(atoms):
        residue = frames.topology.atom(int(quartet[1])).residue
        k = said["atoms"].index(next(a.index for a in residue.atoms if a.name == "CA"))
        assert psi[:, k] == pytest.approx(np.degrees(values[:, column]), abs=0.051)
    # A chain's first residue has no φ, its last no ψ; named as the frames
    # name them (3PTB numbers by chymotrypsin's).
    assert np.isnan(phi[:, 0]).all() and np.isfinite(psi[:, 0]).all()
    assert np.isnan(psi[:, -1]).all()
    assert residues[0] == ["A", 16, "", "ILE"] and ["A", 193, "", "GLY"] in residues
    # Written once for the frames as they are.
    assert json.loads((study / "simulation" / ANGLES_FILE).read_text())["signature"] == (
        said["signature"])
    assert backbone_angles(study) == said


def test_what_has_no_angles_says_so(tmp_path):
    from fastmdxplora.gui.backbone_angles import backbone_angles

    assert backbone_angles(tmp_path)["reason"] == (
        "There are no frames to follow a residue's angles in yet.")


def test_the_viewer_follows_a_residue_over_the_frames(study):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    rama = "window.FastMDXRamachandran"
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
            page.wait_for_function("() => !document.getElementById('side-rama').hidden")
            page.click("#side-rama > summary")
            page.wait_for_function(f"() => {rama}.state.data")
            page.wait_for_function("() => document.getElementById('rama-note')"
                                   ".textContent.startsWith('φ and ψ of')")
            listed = page.eval_on_selector_all("#rama-residue option",
                                               "o => o.map((x) => x.textContent)")
            shown = "(sel) => [...document.querySelectorAll(sel)].filter((e) => !e.hidden)" \
                ".map((e) => e.textContent.trim())"
            key_before = page.evaluate(f"({shown})('#rama-key li')")
            page.select_option("#rama-residue", label="A ALA 55")
            key_after = page.evaluate(f"({shown})('#rama-key li')")
            path = page.evaluate(f"() => {rama}.state.path.length")
            selected = page.evaluate(f"() => {VIEWER}.STATE.selection.residues")
            note = page.text_content("#rama-note")
            # A click on the path shows that frame.
            page.locator("#rama-canvas").scroll_into_view_if_needed()
            point = page.evaluate(f"""() => {{
                const p = {rama}.state.path[7];
                const canvas = document.getElementById('rama-canvas');
                const box = canvas.getBoundingClientRect();
                const scale = box.width / canvas.width;
                return [box.left + p.x * scale, box.top + p.y * scale];
            }}""")
            page.mouse.click(*point)
            page.wait_for_function(f"() => {VIEWER}.movie.shownFrame() === 7")
            page.wait_for_function("() => document.getElementById('rama-note')"
                                   ".textContent.includes('in frame 7')")
            # A residue selected in the structure is followed.
            page.evaluate(f"""() => {{
                const engine = {VIEWER}.STATE.engine;
                const residue = {{chain: 'A', resi: 193, icode: '', resn: 'GLY'}};
                {VIEWER}.selectResidues([residue], engine.atomsOfResidues([residue]));
            }}""")
            followed = page.input_value("#rama-residue")
            name = page.evaluate(f"() => {rama}.state.data.residues[{rama}.state.chosen]")
            # A click on a residue's dot follows it.
            page.locator("#rama-canvas").scroll_into_view_if_needed()
            dot = page.evaluate(f"""() => {{
                const d = {rama}.state.dots.find((d) => d.residue === 30);
                const canvas = document.getElementById('rama-canvas');
                const box = canvas.getBoundingClientRect();
                const scale = box.width / canvas.width;
                {rama}.state.path = [];
                return [box.left + d.x * scale, box.top + d.y * scale];
            }}""")
            page.mouse.click(*dot)
            page.wait_for_function(f"() => {rama}.state.chosen !== null"
                                   f" && {VIEWER}.STATE.selection.residues[0].resi !== 193")
            # The residue followed is the one whose dot was clicked: the
            # nearest to the click (another may sit on the same place).
            apart = page.evaluate(f"""(at) => {{
                const s = {rama}.state;
                const canvas = document.getElementById('rama-canvas');
                const box = canvas.getBoundingClientRect();
                const scale = canvas.width / box.width;
                const x = (at[0] - box.left) * scale;
                const y = (at[1] - box.top) * scale;
                const d = s.dots.find((d) => d.residue === s.chosen);
                const near = Math.min(...s.dots.map((o) => Math.hypot(o.x - x, o.y - y)));
                return [Math.hypot(d.x - x, d.y - y), near];
            }}""", dot)
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    # The key: each mark beside what it is, the residue chosen named.
    assert key_before == ["All residues, all frames (darker where more often)",
                          "Each residue at the frame shown (hollow: glycine)",
                          "Choose a residue above, or click one in the structure, to follow it "
                          "over the frames."]
    assert key_after[2:] == ["A ALA 55 over the frames, faint at the first, solid at the last",
                             "A ALA 55 at the frame shown"]
    assert listed[0] == "None chosen" and "A ILE 16" in listed and "A GLY 193" in listed
    assert path == FRAMES
    assert selected == [{"chain": "A", "resi": 55, "icode": "", "resn": "ALA"}]
    assert note.startswith("A ALA 55 in frame 0: φ ")
    assert name == ["A", 193, "", "GLY"] and followed != ""
    assert apart[0] == pytest.approx(apart[1], abs=1e-6)
