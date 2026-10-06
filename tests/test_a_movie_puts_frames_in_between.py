"""A movie with frames in between the frames played, for a smoother movie.

A study saves a frame every so often, and a movie of its frames jumps from
one to the next. The Movie section can put 1, 3 or 7 frames in between each
two, each atom moved in a straight line from its place in one frame to its
place in the next (`trajectory_frames.frames_between`, sent as
`/structure/frames.dcd?...&span=&between=`). They are not simulated, and the
movie's record says so. The frames are superposed first, since a straight
line between two places of a molecule turning is a molecule shrunk. While the
movie is made, the frames go on being named by the frames played.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from tests import viewer_hooks as hooks

md = pytest.importorskip("mdtraj")

VIEWER = "window.FastMDXMoleculeViewer"
WIDTH, HEIGHT = 640, 360


def _frames_folder(root: Path, frames: int = 5, box: bool = True) -> Path:
    """Four atoms whose x grows by 1 nm a frame, and a box that grows too."""
    simulation = root / "simulation"
    simulation.mkdir(parents=True)
    top = md.Topology()
    chain = top.add_chain()
    residue = top.add_residue("ALA", chain, resSeq=1)
    for name in ("N", "CA", "C", "O"):
        top.add_atom(name, md.element.get_by_symbol(name[0]), residue)
    xyz = np.zeros((frames, 4, 3), np.float32)
    xyz[:, :, 1] = np.arange(4)[None, :] * 0.15
    xyz[:, :, 0] = np.arange(frames)[:, None]
    lengths = (np.full((frames, 3), 3.0) + np.arange(frames)[:, None] * 0.1) if box else None
    angles = np.full((frames, 3), 90.0) if box else None
    trajectory = md.Trajectory(xyz, top, unitcell_lengths=lengths, unitcell_angles=angles)
    trajectory[0].save_pdb(str(simulation / "frames_topology.pdb"))
    trajectory.save_dcd(str(simulation / "frames.dcd"))
    return simulation / "frames.dcd"


def test_each_atom_moves_in_a_straight_line_between_frames(tmp_path):
    from fastmdxplora.gui.trajectory_frames import frames_between

    source = _frames_folder(tmp_path)
    made, reason = frames_between(source, "1:3:1", 3)
    assert reason is None and made.name == "frames_between3_1_3_1.dcd"
    tweened = md.load_dcd(str(made), top=str(source.parent / "frames_topology.pdb"))
    assert tweened.n_frames == 2 * 4 + 1
    assert tweened.xyz[:, 0, 0] == pytest.approx(1 + np.arange(9) / 4, abs=1e-5)
    assert tweened.xyz[:, 3, 1] == pytest.approx(0.45, abs=1e-5)
    assert tweened.unitcell_lengths[:, 0] == pytest.approx(3.1 + np.arange(9) * 0.025,
                                                          abs=1e-4)
    # Backwards, and every other frame.
    made, _ = frames_between(source, "4:0:2", 1)
    tweened = md.load_dcd(str(made), top=str(source.parent / "frames_topology.pdb"))
    assert tweened.xyz[:, 0, 0] == pytest.approx([4, 3, 2, 1, 0], abs=1e-5)
    # Only the last written for the frames is kept, and written once.
    assert [p.name for p in source.parent.glob("frames_between*")] == [made.name]
    stamp = made.stat().st_mtime_ns
    assert frames_between(source, "4:0:2", "1")[0].stat().st_mtime_ns == stamp
    # Frames with no box (superposed) have none in between either.
    bare = _frames_folder(tmp_path / "bare", box=False)
    made, _ = frames_between(bare, "0:1:1", 7)
    tweened = md.load_dcd(str(made), top=str(bare.parent / "frames_topology.pdb"))
    assert tweened.n_frames == 9 and tweened.unitcell_lengths is None


def test_what_cannot_be_put_in_between_is_said(tmp_path, monkeypatch):
    from fastmdxplora.gui import trajectory_frames
    from fastmdxplora.gui.trajectory_frames import frames_between

    source = _frames_folder(tmp_path)
    assert frames_between(source, "0:3", 3) == (
        None, "Frames in between are asked for as span=from:to:every and between=1, 3 or 7.")
    assert frames_between(source, "0:3:1", 2)[1] == ("A movie puts 1, 3 or 7 frames in "
                                                    "between two frames played.")
    assert frames_between(source, "0:3:0", 1)[1] == ("A movie's frames are counted from 0, "
                                                    "every 1 or more.")
    assert frames_between(source, "0:9:1", 1)[1] == "The frames played are 0 to 4."
    monkeypatch.setattr(trajectory_frames, "BINARY_ATOM_FRAMES", 20)
    assert frames_between(source, "0:4:1", 1)[1] == (
        "9 frames of 4 atoms are too many to send; choose fewer frames, or fewer in between.")


def test_the_server_sends_them_as_dcd_and_as_xtc(tmp_path):
    import urllib.error
    import urllib.request

    from fastmdxplora.gui.server import start_dashboard_session

    study = tmp_path / "study"
    _frames_folder(study)
    (study / "simulation" / "live_status.json").write_text(json.dumps(
        {"status": "completed", "stage": "production"}), encoding="utf-8")
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with urllib.request.urlopen(session.url + "/structure/frames.dcd?span=0:2:1&between=1",
                                    timeout=60) as response:
            assert response.status == 200
        made = study / "simulation" / "frames_between1_0_2_1.dcd"
        assert md.load_dcd(str(made), top=str(made.with_name("frames_topology.pdb"))).n_frames == 5
        with urllib.request.urlopen(
                session.url + "/structure/frames.dcd?span=0:2:1&between=1&as=xtc",
                timeout=60) as response:
            assert response.read()[:4] == b"\x00\x00\x07\xcb"
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(session.url + "/structure/frames.dcd?span=0:2:1&between=5",
                                   timeout=60)
        assert refused.value.code == 404
        assert "1, 3 or 7" in refused.value.reason
    finally:
        session.server.shutdown()


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    pytest.importorskip("playwright.sync_api")
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        pytest.skip("ffmpeg is not on this computer")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_the_cartoon_is_dssp_of_each_frame import _helical_study

    root = tmp_path_factory.mktemp("between") / "work"
    root.mkdir()
    study = _helical_study(root / "haemoglobin", analyse=False)
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        context = browser.new_context(viewport={"width": 1400, "height": 900},
                                      accept_downloads=True)
        opened = context.new_page()
        opened.set_default_timeout(300000)
        errors: list[str] = []
        opened.on("pageerror", lambda error: errors.append(str(error)))
        opened.errors = errors
        opened.study = study
        opened.goto(session.url + "#viewer", wait_until="domcontentloaded")
        if not opened.evaluate("() => !!document.createElement('canvas').getContext('webgl')"):
            pytest.skip("this browser has no WebGL, so the viewer cannot render")
        opened.wait_for_function(f"() => {VIEWER} && {VIEWER}.STATE.model")
        opened.wait_for_function("() => document.getElementById('movie-by').textContent")
        yield opened
        browser.close()
    session.server.shutdown()


def _movie_frames(path: Path) -> list:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    return list(np.frombuffer(raw, np.uint8).reshape(-1, HEIGHT, WIDTH, 3).astype(float))


def test_the_frames_in_between_lie_between_the_frames_played(page):
    from PIL import Image

    page.evaluate(f"async () => {{ await {VIEWER}.movie.frames(); "
                  f"await {VIEWER}.movie.showFrame(1); }}")
    assert page.evaluate(f"() => {VIEWER}.STATE.superposed") == "none"
    count = page.evaluate(f"() => {VIEWER}.STATE.engine.frameCount()")
    hooks.tool(page, "side-movie")
    page.fill("#movie-from", "2")
    page.fill("#movie-to", "3")
    page.select_option("#movie-between", "3")
    page.dispatch_event("#movie-between", "change")
    page.select_option("#movie-size", "shown")
    assert page.text_content("#movie-length").startswith(
        "5 frames (2 played, 3 in between each two), 0.2 s")
    made = page.evaluate(f"""() => window.FastMDXViewerMovie.make({{
        name: "between", size: "{WIDTH}x{HEIGHT}", keep: true, time: false}})""")
    assert made["ok"] and made["frames"] == 5 and made["between"] == 3
    assert page.text_content("#movie-said").endswith(
        ", 3 frames in between each two played, interpolated.")
    # Superposed on the backbone first, and said so on the page; the frames
    # played, the frame shown and their count are put back.
    assert page.evaluate(f"() => {VIEWER}.STATE.superposed") == "backbone"
    assert page.evaluate(f"() => {VIEWER}.STATE.engine.frameCount()") == count
    assert page.evaluate(f"() => {VIEWER}.STATE.engine.frame()") == 1
    assert page.evaluate(f"() => {VIEWER}.STATE.engine.tween") is None
    probe = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format_tags=comment", "-of", "json",
         str(page.study / made["file"])], capture_output=True, text=True,
        check=True).stdout)["format"]["tags"]["comment"]
    assert "3 frames in between each two, interpolated in straight lines (not simulated)" \
        in probe

    stills = []
    for frame in (2, 3):
        uri = page.evaluate(f"""async (frame) => {{
            await {VIEWER}.movie.showFrame(frame);
            const still = await {VIEWER}.STATE.engine.still({WIDTH}, {HEIGHT});
            return still.toDataURL('image/png');
        }}""", frame)
        png = __import__("base64").b64decode(uri.split(",", 1)[1])
        stills.append(np.asarray(Image.open(io.BytesIO(png)).convert("RGB")).astype(float))
    movie = _movie_frames(page.study / made["file"])
    assert len(movie) == 5
    apart = lambda a, b: float(np.abs(a - b).mean())  # noqa: E731
    # The first and last are the frames played; each in between is a frame
    # of its own (where its atoms are is held in the next test).
    assert apart(movie[0], stills[0]) < apart(movie[0], stills[1])
    assert apart(movie[4], stills[1]) < apart(movie[4], stills[0])
    for k in range(4):
        assert apart(movie[k], movie[k + 1]) > 0.5
    assert page.errors == []


def test_frames_in_between_keep_the_names_of_the_frames_played(page):
    said = page.evaluate(f"""async () => {{
        const viewer = {VIEWER};
        const engine = viewer.STATE.engine;
        const url = viewer.STATE.superposedUrl || viewer.STATE.framesCoordinatesUrl;
        const total = engine.frameCount();
        const ok = await engine.tweenFrames(url + "&span=4:0:2&between=1", [4, 2, 0], 1);
        const seen = [];
        const at = [];
        for (const [frame, step] of [[4, 0], [4, 1], [2, 0], [2, 1], [0, 0], [0, 1], [3, 0]]) {{
            await engine.setFrame(frame, step);
            const cell = engine.trajectoryModelCell();
            seen.push([engine.frame(),
                engine.lib.structure.Model.TrajectoryInfo.get(cell.obj.data).index]);
            at.push(engine.atoms([0, 500, 1500]).map((a) => [a.x, a.y, a.z]));
        }}
        const during = engine.frameCount();
        await engine.untweenFrames(url);
        return {{ok, seen, at, during, after: engine.frameCount(), total,
                 shown: engine.frame()}};
    }}""")
    assert said["ok"] and said["during"] == 5 and said["after"] == said["total"]
    # Each atom in between is half way from its place in one frame played to
    # its place in the next (to the 0.01 A of the frames as XTC).
    at = np.array(said["at"])
    assert at[1] == pytest.approx((at[0] + at[2]) / 2, abs=0.011)
    assert at[3] == pytest.approx((at[2] + at[4]) / 2, abs=0.011)
    assert np.abs(at[2] - at[0]).max() > 0.5
    # Frame 0, the last, has none after it; 3 is not in the movie: the
    # nearest frame played that is (4, the first of two as near).
    assert said["seen"] == [[4, 0], [4, 1], [2, 2], [2, 3], [0, 4], [0, 4], [4, 0]]
    assert said["shown"] == 4
    refused = page.evaluate(f"""async () => {{
        const engine = {VIEWER}.STATE.engine;
        const url = {VIEWER}.STATE.superposedUrl;
        const total = engine.frameCount();
        return [await engine.tweenFrames(url + "&span=0:2:1&between=1", [0, 1, 2, 3], 1, url),
                engine.tween, engine.frameCount(), total];
    }}""")
    # Read again as they were where the frames in between are not as asked.
    assert refused[0] is False and refused[1] is None and refused[2] == refused[3]
    assert page.errors == []
