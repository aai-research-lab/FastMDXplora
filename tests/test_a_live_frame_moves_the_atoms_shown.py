"""A live frame moves the atoms the viewer shows; it is not loaded again.

Each frame a running study wrote was fetched as its whole PDB, about 81
bytes an atom, and loaded as a new structure: every representation built
again, and the atoms being measured and picked dropped and picked again.
The frame's coordinates now come alone, as a one-frame DCD of 12 bytes an
atom (`/structure/live-frame.dcd`), and the viewer moves the atoms it
already shows, as it does to play a frame: the measurement stays, its
numbers follow, and the cartoon is the new frame's DSSP. A frame of other
atoms is loaded whole, as before.
"""

from __future__ import annotations

import json
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import pytest

from tests import viewer_hooks as hooks

md = pytest.importorskip("mdtraj")

VIEWER = hooks.VIEWER


def _peptide(residues: int, shift: float = 0.0, bend: float = 0.0) -> md.Trajectory:
    topology = md.Topology()
    chain = topology.add_chain()
    xyz = []
    for index in range(residues):
        residue = topology.add_residue("ALA", chain, resSeq=index + 1)
        for offset, (name, element) in enumerate((
                ("N", md.element.nitrogen), ("CA", md.element.carbon),
                ("C", md.element.carbon), ("O", md.element.oxygen),
                ("CB", md.element.carbon))):
            topology.add_atom(name, element, residue)
            xyz.append([0.38 * index + 0.1 * offset, 0.05 * offset + bend * index ** 2, 0.0])
    return md.Trajectory(np.array(xyz)[None] + shift, topology)


def _write(root: Path, frame: md.Trajectory, step: int) -> None:
    from fastmdxplora.gui.live_frames import write_live_frame

    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "frame.pdb"
        frame.save_pdb(str(path))
        text = path.read_text(encoding="utf-8")
    write_live_frame(root / "simulation", pdb_text=text, frame_index=step,
                     stage="production", simulation_time_ns=step * 2e-6)


def _study(root: Path) -> Path:
    (root / "setup").mkdir(parents=True)
    (root / "simulation").mkdir()
    _peptide(12).save_pdb(str(root / "setup" / "topology.pdb"))
    _write(root, _peptide(12, shift=0.5), 6000)
    (root / "simulation" / "live_status.json").write_text(json.dumps({
        "status": "running", "stage": "production", "current_step": 6000,
        "total_steps": 600000, "current_frame_count": 3}), encoding="utf-8")
    return root


def _get(url: str):
    return urllib.request.urlopen(url, timeout=30)


def test_the_live_frame_s_coordinates_come_alone(tmp_path) -> None:
    from fastmdxplora.gui.server import start_dashboard_session

    study = _study(tmp_path / "study")
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        base = session.url.rstrip("/")
        answer = _get(base + "/structure/live-frame.dcd")
        data = answer.read()
        headers = answer.headers
        pdb = _get(base + "/structure/live-frame.pdb").read().decode()
        (study / "simulation" / "live_frame.pdb").unlink()
        with pytest.raises(urllib.error.HTTPError) as missing:
            _get(base + "/structure/live-frame.dcd")
    finally:
        session.server.shutdown()
    assert missing.value.code == 404
    assert headers["Cache-Control"] == "no-store"
    lines = [line for line in pdb.splitlines() if line[:6] in ("ATOM  ", "HETATM")]
    assert headers["X-FastMDX-Atoms"] == str(len(lines)) == "60"
    assert urllib.parse.unquote(headers["X-FastMDX-Fingerprint"]) == lines[0][30:54]
    # 12 bytes an atom against the PDB's 81: under a quarter for sixty atoms,
    # the DCD's header counted; and the same coordinates.
    assert len(data) < len(pdb) / 4
    path = tmp_path / "live.dcd"
    path.write_bytes(data)
    read = md.load_dcd(str(path), top=md.load_pdb(str(study / "setup" / "topology.pdb")))
    expected = [[float(line[30:38]), float(line[38:46]), float(line[46:54])] for line in lines]
    assert read.xyz[0] * 10 == pytest.approx(np.array(expected), abs=1e-4)


@pytest.mark.parametrize("text", [
    "REMARK   nothing yet\nEND\n",
    "ATOM      1  CA  ALA A   1       x.000   0.000   0.000  1.00  0.00           C\n"])
def test_a_frame_with_no_coordinates_to_send_sends_none(tmp_path, text) -> None:
    from fastmdxplora.gui.live_frames import live_frame_coordinates

    (tmp_path / "live_frame.pdb").write_text(text, encoding="utf-8")
    assert live_frame_coordinates(tmp_path) is None


def test_a_new_frame_moves_the_atoms_and_keeps_the_measurement(tmp_path) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    study = _study(tmp_path / "study")
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    live = f"{VIEWER}.STATE"
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
            page.wait_for_function(f"() => {live}.model && {live}.model.of === 'live'"
                                   f" && {live}.liveCoordinates && {live}.liveFrameIndex === 6000")
            # Each structure loaded is rendered as a model of its own.
            page.evaluate(f"() => {{ window.shown = {live}.model; }}")
            page.click('[data-action="measure"]')
            assert hooks.click(page, resi=3, atom="CA")
            assert hooks.click(page, resi=9, atom="CA")
            page.wait_for_function(f"() => {live}.engine.measurementCount() === 1")
            before = page.text_content("#measure-said .measure-values")

            bent = _peptide(12, shift=0.5, bend=0.02)
            _write(study, bent, 7000)
            page.evaluate(f"() => {VIEWER}.pollLiveFrame()")
            page.wait_for_function(f"() => {live}.liveFrameIndex === 7000")
            page.wait_for_function("(text) => document.querySelector("
                                   "'#measure-said .measure-values')?.textContent !== text",
                                   arg=before)
            moved = {
                "loaded again": page.evaluate(f"() => {live}.model !== window.shown"),
                "measured": page.evaluate(f"() => {live}.engine.measurementCount()"),
                "picks": page.evaluate(f"() => {live}.picks.length"),
                "said": page.text_content("#measure-said .measure-values"),
                "atoms": [[a["x"], a["y"], a["z"]] for resi in (3, 9)
                          for a in hooks.atoms(page, resi=resi, atom="CA")],
                "dssp": page.evaluate(f"() => {live}.secondaryStructure"),
                "step": page.text_content("#overlay-frame"),
            }

            # A frame of other atoms is loaded whole.
            _write(study, _peptide(11, shift=0.5), 8000)
            page.evaluate(f"() => {VIEWER}.pollLiveFrame()")
            page.wait_for_function(f"() => {live}.liveFrameIndex === 8000"
                                   f" && {live}.engine.atomCount() === 55")
            reloaded = page.evaluate(f"() => {live}.model !== window.shown")
            browser.close()
    finally:
        session.server.shutdown()
    assert moved["loaded again"] is False
    assert moved["measured"] == 1 and moved["picks"] == 2
    ca = bent.topology.select("name CA")
    expected = bent.xyz[0][ca[[2, 8]]] * 10
    assert np.array(moved["atoms"]) == pytest.approx(expected, abs=1e-3)
    distance = float(np.linalg.norm(expected[0] - expected[1]))
    assert f"{distance:.2f} Å" in moved["said"]
    assert moved["dssp"]["of"] == "live" and moved["dssp"]["applied"] is True
    assert moved["step"] in ("step 7,000", "step 7000")
    assert reloaded is True
    assert errors == []
