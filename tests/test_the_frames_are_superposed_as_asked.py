"""The frames played can be superposed on the protein's backbone or a pocket.

The frames were played as written, made whole and centred: a protein that
tumbled tumbled on the screen, and how a ligand sat in its pocket had to be
judged through the turning of both. Each frame can now be fitted to the
first, by MDTraj's least squares, on the protein's backbone or on the
backbone of the residues within a cutoff of the ligand in the first frame.
The Viewer reads the frames so in place of those shown, at the same frame.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")

from fastmdxplora.gui.trajectory_frames import frames_info, superposed_frames  # noqa: E402

FRAMES = 5


def _rotation(rng) -> np.ndarray:
    q = rng.normal(size=4)
    w, x, y, z = q / np.linalg.norm(q)
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


@pytest.fixture(scope="module")
def study(tmp_path_factory) -> Path:
    """Twelve alanines on a helix and a ligand of three atoms beside residues
    3 to 5, tumbling from frame to frame, the ligand drifting."""
    root = tmp_path_factory.mktemp("superposed") / "study"
    (root / "simulation").mkdir(parents=True)
    topology = md.Topology()
    chain = topology.add_chain()
    xyz = []
    for r in range(12):
        residue = topology.add_residue("ALA", chain, resSeq=r + 1)
        angle = np.radians(100.0 * r)
        centre = np.array([0.23 * np.cos(angle), 0.23 * np.sin(angle), 0.15 * r])
        for k, (name, element) in enumerate((("N", md.element.nitrogen),
                                             ("CA", md.element.carbon),
                                             ("C", md.element.carbon),
                                             ("O", md.element.oxygen),
                                             ("CB", md.element.carbon))):
            topology.add_atom(name, element, residue)
            xyz.append(centre + 0.1 * np.array([np.cos(angle + k), np.sin(angle + k), 0.2 * k]))
    ligand = topology.add_residue("LIG", topology.add_chain(), resSeq=1)
    for k in range(3):
        topology.add_atom(f"C{k}", md.element.carbon, ligand)
        xyz.append([0.45 + 0.14 * k, 0.0, 0.6])
    topology.create_standard_bonds()
    base = np.array(xyz)
    rng = np.random.default_rng(3)
    frames = []
    for f in range(FRAMES):
        moved = base.copy()
        moved[-3:] += [0.05 * f, 0.0, 0.0]
        moved += rng.normal(0, 0.01, moved.shape)
        frames.append((moved - moved.mean(axis=0)) @ _rotation(rng).T + rng.uniform(1, 3, 3))
    trajectory = md.Trajectory(np.array(frames, dtype=np.float32), topology)
    trajectory[0].save_pdb(str(root / "simulation" / "trajectory_topology.pdb"))
    trajectory.save_dcd(str(root / "simulation" / "production.dcd"))
    (root / "simulation" / "live_status.json").write_text(json.dumps(
        {"status": "completed", "stage": "production"}), encoding="utf-8")
    assert frames_info(root)["available"]
    return root


def _frames(study: Path, name: str = "frames.dcd") -> md.Trajectory:
    return md.load_dcd(str(study / "simulation" / name),
                       top=str(study / "simulation" / "frames_topology.pdb"))


def test_on_the_backbone_is_mdtrajs_fit_on_the_first_frame(study):
    said = superposed_frames(study, "backbone")
    assert said == {"ok": True, "file": "frames_superposed_backbone.dcd",
                    "said": "the protein's backbone (48 atoms), fitted to the first frame",
                    "atoms": 48, "to": "first", "smooth": 1}
    played = _frames(study)
    expected = played.superpose(played, frame=0,
                                atom_indices=played.topology.select("protein and backbone"))
    written = _frames(study, said["file"])
    assert written.xyz == pytest.approx(expected.xyz, abs=2e-4)
    assert written.unitcell_vectors is None
    # The fit is the best there is: the backbone's RMSD to the first frame
    # is what MDTraj's own fit leaves.
    backbone = written.topology.select("protein and backbone")
    left = np.sqrt(((written.xyz[:, backbone] - written.xyz[0, backbone]) ** 2)
                   .sum(axis=2).mean(axis=1))
    assert left == pytest.approx(md.rmsd(played, played, 0, atom_indices=backbone), abs=2e-4)


def test_on_the_pocket_is_the_fit_on_the_residues_near_the_ligand(study):
    said = superposed_frames(study, "pocket", ligand="lig", cutoff_angstrom=5)
    played = _frames(study)
    topology = played.topology
    near = md.compute_neighbors(played[0], 0.5, topology.select("resname LIG"),
                                haystack_indices=topology.select("protein"))[0]
    residues = sorted({topology.atom(int(i)).residue.index for i in near})
    assert residues and len(residues) < 12
    atoms = [a.index for a in topology.atoms
             if a.residue.index in residues and a.name in ("N", "CA", "C", "O")]
    assert said["ok"] and said["file"] == "frames_superposed_pocket_LIG_5.00.dcd"
    assert said["said"] == (f"the backbone of the {len(residues)} residues within 5 Å "
                            f"of LIG in the first frame ({len(atoms)} atoms), "
                            "fitted to the first frame")
    expected = played.superpose(played, frame=0, atom_indices=atoms)
    assert _frames(study, said["file"]).xyz == pytest.approx(expected.xyz, abs=2e-4)


@pytest.mark.parametrize("on,ligand,cutoff,said", [
    ("sideways", None, 5, "superposed on backbone or pocket"),
    ("pocket", None, 5, "no ligand was named"),
    ("pocket", "L;G", 5, "no ligand was named"),
    ("pocket", "LIG", 50, "1 to 20"),
    ("pocket", "LIG", "x", "1 to 20"),
    ("pocket", "HEM", 5, "There is no HEM in the frames."),
    ("pocket", "LIG", 1, "No protein residue is within 1"),
])
def test_what_is_refused(study, on, ligand, cutoff, said):
    answer = superposed_frames(study, on, ligand=ligand, cutoff_angstrom=cutoff)
    assert not answer["ok"] and said in answer["reason"]


def test_no_frames_no_superposition(tmp_path):
    assert superposed_frames(tmp_path, "backbone") == {
        "ok": False, "reason": "There are no frames to superpose yet."}


def test_they_are_written_once_and_again_after_the_frames(study):
    target = study / "simulation" / "frames_superposed_backbone.dcd"
    superposed_frames(study, "backbone")
    written = target.stat().st_mtime_ns
    superposed_frames(study, "backbone")
    assert target.stat().st_mtime_ns == written
    frames = study / "simulation" / "frames.dcd"
    later = written + 10_000_000_000
    os.utime(frames, ns=(later, later))
    superposed_frames(study, "backbone")
    assert target.stat().st_mtime_ns > written


def test_the_server_gives_their_address_and_sends_them(study):
    from fastmdxplora.gui.server import GETS_ANSWERED_BEYOND_LOOPBACK, start_dashboard_session

    assert "/api/frames-superposed" in GETS_ANSWERED_BEYOND_LOOPBACK
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    base = session.url.rstrip("/")
    try:
        answer = json.loads(urllib.request.urlopen(
            base + "/api/frames-superposed?on=pocket&ligand=LIG&cutoff=5", timeout=30).read())
        sent = urllib.request.urlopen(base + answer["url"], timeout=30).read()
        refused = json.loads(urllib.request.urlopen(
            base + "/api/frames-superposed?on=pocket", timeout=30).read())
        with pytest.raises(urllib.error.HTTPError) as unknown:
            urllib.request.urlopen(base + "/structure/frames.dcd?superposed=..%2Fsecrets",
                                   timeout=30)
        with pytest.raises(urllib.error.HTTPError) as sideways:
            urllib.request.urlopen(base + "/structure/frames.dcd?superposed=sideways",
                                   timeout=30)
        with pytest.raises(urllib.error.HTTPError) as absent:
            urllib.request.urlopen(base + "/structure/frames.dcd?superposed=pocket"
                                   "&ligand=HEM&cutoff=5", timeout=30)
        # Asked for by its address before it was written, it is written then.
        (study / "simulation" / "frames_superposed_backbone.dcd").unlink()
        written = urllib.request.urlopen(base + "/structure/frames.dcd?superposed=backbone",
                                         timeout=30).read()
    finally:
        session.server.shutdown()
    assert answer["ok"] and answer["url"].startswith(
        "/structure/frames.dcd?superposed=pocket&v=")
    assert "ligand=LIG" in answer["url"] and "cutoff=5" in answer["url"]
    assert sent == (study / "simulation" / "frames_superposed_pocket_LIG_5.00.dcd").read_bytes()
    assert refused == {"ok": False, "reason": "A pocket is the ligand's: no ligand was named."}
    assert unknown.value.code == sideways.value.code == absent.value.code == 404
    assert written == (study / "simulation" / "frames_superposed_backbone.dcd").read_bytes()


def test_the_viewer_reads_them_at_the_frame_shown(study):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    (study / "setup").mkdir(exist_ok=True)
    (study / "setup" / "topology.pdb").write_text(
        (study / "simulation" / "trajectory_topology.pdb").read_text(encoding="utf-8"),
        encoding="utf-8")
    state = "window.FastMDXMoleculeViewer.STATE"
    first_ca = "() => window.FastMDXMoleculeViewer.atoms({resi: 1, atom: 'CA'})[0]"
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            # The Playback starts closed; these drive what is in it.
            page.add_init_script("try { localStorage.setItem('fmx.viewerPlaybackOpen', '1'); } catch (e) {}")
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
            page.evaluate("() => window.dispatchEvent(new CustomEvent("
                          "'dashboard:trajectory-seek', {detail: {frame: 3}}))")
            page.wait_for_function(f"() => {state}.engine.frame() === 3")
            model = page.evaluate(f"() => {{ window.shown = {state}.model; return true; }}")
            as_written = page.evaluate(first_ca)
            page.select_option("#traj-superpose", "pocket")
            page.wait_for_function(f"() => {state}.superposedUrl"
                                   " && /pocket/.test(" + state + ".superposedUrl)")
            on_pocket = page.evaluate(first_ca)
            frame = page.evaluate(f"() => {state}.engine.frame()")
            kept = page.evaluate(f"() => {state}.model === window.shown")
            page.select_option("#traj-superpose", "none")
            page.wait_for_function(f"() => {state}.superposedUrl === null"
                                   f" && {state}.superposed === 'none'")
            page.wait_for_function(f"(x) => Math.abs(({first_ca})().x - x) < 1e-3",
                                   arg=as_written["x"])
            browser.close()
    finally:
        session.server.shutdown()
    assert model and kept and frame == 3
    pocket = _frames(study, "frames_superposed_pocket_LIG_5.00.dcd")
    ca = pocket.topology.select("resSeq 1 and name CA")[0]
    assert [on_pocket["x"], on_pocket["y"], on_pocket["z"]] == pytest.approx(
        (pocket.xyz[3, ca] * 10).tolist(), abs=1e-2)
    played = _frames(study)
    assert [as_written["x"], as_written["y"], as_written["z"]] == pytest.approx(
        (played.xyz[3, ca] * 10).tolist(), abs=1e-2)
    assert errors == []
