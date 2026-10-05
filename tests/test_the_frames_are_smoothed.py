"""The frames played can be smoothed over a few frames, once fitted.

Thermal jitter hides a slow motion: a hinge closing or a loop folding over
is easier seen with each atom's position averaged over the frames either
side. The frames are smoothed after they are fitted, since an average of a
molecule turning is a molecule shrunk; frames shown as written are fitted on
the backbone first. A centred average moves no feature in time, and over
fewer frames at the ends, where there are fewer.
"""

from __future__ import annotations

import json
import urllib.request

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")

from fastmdxplora.gui.trajectory_frames import (  # noqa: E402
    smoothed,
    superposed_frames,
    superposed_name,
)
from tests.test_the_frames_are_fitted_to_a_structure import _frames, made_study  # noqa: E402


@pytest.fixture(scope="module")
def study(tmp_path_factory):
    return made_study(tmp_path_factory.mktemp("smoothed") / "study")


def test_a_centred_average_over_fewer_at_the_ends():
    rng = np.random.default_rng(1)
    xyz = rng.normal(size=(7, 4, 3)).astype(np.float32)
    three = smoothed(xyz, 3)
    assert three[0] == pytest.approx(xyz[0:2].mean(axis=0), abs=1e-6)
    assert three[3] == pytest.approx(xyz[2:5].mean(axis=0), abs=1e-6)
    assert three[6] == pytest.approx(xyz[5:7].mean(axis=0), abs=1e-6)
    assert smoothed(xyz, 15)[3] == pytest.approx(xyz.mean(axis=0), abs=1e-6)
    assert smoothed(xyz, 1) is xyz and three.dtype == xyz.dtype
    # A steady motion is kept where the window is whole: nothing moves in time.
    steady = np.arange(9, dtype=np.float32)[:, None, None] * np.ones((1, 2, 3), np.float32)
    assert smoothed(steady, 5)[2:7] == pytest.approx(steady[2:7], abs=1e-5)


def test_the_fitted_frames_are_smoothed_and_their_jitter_falls(study):
    said = superposed_frames(study, "backbone", smooth=5)
    assert said["ok"] and said["file"] == "frames_superposed_backbone_smooth5.dcd"
    assert said["smooth"] == 5 and said["said"].endswith(
        "fitted to the first frame, smoothed over 5 frames")
    fitted = _frames(study, superposed_frames(study, "backbone")["file"])
    written = _frames(study, said["file"])
    assert written.xyz == pytest.approx(smoothed(fitted.xyz, 5), abs=2e-5)
    # Each atom wanders less about its mean.
    def spread(t):
        return float(np.sqrt(((t.xyz - t.xyz.mean(axis=0)) ** 2).sum(axis=2).mean()))
    assert spread(written) < 0.6 * spread(fitted)
    assert superposed_frames(study, "backbone", to="deposited", smooth=3)["file"] == (
        "frames_superposed_backbone_to_deposited_smooth3.dcd")


@pytest.mark.parametrize("smooth", [4, 0, "x", 31])
def test_a_window_not_offered_is_refused(smooth):
    assert superposed_name("backbone", None, 5, "first", smooth)[2] == (
        "Frames are smoothed over 3, 5, 9, 15 frames, or not.")


def test_the_server_and_a_saved_view_carry_it(study, monkeypatch, tmp_path):
    from fastmdxplora.gui.saved_views import _checked
    from fastmdxplora.gui.server import start_dashboard_session

    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path / "settings"))
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    base = session.url.rstrip("/")
    try:
        answer = json.loads(urllib.request.urlopen(
            base + "/api/frames-superposed?on=backbone&smooth=9", timeout=30).read())
        target = study / "simulation" / "frames_superposed_backbone_smooth9.dcd"
        target.unlink()
        sent = urllib.request.urlopen(base + "/structure/frames.dcd?superposed=backbone"
                                      "&smooth=9", timeout=30).read()
    finally:
        session.server.shutdown()
    assert answer["ok"] and answer["smooth"] == 9 and "smooth=9" in answer["url"]
    assert sent == target.read_bytes()
    camera = {"position": [0, 0, 50], "target": [0, 0, 0], "up": [0, 1, 0]}
    assert _checked({"camera": camera, "smoothed_over": 9})["smoothed_over"] == 9
    for wrong in (True, 4, "9"):
        assert "smoothed_over" not in _checked({"camera": camera, "smoothed_over": wrong})


def test_the_viewer_fits_then_smooths(study):
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
            page.evaluate("() => window.FastMDXMoleculeViewer.movie.showFrame(2)")
            page.select_option("#traj-smooth", "3")
            page.wait_for_function(f"() => {state}.appliedSmooth === 3")
            on = page.input_value("#traj-superpose")
            smoothed_ca = page.evaluate(first_ca)
            view = page.evaluate("() => window.FastMDXMoleculeViewer.viewNow()")
            page.select_option("#traj-superpose", "none")
            page.wait_for_function(f"() => {state}.superposedUrl === null")
            after = (page.input_value("#traj-smooth"),
                     page.evaluate(f"() => {state}.smoothedOver"))
            browser.close()
    finally:
        session.server.shutdown()
    assert on == "backbone" and view["smoothed_over"] == 3
    assert after == ("1", 1)
    written = _frames(study, "frames_superposed_backbone_smooth3.dcd")
    ca = written.topology.select("resSeq 1 and name CA")[0]
    assert [smoothed_ca["x"], smoothed_ca["y"], smoothed_ca["z"]] == pytest.approx(
        (written.xyz[2, ca] * 10).tolist(), abs=1e-2)
    assert errors == []
