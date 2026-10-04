"""The frames arrive in pieces, as XTC, and play from the first.

The Viewer fetched the frames whole, as a DCD of up to 120 MB, before it
showed one: over a tunnel, or from a hosted GUI, a large system waited for
all of it. The frames are now sent as XTC, about a third of a DCD's size at
a hundredth of an angstrom, in pieces the Viewer plays from the first while
the rest arrive.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")


@pytest.fixture(scope="module")
def study(tmp_path_factory) -> Path:
    from fastmdxplora.gui.trajectory_frames import frames_info
    from tests.test_the_cartoon_is_dssp_of_each_frame import _helical_study

    root = _helical_study(tmp_path_factory.mktemp("pieces") / "study", analyse=False)
    assert frames_info(root)["available"]
    return root


@pytest.fixture
def small_pieces(monkeypatch, study):
    """Pieces of two frames each, so a small study has several."""
    from fastmdxplora.gui import trajectory_frames

    atoms = md.load_topology(str(study / "simulation" / "frames_topology.pdb")).n_atoms
    monkeypatch.setattr(trajectory_frames, "PIECE_ATOM_FRAMES", 2 * atoms)
    folder = study / "simulation" / "frames_pieces"
    if (folder / "index.json").is_file():
        (folder / "index.json").unlink()
    return atoms


def test_the_pieces_are_the_frames(study, small_pieces):
    from fastmdxplora.gui.trajectory_frames import frames_pieces

    said = frames_pieces(study)
    assert said["ok"] and said["total"] == 6 and said["atoms"] == small_pieces
    assert [(p["start"], p["frames"]) for p in said["pieces"]] == [(0, 2), (2, 2), (4, 2)]
    assert said["pieces"][1]["url"] == "/structure/frames-piece.xtc?k=1"
    assert said["bytes"] < said["dcd_bytes"] / 2
    simulation = study / "simulation"
    whole = md.load_dcd(str(simulation / "frames.dcd"), top=str(simulation / "frames_topology.pdb"))
    pieces = md.join([md.load_xtc(str(simulation / "frames_pieces" / f"piece_{k}.xtc"),
                                  top=str(simulation / "frames_topology.pdb")) for k in range(3)])
    # To a thousandth of a nanometre, as XTC keeps them.
    assert np.abs(pieces.xyz - whole.xyz).max() <= 0.0006
    stamp = (simulation / "frames_pieces" / "piece_0.xtc").stat().st_mtime_ns
    assert frames_pieces(study) == said
    assert (simulation / "frames_pieces" / "piece_0.xtc").stat().st_mtime_ns == stamp


def test_no_frames_no_pieces(tmp_path):
    from fastmdxplora.gui.trajectory_frames import frames_pieces

    assert frames_pieces(tmp_path) == {"ok": False, "reason": "There are no frames yet."}


def test_the_server_sends_them(study, small_pieces):
    import urllib.error
    import urllib.request

    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)

    def get(path):
        try:
            with urllib.request.urlopen(session.url + path, timeout=120) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, b""

    try:
        said = json.loads(get("/api/frames-pieces")[1])
        assert said["ok"] and len(said["pieces"]) == 3
        status, piece = get(said["pieces"][2]["url"])
        assert status == 200
        assert piece == (study / "simulation" / "frames_pieces" / "piece_2.xtc").read_bytes()
        assert get("/structure/frames-piece.xtc?k=9")[0] == 404
        assert get("/structure/frames-piece.xtc?k=..%2F..%2Fx")[0] == 404
        status, xtc = get("/structure/frames.dcd?as=xtc")
        assert status == 200
        assert xtc == (study / "simulation" / "frames.xtc").read_bytes()
        superposed = json.loads(get("/api/frames-superposed?on=backbone")[1])
        status, fitted = get(superposed["url"] + "&as=xtc")
        assert status == 200 and fitted == (
            study / "simulation" / "frames_superposed_backbone.xtc").read_bytes()
    finally:
        session.server.shutdown()


def test_the_viewer_plays_from_the_first_piece(study, small_pieces):
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
            page.wait_for_function(f"() => {state}.model")
            page.evaluate("async () => window.FastMDXMoleculeViewer.loadPlayback("
                          "await (await fetch('/api/frames-info')).json())")
            page.wait_for_function(f"() => {state}.framesPieces"
                                   f" && {state}.framesPieces.loaded === 6"
                                   f" && {state}.engine.frameCount() === 6")
            pieces = page.evaluate(f"() => {state}.framesPieces")
            loaded = page.get_attribute("#trajectory-row", "data-frames-loaded")
            page.evaluate("() => window.dispatchEvent(new CustomEvent("
                          "'dashboard:trajectory-seek', {detail: {frame: 5}}))")
            page.wait_for_function(f"() => {state}.engine.frame() === 5")
            # Superposed: the fitted frames read as XTC in place of these.
            page.select_option("#traj-superpose", "backbone")
            page.wait_for_function(f"() => {state}.superposedUrl")
            after = page.evaluate(f"() => [{state}.engine.frameCount(), {state}.engine.frame(),"
                                  f" {state}.playbackFrames]")
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert pieces == {"total": 6, "loaded": 6, "pieces": 3}
    assert loaded == "6"
    assert after == [6, 5, 6]
