"""A movie is made in the Viewer, of the frames as the Viewer shows them.

The Movie section of the Viewer renders each frame asked for at the movie's
size, with the time in its corner, and sends it to ffmpeg on this computer
(movies.py); the movie is kept in the study's movies/ and downloaded, and
the frame and the camera shown before are put back. Each frame of the movie
is the frame of the trajectory it says it is: closer to that frame rendered
on its own than to any other.

Haemoglobin (1HHO), its frames as the GUI plays them.
"""

from __future__ import annotations

import io
import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("mdtraj")
pytest.importorskip("playwright.sync_api")
pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                reason="ffmpeg is not on this computer")

VIEWER = "window.FastMDXMoleculeViewer"
WIDTH, HEIGHT = 1280, 720


@pytest.fixture(scope="module")
def study(tmp_path_factory) -> Path:
    from fastmdxplora.gui.trajectory_frames import frames_info
    from tests.test_the_cartoon_is_dssp_of_each_frame import _helical_study

    root = tmp_path_factory.mktemp("movie") / "work"
    root.mkdir()
    made = _helical_study(root / "haemoglobin", analyse=False)
    assert frames_info(made)["available"]
    return made


@pytest.fixture(scope="module")
def page(study):
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

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
        opened.goto(session.url + "#viewer", wait_until="domcontentloaded")
        if not opened.evaluate("() => !!document.createElement('canvas').getContext('webgl')"):
            pytest.skip("this browser has no WebGL, so the viewer cannot render")
        opened.wait_for_function(f"() => {VIEWER} && {VIEWER}.STATE.model")
        opened.wait_for_function("() => document.getElementById('movie-by').textContent")
        yield opened
        browser.close()
    session.server.shutdown()


def _frames(path: Path) -> list:
    import numpy as np

    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    return list(np.frombuffer(raw, np.uint8).reshape(-1, HEIGHT, WIDTH, 3).astype(float))


def test_the_movie_is_of_the_frames_shown_and_the_view_is_put_back(page, study):
    import numpy as np
    from PIL import Image

    assert page.text_content("#movie-by").startswith(("MP4 (H.264), by ffmpeg",
                                                      "WEBM (VP9), by ffmpeg",
                                                      "WEBM (VP8), by ffmpeg"))
    page.evaluate(f"async () => {{ await {VIEWER}.movie.frames(); "
                  f"await {VIEWER}.movie.showFrame(5); }}")
    assert page.evaluate(f"() => {VIEWER}.movie.shownFrame()") == 5
    # A camera of its own, set at once rather than animated.
    before = page.evaluate(f"""() => {{
        const engine = {VIEWER}.STATE.engine;
        const now = engine.cameraSnapshot();
        const position = [0, 1, 2].map(
            (i) => now.target[i] + (now.position[i] - now.target[i]) / 1.3);
        engine.restoreCamera({{...now, position}});
        return engine.cameraSnapshot();
    }}""")
    source_before = page.evaluate(f"""() => {{
        const engine = {VIEWER}.STATE.engine;
        return engine.atoms(Array.from({{length: engine.atomCount()}}, (_, i) => i))
            .map((atom) => [atom.x, atom.y, atom.z]);
    }}""")
    page.select_option("#movie-size", f"{WIDTH}x{HEIGHT}")
    page.fill("#movie-from", "0")
    page.fill("#movie-to", "3")
    page.fill("#movie-name", "first four")
    page.dispatch_event("#movie-to", "change")
    output_fps = 2 if page.text_content("#movie-by").startswith("MP4") else 24
    assert page.text_content("#movie-length") == (
        f"4 frames, {4 / output_fps:.1f} s, {WIDTH} × {HEIGHT}")
    with page.expect_download(timeout=600000) as caught:
        page.click("#movie-make")
    page.wait_for_function("() => !document.getElementById('movie-settings').disabled")
    made = study / "movies" / caught.value.suggested_filename
    assert made.is_file() and made.stem == "first four"
    assert Path(caught.value.path()).read_bytes() == made.read_bytes()
    said = page.text_content("#movie-said")
    assert said.startswith(f"Made movies/{made.name}: 4 frames, 2 s, {WIDTH} × {HEIGHT}")
    camera_path = page.evaluate(f"""async () => {{
        const engine = {VIEWER}.STATE.engine;
        const centers = [];
        for (const frame of [0, 1, 2, 3]) {{
            await {VIEWER}.movie.showFrame(frame);
            centers.push(engine.proteinCentroid());
        }}
        await {VIEWER}.movie.showFrame(5);
        return {{path: window.FastMDXViewerMovie.lastCameraPath, centers}};
    }}""")
    assert len(camera_path["path"]) == 4
    reference = camera_path["centers"][0]
    for rendered, center in zip(camera_path["path"], camera_path["centers"]):
        translation = [reference[axis] - center[axis] for axis in range(3)]
        assert rendered["position"] == pytest.approx(
            [before["position"][axis] + translation[axis] for axis in range(3)], abs=1e-5)
        assert rendered["target"] == pytest.approx(
            [before["target"][axis] + translation[axis] for axis in range(3)], abs=1e-5)
        assert rendered["up"] == pytest.approx(before["up"], abs=1e-6)
    source_after = page.evaluate(f"""() => {{
        const engine = {VIEWER}.STATE.engine;
        return engine.atoms(Array.from({{length: engine.atomCount()}}, (_, i) => i))
            .map((atom) => [atom.x, atom.y, atom.z]);
    }}""")
    np.testing.assert_allclose(source_after, source_before, atol=1e-7, rtol=0)
    probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json",
                                       str(made)], capture_output=True, text=True,
                                      check=True).stdout)["streams"][0]
    assert (probe["width"], probe["height"], probe["r_frame_rate"]) == (WIDTH, HEIGHT, "2/1")

    # The frame and the camera shown before are put back.
    assert page.evaluate(f"() => {VIEWER}.movie.shownFrame()") == 5
    after = page.evaluate(f"() => {VIEWER}.STATE.engine.cameraSnapshot()")
    for key in ("position", "target", "up"):
        assert after[key] == pytest.approx(before[key], abs=1e-6), key
    assert not page.evaluate(f"() => {VIEWER}.STATE.makingMovie")

    # Each frame of the movie is the frame it says it is: rendered again on
    # its own, frame by frame, the movie's frame is nearest its own.
    stills = []
    for frame in range(4):
        uri = page.evaluate(f"""async (frame) => {{
            await {VIEWER}.movie.showFrame(frame);
            const still = await {VIEWER}.STATE.engine.still({WIDTH}, {HEIGHT});
            return still.toDataURL('image/png');
        }}""", frame)
        png = __import__("base64").b64decode(uri.split(",", 1)[1])
        stills.append(np.asarray(Image.open(io.BytesIO(png)).convert("RGB")).astype(float))
    movie = _frames(made)
    assert len(movie) == 4
    # Leaving out the corner the time is written in.
    keep = np.ones((HEIGHT, WIDTH), bool)
    keep[HEIGHT - 120:, WIDTH - 360:] = False
    for index, frame in enumerate(movie):
        apart = [np.abs(frame - still)[keep].mean() for still in stills]
        assert int(np.argmin(apart)) == index, apart
    # The time is written in the corner (no times here, so the frame).
    corner = movie[0][HEIGHT - 60:HEIGHT - 20, WIDTH - 200:WIDTH - 20]
    assert corner.max() > 200
    assert page.errors == []


def test_a_movie_cancelled_leaves_nothing(page, study):
    page.select_option("#movie-size", f"{WIDTH}x{HEIGHT}")
    page.fill("#movie-from", "0")
    page.fill("#movie-to", "")
    page.fill("#movie-name", "never")
    page.click("#movie-make")
    page.wait_for_function("() => Number(document.getElementById('movie-progress').value) >= 1")
    assert page.is_visible("#movie-cancel")
    page.click("#movie-cancel")
    page.wait_for_function("() => !document.getElementById('movie-settings').disabled")
    assert page.text_content("#movie-said") == "The movie was cancelled; nothing was kept."
    assert not any(p.stem == "never" for p in (study / "movies").iterdir())
    assert not page.is_visible("#movie-cancel")
    assert page.errors == []


def test_one_turn_is_about_the_screen_s_vertical(page):
    turned = page.evaluate(f"""() => {{
        const engine = {VIEWER}.STATE.engine;
        const start = engine.cameraSnapshot();
        engine.turnCamera(start, Math.PI / 2);
        const quarter = engine.cameraSnapshot();
        engine.turnCamera(start, 2 * Math.PI);
        const whole = engine.cameraSnapshot();
        engine.restoreCamera(start);
        return {{start, quarter, whole}};
    }}""")
    start, quarter, whole = turned["start"], turned["quarter"], turned["whole"]
    target = start["target"]
    a = [p - t for p, t in zip(start["position"], target)]
    b = [p - t for p, t in zip(quarter["position"], target)]
    up = start["up"]
    length = math.sqrt(sum(x * x for x in up))
    up = [x / length for x in up]
    norm = lambda v: math.sqrt(sum(x * x for x in v))  # noqa: E731
    dot = lambda u, v: sum(x * y for x, y in zip(u, v))  # noqa: E731
    assert norm(b) == pytest.approx(norm(a), rel=1e-9)
    assert dot(b, up) == pytest.approx(dot(a, up), abs=1e-6)
    flat_a = [x - dot(a, up) * u for x, u in zip(a, up)]
    flat_b = [x - dot(b, up) * u for x, u in zip(b, up)]
    assert dot(flat_a, flat_b) == pytest.approx(0, abs=1e-6 * norm(a) ** 2)
    assert whole["position"] == pytest.approx(start["position"], abs=1e-6)
    assert quarter["target"] == start["target"] and quarter["up"] == start["up"]


def test_the_frames_and_the_time_are_as_asked(page):
    said = page.evaluate("""() => {
        const movie = window.FastMDXViewerMovie;
        const set = (from, to, every) => {
            document.getElementById('movie-from').value = from;
            document.getElementById('movie-to').value = to;
            document.getElementById('movie-every').value = every;
            return movie.framesOf(10);
        };
        return {
            all: set('', '', ''), some: set(2, 8, 3), back: set(9, 0, 4), beyond: set(-5, 99, 5),
            times: [movie.timeSaid(12.345, 0.1), movie.timeSaid(5, 0.002), movie.timeSaid(100, 10),
                    movie.timeSaid(1.5, 0)],
        };
    }""")
    assert said["all"] == list(range(10))
    assert said["some"] == [2, 5, 8]
    assert said["back"] == [9, 5, 1]
    assert said["beyond"] == [0, 5]
    assert said["times"] == ["12.3 ns", "5.000 ns", "100 ns", "1.50 ns"]
    size = page.evaluate("""() => {
        const sizes = {};
        for (const value of ['1280x720', '1920x1080', '3840x2160', 'shown']) {
            document.getElementById('movie-size').value = value;
            sizes[value] = window.FastMDXViewerMovie.size();
        }
        const canvas = document.querySelector('#viewer-canvas canvas');
        sizes.aspect = canvas.clientWidth / canvas.clientHeight;
        return sizes;
    }""")
    assert size["1920x1080"] == [1920, 1080] and size["3840x2160"] == [3840, 2160]
    width, height = size["shown"]
    assert width % 2 == 0 and height % 2 == 0 and height <= 2160
    assert width / height == pytest.approx(size["aspect"], rel=0.01)


def test_a_structure_without_frames_is_turned_once(tmp_path, study, page):
    """A study set up and not yet run has no frames; its movie is the
    structure shown turned once about the screen's vertical."""
    from fastmdxplora.gui.server import start_dashboard_session

    still = tmp_path / "still"
    (still / "setup").mkdir(parents=True)
    (still / "setup" / "topology.pdb").write_text(
        (study / "simulation" / "frames_topology.pdb").read_text(encoding="utf-8"),
        encoding="utf-8")
    (still / "resolved_config.yml").write_text("systems:\n  - system: 1HHO\n",
                                               encoding="utf-8")
    session = start_dashboard_session(output=str(still), host="127.0.0.1", port=0)
    # In a context of its own in the module's browser, which is already open.
    context = page.context.browser.new_context(viewport={"width": 1400, "height": 900},
                                               accept_downloads=True)
    try:
        other = context.new_page()
        other.set_default_timeout(300000)
        errors: list[str] = []
        other.on("pageerror", lambda error: errors.append(str(error)))
        other.goto(session.url + "#viewer", wait_until="domcontentloaded")
        other.wait_for_function(f"() => {VIEWER} && {VIEWER}.STATE.model")
        other.wait_for_function("() => document.getElementById('movie-by').textContent")
        other.evaluate("() => { window.FastMDXViewerMovie.settings.turnSeconds = 0.4; }")
        other.select_option("#movie-fps", "10")
        other.select_option("#movie-size", f"{WIDTH}x{HEIGHT}")
        other.wait_for_function("() => document.getElementById('movie-length').textContent"
                                ".startsWith('No frames')")
        said = other.text_content("#movie-length")
        disabled = other.is_disabled("#movie-from")
        before = other.evaluate(f"() => {VIEWER}.STATE.engine.cameraSnapshot()")
        other.fill("#movie-name", "turning")
        with other.expect_download(timeout=600000) as caught:
            other.click("#movie-make")
        other.wait_for_function("() => !document.getElementById('movie-settings').disabled")
        after = other.evaluate(f"() => {VIEWER}.STATE.engine.cameraSnapshot()")
        name = caught.value.suggested_filename
    finally:
        context.close()
        session.server.shutdown()
    assert said == f"No frames: the structure turned once, 4 frames, 0.4 s, {WIDTH} × {HEIGHT}"
    assert disabled
    made = still / "movies" / name
    frames = _frames(made)
    assert len(frames) == 4
    import numpy as np

    # Each frame a quarter turn on from the last: none alike.
    apart = [np.abs(frames[i] - frames[i + 1]).mean() for i in range(3)]
    assert min(apart) > 1.0
    for key in ("position", "target", "up"):
        assert after[key] == pytest.approx(before[key], abs=1e-6), key
    assert errors == []
