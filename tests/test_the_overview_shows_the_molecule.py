"""The Overview's preview shows the molecule, wherever its frames are written.

The preview framed the prepared system when the page opened, and kept that
camera for every frame after it, which is right for frames of the same
system in the same place. The engine writes its frames about its own origin,
several nanometres from where setup centred the system, and a finished study
opened with the preview looking at empty space: a black box under
"Structure". A camera that no longer frames the structure is now set again.

A peptide of a few residues was drawn as a cartoon alone, which has nothing
to shape a ribbon from; its atoms are drawn as well. And the viewer's label
called a live frame's step number its frame: "frame 6000" of a run that had
written a hundred.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")
pytest.importorskip("playwright.sync_api")

#: How far from the prepared system the engine's frame is, in nm.
SHIFT_NM = 4.0


def _peptide(residues: int) -> md.Trajectory:
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
            xyz.append([0.38 * index + 0.1 * offset, 0.05 * offset, 0.0])
    return md.Trajectory(np.array(xyz)[None], topology)


def _study(root: Path, residues: int) -> Path:
    (root / "setup").mkdir(parents=True)
    (root / "simulation").mkdir()
    peptide = _peptide(residues)
    peptide.save_pdb(str(root / "setup" / "topology.pdb"))
    moved = md.Trajectory(peptide.xyz + SHIFT_NM, peptide.topology)
    moved.save_pdb(str(root / "simulation" / "live_frame.pdb"))
    (root / "simulation" / "live_frame_index.json").write_text(json.dumps({
        "live_frame_available": True, "live_frame_index": 6000,
        "live_frame_updated_at": "2026-09-29T05:36:01+00:00",
        "live_frame_mtime": 1.0, "simulation_stage": "production",
        "simulation_time_ns": 0.012}), encoding="utf-8")
    (root / "simulation" / "live_status.json").write_text(json.dumps({
        "status": "completed", "stage": "production", "current_step": 6000,
        "total_steps": 6000, "current_frame_count": 100}), encoding="utf-8")
    return root


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        launched = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        yield launched
        launched.close()


def _overview(browser, study: Path):
    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    # 3Dmol on a software renderer, beside a full suite on two cores, took
    # longer than thirty seconds to draw once.
    page.set_default_timeout(60000)
    page.goto(session.url + "#overview", wait_until="domcontentloaded")
    if not page.evaluate("() => !!document.createElement('canvas').getContext('webgl')"):
        pytest.skip("this browser has no WebGL, so 3Dmol cannot draw")
    # Until the engine's frame has replaced the prepared system.
    page.wait_for_function(
        "() => { const S = window.FastMDXMoleculeViewer && window.FastMDXMoleculeViewer.STATE;"
        " return S && S.miniModel && S.liveFrameIndex != null; }", timeout=60000)
    page.wait_for_timeout(500)
    return session, page


def _lit(page) -> float:
    """The share of the preview that is not background."""
    from io import BytesIO

    from PIL import Image

    shot = Image.open(BytesIO(page.locator("#mini-preview-canvas").screenshot())).convert("L")
    pixels = shot.tobytes()
    return sum(1 for value in pixels if value > 60) / len(pixels)


def test_the_preview_follows_a_frame_written_elsewhere(browser, tmp_path) -> None:
    session, page = _overview(browser, _study(tmp_path / "study", residues=12))
    try:
        centre = page.evaluate("""() => {
            const v = window.FastMDXMoleculeViewer.STATE.miniViewer;
            const atoms = v.getModel().selectedAtoms({});
            const mean = axis => atoms.reduce((s, a) => s + a[axis], 0) / atoms.length;
            const view = v.getView();
            return [mean('x') + view[0], mean('y') + view[1], mean('z') + view[2]];
        }""")
        # The camera is on the frame, which is 40 A from the prepared system.
        assert max(abs(c) for c in centre) < 10.0
        assert _lit(page) > 0.005
    finally:
        page.close()
        session.server.shutdown()


def test_a_short_peptide_has_its_atoms_drawn(browser, tmp_path) -> None:
    session, page = _overview(browser, _study(tmp_path / "study", residues=3))
    try:
        styles = page.evaluate("""() => {
            const S = window.FastMDXMoleculeViewer.STATE;
            return [S.miniViewer, S.viewer].filter(Boolean).map(v =>
                v.getModel().selectedAtoms({atom: 'CA'}).map(a => !!(a.style && a.style.stick)));
        }""")
        assert styles and all(all(drawn) for drawn in styles)
    finally:
        page.close()
        session.server.shutdown()


def test_a_protein_is_a_cartoon_without_its_atoms(browser, tmp_path) -> None:
    session, page = _overview(browser, _study(tmp_path / "study", residues=12))
    try:
        sticks = page.evaluate("""() => window.FastMDXMoleculeViewer.STATE.miniViewer
            .getModel().selectedAtoms({atom: 'CA'}).some(a => a.style && a.style.stick)""")
        assert sticks is False
    finally:
        page.close()
        session.server.shutdown()


def test_a_live_frame_is_named_by_its_step(browser, tmp_path) -> None:
    session, page = _overview(browser, _study(tmp_path / "study", residues=12))
    try:
        page.wait_for_function(
            "() => document.getElementById('overlay-frame').textContent.startsWith('step')",
            timeout=60000)
        label = page.evaluate("() => document.getElementById('overlay-frame').textContent")
        assert label in ("step 6,000", "step 6000")
    finally:
        page.close()
        session.server.shutdown()
