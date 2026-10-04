"""The frames played are fitted to the first frame, the starting structure or
the deposited one.

Fitted to the first frame, a superposed trajectory shows how each frame
differs from where the run began its production; a reader comparing with the
crystal asks how it differs from the structure the study was given. The frames
can now be fitted to the structure the run started from (as setup prepared
it: the frames' own topology file) or to the deposited structure
(``setup/input.pdb``), whose backbone atoms are matched to the frames' by
chain, residue number and name: the first of alternate locations, the first
model, residues it lacks left out of the fit.
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")

from fastmdxplora.gui.trajectory_frames import (  # noqa: E402
    frames_info,
    superposed_frames,
    superposed_name,
)
from tests.test_the_frames_are_superposed_as_asked import _rotation  # noqa: E402

FRAMES = 5
BACKBONE = ("N", "CA", "C", "O")


def _helix() -> tuple[md.Topology, np.ndarray]:
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
    topology.create_standard_bonds()
    return topology, np.array(xyz)


@pytest.fixture(scope="module")
def study(tmp_path_factory) -> Path:
    """A helix of twelve alanines tumbling from frame to frame; the structure
    the run started from turned another way again, and a deposited file of
    eleven of its residues, with alternate locations, a water and a second
    model."""
    root = tmp_path_factory.mktemp("fitted") / "study"
    (root / "simulation").mkdir(parents=True)
    (root / "setup").mkdir()
    topology, base = _helix()
    rng = np.random.default_rng(7)
    frames = [(base + rng.normal(0, 0.02, base.shape) - base.mean(axis=0))
              @ _rotation(rng).T + rng.uniform(1, 3, 3) for _ in range(FRAMES)]
    start = (base - base.mean(axis=0)) @ _rotation(rng).T + [4.0, 4.0, 4.0]
    md.Trajectory(start[None].astype(np.float32), topology).save_pdb(
        str(root / "simulation" / "trajectory_topology.pdb"))
    md.Trajectory(np.array(frames, dtype=np.float32), topology).save_dcd(
        str(root / "simulation" / "production.dcd"))
    (root / "simulation" / "live_status.json").write_text(json.dumps(
        {"status": "completed", "stage": "production"}), encoding="utf-8")
    # The deposited structure: another frame of reference, residue 12
    # missing, residue 3 in two alternate locations (B far away).
    crystal = (base - base.mean(axis=0)) @ _rotation(rng).T * 10 + [20.0, -5.0, 3.0]
    lines = ["HEADER    TEST", "MODEL        1"]
    serial = 0
    for atom in topology.atoms:
        if atom.residue.resSeq == 12:
            continue
        x, y, z = crystal[atom.index]
        locations = ("A", "B") if atom.residue.resSeq == 3 else (" ",)
        for location in locations:
            serial += 1
            shift = 50.0 if location == "B" else 0.0
            lines.append(f"ATOM  {serial:5d} {atom.name:<4s}{location}ALA A"
                         f"{atom.residue.resSeq:4d}    {x + shift:8.3f}{y:8.3f}{z:8.3f}"
                         f"  1.00 20.00           {atom.element.symbol:>2s}")
    lines += ["HETATM 9000  O   HOH A 101      0.000   0.000   0.000  1.00 30.00           O",
              "ENDMDL", "MODEL        2",
              "ATOM  9001  N   ALA A   1     99.000  99.000  99.000  1.00 20.00           N",
              "ENDMDL", "END"]
    (root / "setup" / "input.pdb").write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert frames_info(root)["available"]
    return root


def _frames(study: Path, name: str = "frames.dcd") -> md.Trajectory:
    return md.load_dcd(str(study / "simulation" / name),
                       top=str(study / "simulation" / "frames_topology.pdb"))


def test_fitted_to_the_starting_structure(study):
    said = superposed_frames(study, "backbone", to="start")
    assert said == {"ok": True, "file": "frames_superposed_backbone_to_start.dcd",
                    "said": "the protein's backbone (48 atoms), fitted to the structure the "
                            "run started from", "atoms": 48, "to": "start"}
    played = _frames(study)
    start = md.load_pdb(str(study / "simulation" / "frames_topology.pdb"))
    backbone = played.topology.select("protein and backbone")
    written = _frames(study, said["file"])
    expected = played.superpose(start, atom_indices=backbone)
    assert written.xyz == pytest.approx(expected.xyz, abs=2e-4)
    # Each frame as near the starting structure as a fit can bring it.
    left = np.sqrt(((written.xyz[:, backbone] - start.xyz[0, backbone]) ** 2)
                   .sum(axis=2).mean(axis=1))
    assert left == pytest.approx(md.rmsd(_frames(study), start, 0, atom_indices=backbone),
                                 abs=2e-4)
    # And not where the first frame left them.
    first = _frames(study, superposed_frames(study, "backbone")["file"])
    assert np.abs(first.xyz - written.xyz).max() > 0.5


def test_fitted_to_the_deposited_structure_by_chain_residue_and_name(study):
    said = superposed_frames(study, "backbone", to="deposited")
    assert said["ok"] and said["file"] == "frames_superposed_backbone_to_deposited.dcd"
    assert said["said"] == ("the protein's backbone (48 atoms), fitted to the deposited "
                            "structure (setup/input.pdb, 44 of 48 atoms matched)")
    assert said["atoms"] == 44
    played = _frames(study)
    topology = played.topology
    matched = [a.index for a in topology.atoms
               if a.name in BACKBONE and a.residue.resSeq != 12]
    # The deposited coordinates of those atoms, location A, model 1, in nm.
    rows = {}
    for line in (study / "setup" / "input.pdb").read_text().splitlines():
        if line.startswith("ENDMDL"):
            break
        if line.startswith("ATOM") and line[16] in " A":
            rows.setdefault((int(line[22:26]), line[12:16].strip()),
                            [float(line[30:38]), float(line[38:46]), float(line[46:54])])
    reference = np.array([rows[(topology.atom(i).residue.resSeq, topology.atom(i).name)]
                          for i in matched]) / 10.0
    written = _frames(study, said["file"])
    left = np.sqrt(((written.xyz[:, matched] - reference) ** 2).sum(axis=2).mean(axis=1))
    target = md.Trajectory(reference[None].astype(np.float32), topology.subset(matched))
    best = md.rmsd(played.atom_slice(matched), target, 0)
    assert left == pytest.approx(best, abs=2e-4)
    # The frames sit in the crystal's frame of reference, near its atoms.
    assert left.max() < 0.1


def test_what_cannot_be_fitted_to_is_said(study, tmp_path):
    assert superposed_name("backbone", None, 5, "sideways")[2] == (
        "Frames are fitted to the first frame, the starting structure or the deposited "
        "structure.")
    assert not superposed_frames(study, "backbone", to="sideways")["ok"]
    deposited = study / "setup" / "input.pdb"
    kept = deposited.read_text()
    try:
        deposited.write_text(kept.replace("ALA A", "ALA Z"))
        said = superposed_frames(study, "backbone", to="deposited")
        assert said == {"ok": False, "reason": "The deposited structure's backbone could not "
                        "be matched to the frames' by chain, residue number and atom name."}
        lines = kept.splitlines()
        first = next(i for i, line in enumerate(lines) if line.startswith("ATOM"))
        lines[first] = lines[first][:30] + "   x.xxx" + lines[first][38:]
        deposited.write_text("\n".join(lines) + "\n")
        said = superposed_frames(study, "backbone", to="deposited")
        assert said == {"ok": False, "reason": "The deposited structure's coordinates could "
                        "not be read."}
        deposited.unlink()
        said = superposed_frames(study, "backbone", to="deposited")
        assert said == {"ok": False, "reason": "The study has no deposited structure "
                        "(setup/input.pdb) to fit to."}
    finally:
        deposited.write_text(kept)


def test_the_server_and_a_saved_view_carry_it(study, monkeypatch, tmp_path):
    from fastmdxplora.gui.saved_views import _checked
    from fastmdxplora.gui.server import start_dashboard_session

    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path / "settings"))
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    base = session.url.rstrip("/")
    try:
        answer = json.loads(urllib.request.urlopen(
            base + "/api/frames-superposed?on=backbone&to=deposited", timeout=30).read())
        (study / "simulation" / "frames_superposed_backbone_to_start.dcd").unlink(
            missing_ok=True)
        sent = urllib.request.urlopen(base + "/structure/frames.dcd?superposed=backbone"
                                      "&to=start", timeout=30).read()
        refused = json.loads(urllib.request.urlopen(
            base + "/api/frames-superposed?on=backbone&to=sideways", timeout=30).read())
    finally:
        session.server.shutdown()
    assert answer["ok"] and answer["to"] == "deposited" and "to=deposited" in answer["url"]
    assert sent == (study / "simulation" / "frames_superposed_backbone_to_start.dcd").read_bytes()
    assert not refused["ok"]
    camera = {"position": [0, 0, 50], "target": [0, 0, 0], "up": [0, 1, 0]}
    assert _checked({"camera": camera, "superposed": "backbone",
                     "superposed_to": "deposited"})["superposed_to"] == "deposited"
    assert "superposed_to" not in _checked({"camera": camera, "superposed_to": "elsewhere"})


def test_a_scene_is_of_the_frame_fitted_as_the_view_says(study):
    from fastmdxplora.scenes import build_scene

    (study / "setup" / "topology.pdb").write_text(
        (study / "simulation" / "trajectory_topology.pdb").read_text(encoding="utf-8"),
        encoding="utf-8")
    built = build_scene(study, {"frame": 2, "superposed": "backbone",
                                "superposed_to": "deposited"})
    assert built["ok"], built
    pdb = built["files"]["structure.pdb"].decode() if isinstance(
        built["files"]["structure.pdb"], bytes) else built["files"]["structure.pdb"]
    atoms = [line for line in pdb.splitlines() if line.startswith(("ATOM", "HETATM"))]
    fitted = _frames(study, "frames_superposed_backbone_to_deposited.dcd")
    xyz = np.array([[float(a[30:38]), float(a[38:46]), float(a[46:54])] for a in atoms])
    assert xyz == pytest.approx(fitted.xyz[2] * 10, abs=2e-3)


def test_the_viewer_fits_them_as_chosen(study):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

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
            page.select_option("#traj-superpose-to", "deposited")
            assert page.evaluate(f"() => {state}.superposedUrl") is None
            page.select_option("#traj-superpose", "backbone")
            page.wait_for_function(f"() => {state}.appliedTo === 'deposited'")
            deposited = page.evaluate(first_ca)
            said = page.get_attribute("#traj-superpose-label", "data-said")
            view = page.evaluate("() => window.FastMDXMoleculeViewer.viewNow()")
            page.select_option("#traj-superpose-to", "start")
            page.wait_for_function(f"() => {state}.appliedTo === 'start'")
            start = page.evaluate(first_ca)
            # A view saved fitted to the deposited structure is shown so again.
            page.evaluate("(view) => window.FastMDXMoleculeViewer.showView(view)", view)
            page.wait_for_function(f"() => {state}.appliedTo === 'deposited'")
            chosen = page.input_value("#traj-superpose-to")
            browser.close()
    finally:
        session.server.shutdown()
    assert view["superposed_to"] == "deposited" and chosen == "deposited"
    assert said.endswith("fitted to the deposited structure (setup/input.pdb, 44 of 48 "
                         "atoms matched)")
    for name, seen in (("deposited", deposited), ("start", start)):
        fitted = _frames(study, f"frames_superposed_backbone_to_{name}.dcd")
        ca = fitted.topology.select("resSeq 1 and name CA")[0]
        assert [seen["x"], seen["y"], seen["z"]] == pytest.approx(
            (fitted.xyz[3, ca] * 10).tolist(), abs=1e-2), name
    assert errors == []
