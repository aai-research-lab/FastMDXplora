import base64
import hashlib
import io
import json
import shutil
import subprocess
from types import SimpleNamespace

import pytest
from PIL import Image

from fastmdxplora.gui.clips import clip_endpoint
from fastmdxplora.gui.trajectory_playback import playback_info
from tests.test_the_drawing_scripts_run_in_a_browser import _write_study
from tests.test_the_drawing_scripts_run_in_a_browser import dashboard as _dashboard
from tests.test_the_drawing_scripts_run_in_a_browser import page as _page

clip_page = _page
dashboard = _dashboard


@pytest.fixture
def runtime(tmp_path):
    root = _write_study(tmp_path / "study")
    index = playback_info(root)
    assert index["playback_available"]
    return SimpleNamespace(active_root=root, data_stale=False, index=index)


def start(runtime, **kwargs):
    payload = {"action": "start", "study": str(runtime.active_root), "format": "gif", "fps": 10,
               "frames": [0, 1], "signature": runtime.index["source_signature"], "view": {"page": "viewer"},
               "labels": {"frame": True}, "rotation": 30, "label_scope": "protein"}
    payload.update(kwargs)
    return clip_endpoint(runtime, payload)


def png(color):
    stream = io.BytesIO()
    Image.new("RGB", (40, 30), color).save(stream, format="PNG")
    return base64.b64encode(stream.getvalue()).decode()


def upload(runtime, identity, index, color):
    return clip_endpoint(runtime, {"action": "frame", "study": str(runtime.active_root),
                                  "id": identity, "index": index, "png": png(color)})


@pytest.mark.parametrize("format", ["gif", "mp4", "both"])
def test_encoded_clip_and_source_metadata_preserve_scientific_artifacts(runtime, format):
    if format in {"mp4", "both"} and not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    sources = [runtime.active_root / "simulation/production.dcd", runtime.active_root / "setup/topology.pdb"]
    before = [hashlib.sha256(path.read_bytes()).hexdigest() for path in sources]
    opened = start(runtime, format=format)
    assert opened["ok"], opened
    assert upload(runtime, opened["id"], 0, "red")["ok"]
    assert upload(runtime, opened["id"], 1, "blue")["ok"]
    result = clip_endpoint(runtime, {"action": "finish", "study": str(runtime.active_root), "id": opened["id"]})
    assert result["ok"], result
    path = runtime.active_root / result["url"].removeprefix("/artifacts/")
    if format in {"gif", "both"}:
        with Image.open(path) as image:
            assert image.n_frames == 2 and image.info["duration"] == 100
    else:
        assert b"ftyp" in path.read_bytes()[:32]
    if format in {"mp4", "both"}:
        encoded = path.parent / "trajectory.mp4"
        decoded = subprocess.run([shutil.which("ffmpeg"), "-nostdin", "-v", "error", "-i", str(encoded),
                                  "-f", "null", "-"], capture_output=True, timeout=15)
        assert decoded.returncode == 0
    metadata = json.loads((path.parent / "view.json").read_text())
    assert metadata["source_frames"] == runtime.index["frame_indices"][:2]
    assert metadata["times_ns"] == runtime.index["frame_times_ns"][:2]
    assert metadata["rotation"] == 30 and metadata["label_scope"] == "protein"
    assert metadata["source"]["evidence"]["trajectory"]
    assert [hashlib.sha256(path.read_bytes()).hexdigest() for path in sources] == before
    assert runtime._clip_upload is None


def test_changed_trajectory_refuses_finish_and_cleans_upload(runtime):
    opened = start(runtime)
    upload(runtime, opened["id"], 0, "red")
    upload(runtime, opened["id"], 1, "blue")
    with (runtime.active_root / "simulation/production.dcd").open("ab") as stream:
        stream.write(b"fixture-change")
    result = clip_endpoint(runtime, {"action": "finish", "study": str(runtime.active_root), "id": opened["id"]})
    assert not result["ok"] and "changed" in result["error"]
    assert runtime._clip_upload is None
    assert not (runtime.active_root / "exports").exists()


def test_wrong_upload_identity_cannot_cancel_another_export(runtime):
    opened = start(runtime)
    assert not upload(runtime, "wrong-session", 0, "red")["ok"]
    assert runtime._clip_upload["id"] == opened["id"]
    assert not start(runtime)["ok"]
    assert runtime._clip_upload["id"] == opened["id"]
    result = clip_endpoint(runtime, {"action": "cancel", "study": str(runtime.active_root), "id": opened["id"]})
    assert result["ok"] and runtime._clip_upload is None


@pytest.mark.parametrize("options", [{"frames": [1, 0]}, {"frames": [0, 500]}, {"fps": True},
                                     {"labels": {"execute": True}}, {"rotation": float("nan")},
                                     {"dimensions": [1920, 1080]}, {"dimensions": [True, 480]},
                                     {"captions": {"caption": "x" * 241}}, {"captions": {"caption": "two\nlines"}},
                                     {"camera_tracking": "translate-atoms"}, {"camera_reference_frame": True},
                                     {"camera_reference_frame": 500}])
def test_invalid_export_options_are_refused(runtime, options):
    assert not start(runtime, **options)["ok"]


@pytest.mark.parametrize("camera", [None, [0] * 7, [False] * 8, [float("nan")] * 8])
def test_following_camera_requires_a_valid_rendered_view(runtime, camera):
    opened = start(runtime, camera_tracking="protein-centroid")
    assert opened["ok"]
    result = clip_endpoint(runtime, {"action": "frame", "study": str(runtime.active_root),
                                    "id": opened["id"], "index": 0, "png": png("red"), "camera": camera})
    assert not result["ok"] and "camera" in result["error"]
    assert runtime._clip_upload is None
    assert not (runtime.active_root / "exports").exists()


@pytest.mark.parametrize("format", ["gif", "both"])
def test_browser_exports_real_rotating_labeled_clip_and_restores_view(clip_page, format, tmp_path, dashboard):
    if format == "both" and not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    page = clip_page
    root = dashboard.runtime.active_root
    sources = [path for folder in ("setup", "simulation", "analysis")
               for path in (root / folder).rglob("*") if path.is_file()]
    original = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
    page.locator("#clip-export-open").click()
    page.wait_for_function("document.querySelector('#clip-status').textContent.includes('saved browser frames available')")
    page.locator("#clip-last").fill("2")
    page.locator("#clip-format").select_option(format)
    page.locator("#clip-rotation").fill("60")
    page.locator("#clip-residues").check()
    page.locator("#clip-label-scope").select_option("protein")
    page.locator("#clip-resolution").select_option("640x480")
    page.locator("#clip-study-label").check()
    page.locator("#clip-study-title").fill("Test study")
    page.locator("#clip-caption-label").check()
    page.locator("#clip-caption").fill("Saved molecular frames")
    page.locator("#clip-preview").click()
    page.wait_for_function("document.querySelector('#clip-status').textContent.includes('Preview ready')", timeout=60000)
    assert page.locator("#clip-preview-first").is_visible()
    assert page.locator("#clip-preview-last").is_visible()
    assert "640 × 480" in page.locator("#clip-estimate").inner_text()
    assert page.evaluate("FastMDXMoleculeViewer.STATE.mode") == "structure"
    page.locator("#clip-export-start").click()
    page.wait_for_function("document.querySelector('#clip-status').textContent.includes('metadata saved')", timeout=60000)
    link = page.locator("#clip-download").get_attribute("href")
    response = page.request.get(page.url.split("/#")[0] + link)
    assert response.ok
    saved_gif = root / link.split("?", 1)[0].removeprefix("/artifacts/")
    assert response.body() == saved_gif.read_bytes()
    assert_visible_gif(response.body(), 3)
    metadata_link = page.locator("#clip-metadata").get_attribute("href")
    metadata = page.request.get(page.url.split("/#")[0] + metadata_link).json()
    assert metadata["dimensions"] == metadata["render_size"] == [640, 480]
    assert metadata["captions"] == {"study": "Test study", "caption": "Saved molecular frames"}
    assert metadata["camera_path"]["tracking"] == "protein-centroid"
    assert len(metadata["camera_path"]["rendered_views"]) == 3
    if format == "both":
        mp4_link = page.locator("#clip-download-mp4").get_attribute("href")
        assert page.locator("#clip-download-mp4").is_visible()
        mp4_response = page.request.get(page.url.split("/#")[0] + mp4_link)
        assert mp4_response.ok
        saved_mp4 = root / mp4_link.split("?", 1)[0].removeprefix("/artifacts/")
        assert mp4_response.body() == saved_mp4.read_bytes()
        encoded = tmp_path / "browser-trajectory.mp4"
        encoded.write_bytes(mp4_response.body())
        decoded = subprocess.run(
            [shutil.which("ffmpeg"), "-nostdin", "-v", "error", "-i", str(encoded),
             "-f", "image2pipe", "-vcodec", "png", "-frames:v", "1", "-"],
            capture_output=True, timeout=30,
        )
        assert decoded.returncode == 0, decoded.stderr.decode(errors="replace")
        with Image.open(io.BytesIO(decoded.stdout)) as first_frame:
            assert first_frame.size == (640, 480)
        assert len(metadata["source_frames"]) == len(metadata["times_ns"]) == 3
    assert page.evaluate("FastMDXMoleculeViewer.STATE.mode") == "structure"
    assert not page.evaluate("document.querySelector('.viewer-layout').inert")
    assert not page.errors
    assert {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in sources} == original


def assert_visible_gif(content, count):
    import numpy as np

    with Image.open(io.BytesIO(content)) as image:
        assert image.n_frames == count
        assert image.size == (640, 480)
        for frame in range(count):
            image.seek(frame)
            scene = image.convert("RGB").crop((0, 0, 640, 300))
            pixels = np.asarray(scene).astype(int)
            colored = (pixels.max(axis=2) - pixels.min(axis=2) > 30).sum()
            assert colored > 100, f"GIF frame {frame} must contain molecular geometry above captions"


@pytest.mark.parametrize("format", ["gif", "both"])
def test_clip_keeps_displaced_intermediate_frames_visible_without_changing_atoms(tmp_path, format):
    """First/last previews must not hide an empty middle of a recorded clip."""
    if format == "both" and not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    md = pytest.importorskip("mdtraj")
    import numpy as np
    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_the_drawing_scripts_run_in_a_browser import _open

    root = _write_study(tmp_path / "origin-jumps")
    trajectory_path = root / "simulation/production.dcd"
    trajectory = md.load(str(trajectory_path), top=str(root / "simulation/trajectory_topology.pdb"))
    trajectory.xyz[3:16] += [20.0, -25.0, 30.0]  # Fixture-only recorded origin jumps, in nm.
    trajectory.save_dcd(str(trajectory_path))
    sources = [path for folder in ("setup", "simulation")
               for path in (root / folder).rglob("*") if path.is_file()]
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    try:
        for page in _open(session, "#viewer"):
            page.wait_for_function("window.FastMDXMoleculeViewer?.STATE.model", timeout=60000)
            page.locator("#viewer-rep").select_option("ballAndStick")
            page.get_by_role("button", name="Next frame", exact=True).click()
            page.wait_for_function("document.querySelector('#traj-slider').value === '1'")
            page.evaluate("""() => FastMDXMoleculeViewer.select(
                FastMDXMoleculeViewer.STATE.model.selectedAtoms({atom: 'CA', resi: 2})[0])""")
            original = page.evaluate("FastMDXResearch.capture()")
            page.locator("#clip-export-open").click()
            page.wait_for_function("document.querySelector('#clip-status').textContent.includes('saved browser frames available')")
            page.locator("#clip-last").fill("18")
            page.locator("#clip-stride").fill("3")
            page.locator("#clip-resolution").select_option("640x480")
            page.locator("#clip-format").select_option(format)
            page.locator("#clip-rotation").fill("60")
            page.locator("#clip-residues").check()
            page.locator("#clip-atoms").check()
            page.locator("#clip-export-start").click()
            page.wait_for_function("document.querySelector('#clip-status').textContent.includes('metadata saved')", timeout=60000)
            link = page.locator("#clip-download").get_attribute("href")
            gif_response = page.request.get(session.url + link)
            assert gif_response.ok
            assert_visible_gif(gif_response.body(), 7)
            metadata = page.request.get(session.url + page.locator("#clip-metadata").get_attribute("href")).json()
            path = metadata["camera_path"]
            assert path["tracking"] == "protein-centroid" and path["reference_frame"] == 1
            assert metadata["frames"] == metadata["source_frames"] == list(range(0, 19, 3))
            assert path["base_view"] == pytest.approx(original["camera"])
            assert len(path["rendered_views"]) == 7
            # Camera translation follows the recorded origin; zoom remains exactly the user's.
            assert abs(path["rendered_views"][1][0] - path["rendered_views"][0][0]) > 190
            assert all(view[3] == pytest.approx(original["camera"][3]) for view in path["rendered_views"])
            if format == "both":
                mp4_response = page.request.get(session.url + page.locator("#clip-download-mp4").get_attribute("href"))
                assert mp4_response.ok
                video = tmp_path / "displaced-trajectory.mp4"
                video.write_bytes(mp4_response.body())
                decoded_folder = tmp_path / "decoded"
                decoded_folder.mkdir()
                decoded = subprocess.run([shutil.which("ffmpeg"), "-nostdin", "-v", "error", "-i", str(video),
                                          str(decoded_folder / "%03d.png")], capture_output=True, timeout=30)
                assert decoded.returncode == 0, decoded.stderr.decode(errors="replace")
                decoded_frames = sorted(decoded_folder.glob("*.png"))
                assert len(decoded_frames) == 7
                for frame, png_path in enumerate(decoded_frames):
                    with Image.open(png_path) as image:
                        assert image.size == (640, 480)
                        pixels = np.asarray(image.convert("RGB").crop((0, 0, 640, 300))).astype(int)
                        assert (pixels.max(axis=2) - pixels.min(axis=2) > 30).sum() > 100, f"MP4 frame {frame} is empty"
            restored = page.evaluate("FastMDXResearch.capture()")
            assert restored["camera"] == pytest.approx(original["camera"])
            assert restored["frame"] == original["frame"] == 1
            assert restored["selection"] == original["selection"]
            assert not page.evaluate("document.querySelector('.viewer-layout').inert")
            assert not page.errors
            # Fixed-camera mode remains available; it must not translate saved atoms either.
            fixed = page.evaluate("""async () => {
              const viewer = FastMDXMoleculeViewer.STATE.viewer;
              const clip = await FastMDXMoleculeViewer.clipSession({followMolecule: false});
              try {
                await clip.render(0, 0); const first = viewer.getView();
                await clip.render(9, 0); const last = viewer.getView();
                const atoms = FastMDXMoleculeViewer.STATE.model.selectedAtoms({resn: 'ALA'});
                return {first, last, tracking: clip.cameraTracking, xyz: atoms.map(a => [a.x, a.y, a.z])};
              } finally { await clip.restore(); }
            }""")
            assert fixed["tracking"] == "fixed" and fixed["first"] == pytest.approx(fixed["last"])
            expected_xyz = trajectory.xyz[9, trajectory.topology.select("protein")] * 10
            np.testing.assert_allclose(fixed["xyz"], expected_xyz, atol=0.0011, rtol=0)
        assert {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in sources} == before
    finally:
        session.server.shutdown()
        session.server.server_close()


@pytest.mark.parametrize("theme", ["graphite", "ink", "paper"])
def test_clip_dialog_text_fields_are_readable_and_fit(clip_page, theme):
    import re

    page = clip_page
    page.locator("#settings-open").click()
    page.locator(f'.seg-btn[data-theme="{theme}"]').click()
    page.locator("#settings-open").click()

    def luminance(rgb):
        channels = [int(value) / 255 for value in re.findall(r"\d+", rgb)[:3]]
        linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
                  for value in channels]
        return sum(weight * value for weight, value in zip((0.2126, 0.7152, 0.0722), linear))

    for width in (1280, 390):
        page.set_viewport_size({"width": width, "height": 900})
        page.locator("#clip-export-open").click()
        for name in ("clip-study-title", "clip-caption"):
            field = page.locator("#" + name)
            colors = field.evaluate("el => {const s=getComputedStyle(el); return [s.color,s.backgroundColor,getComputedStyle(el,'::placeholder').color];}")
            ground = luminance(colors[1])
            for foreground in (colors[0], colors[2]):
                ink = luminance(foreground)
                assert (max(ink, ground) + 0.05) / (min(ink, ground) + 0.05) >= 4.5
            field.fill("Research frame and selected residues")
            bounds = field.bounding_box()
            assert bounds["width"] > 100 and bounds["x"] >= 0
            assert bounds["x"] + bounds["width"] <= width
        page.locator("#clip-export-cancel").click()
        assert page.locator("#clip-export-dialog").is_hidden()
    assert not page.errors


def test_browser_cancel_restores_view_and_does_not_save_clip(clip_page):
    page = clip_page
    page.locator("#clip-export-open").click()
    page.wait_for_function("document.querySelector('#clip-status').textContent.includes('saved browser frames available')")
    page.locator("#clip-export-start").click()
    page.locator("#clip-export-cancel").click()
    page.wait_for_function("document.querySelector('#clip-status').textContent.includes('cancelled')", timeout=60000)
    page.wait_for_function("!document.querySelector('#clip-export-start').disabled")
    assert page.locator("#clip-download").is_hidden()
    assert page.evaluate("FastMDXMoleculeViewer.STATE.mode") == "structure"
    assert not page.evaluate("document.querySelector('.viewer-layout').inert")
    assert not page.errors


def test_browser_clip_preview_and_export_restore_playback_controls(clip_page):
    page = clip_page
    page.get_by_role("button", name="Next frame", exact=True).click()
    page.wait_for_function("FastMDXMoleculeViewer.STATE.mode === 'playback' && FastMDXMoleculeViewer.STATE.playbackLoaded")
    page.evaluate("window.dispatchEvent(new CustomEvent('dashboard:trajectory-action', {detail: {action: 'first'}}))")
    page.wait_for_function("document.querySelector('#traj-slider').value === '0'")
    page.locator("#traj-follow").check()
    if not page.evaluate("FastMDXMoleculeViewer.STATE.spinning"):
        page.locator('[data-cam="spin"]').click()
    original = page.evaluate("({view: FastMDXResearch.capture(), liveUpdates: FastMDXMoleculeViewer.STATE.liveUpdates})")

    page.locator("#clip-export-open").click()
    page.wait_for_function("document.querySelector('#clip-status').textContent.includes('saved browser frames available')")
    page.locator("#clip-preview").click()
    page.wait_for_function("document.querySelector('#clip-status').textContent.includes('Preview ready')", timeout=60000)

    def assert_restored():
        restored = page.evaluate("({view: FastMDXResearch.capture(), controls: FastMDXMoleculeViewer.STATE, follow: document.querySelector('#traj-follow').checked})")
        assert restored["view"].get("frame") == original["view"].get("frame") == 0
        assert restored["follow"] is True
        assert restored["controls"]["spinning"] is True
        assert restored["controls"]["playbackPlaying"] is False
        assert restored["controls"]["liveUpdates"] is original["liveUpdates"]

    assert_restored()
    page.locator("#clip-export-start").click()
    page.wait_for_function("document.querySelector('#clip-status').textContent.includes('metadata saved')", timeout=60000)
    assert_restored()
    assert not page.errors


def test_browser_clip_unavailable_playback_restores_viewer_controls(clip_page):
    page = clip_page
    if not page.evaluate("FastMDXMoleculeViewer.STATE.spinning"):
        page.locator('[data-cam="spin"]').click()
    page.locator("#traj-follow").check()
    original = page.evaluate("({view: FastMDXResearch.capture(), liveUpdates: FastMDXMoleculeViewer.STATE.liveUpdates})")
    page.evaluate("FastMDXMoleculeViewer.STATE.playbackPayload = null")
    page.route("**/api/playback-info*", lambda route: route.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"playback_available": False, "reason": "test unavailable"})))

    error = page.evaluate("""async () => {
      try { await FastMDXMoleculeViewer.clipSession({}); return null; }
      catch (failure) { return failure.message; }
    }""")
    assert "unavailable" in error.lower()
    restored = page.evaluate("""() => ({view: FastMDXResearch.capture(), state: {
      mode: FastMDXMoleculeViewer.STATE.mode,
      spinning: FastMDXMoleculeViewer.STATE.spinning,
      playbackPlaying: FastMDXMoleculeViewer.STATE.playbackPlaying,
      liveUpdates: FastMDXMoleculeViewer.STATE.liveUpdates,
      clipExporting: FastMDXMoleculeViewer.STATE.clipExporting,
      follow: document.querySelector('#traj-follow').checked
    }})""")
    assert restored["view"]["mode"] == original["view"]["mode"] == "structure"
    assert restored["state"]["spinning"] is True
    assert restored["state"]["playbackPlaying"] is False
    assert restored["state"]["liveUpdates"] is original["liveUpdates"]
    assert not restored["state"]["clipExporting"]
    assert restored["state"]["follow"] is True
    assert not page.errors


def test_clip_preview_maps_a_different_structure_origin_and_restores_camera(tmp_path):
    """A centred static structure must stay visible when playback uses another origin."""
    md = pytest.importorskip("mdtraj")
    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_the_drawing_scripts_run_in_a_browser import _open

    root = _write_study(tmp_path / "translated-study")
    topology_path = root / "setup/topology.pdb"
    structure = md.load(str(topology_path))
    structure.xyz += 10.0  # Fixture static coordinates are 100 angstroms away.
    structure.save_pdb(str(topology_path))
    sources = [path for folder in ("setup", "simulation")
               for path in (root / folder).rglob("*") if path.is_file()]
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    try:
        for page in _open(session, "#viewer"):
            page.wait_for_function("window.FastMDXMoleculeViewer?.STATE.model", timeout=60000)
            original = page.evaluate("FastMDXResearch.capture()")
            page.locator("#clip-export-open").click()
            page.wait_for_function(
                "document.querySelector('#clip-status').textContent"
                ".includes('saved browser frames available')")
            page.locator("#clip-last").fill("2")
            page.locator("#clip-resolution").select_option("640x480")
            page.locator("#clip-preview").click()
            page.wait_for_function(
                "document.querySelector('#clip-status').textContent.includes('Preview ready')",
                timeout=60000)
            for name in ("clip-preview-first", "clip-preview-last"):
                data = page.locator("#" + name).get_attribute("src").split(",", 1)[1]
                with Image.open(io.BytesIO(base64.b64decode(data))) as image:
                    scene = image.convert("RGB").crop((0, 0, 640, 300))
                    colors = list(scene.getdata())
                    assert sum(max(pixel) - min(pixel) > 30 for pixel in colors) > 20
            restored = page.evaluate("FastMDXResearch.capture()")
            assert restored["camera"] == pytest.approx(original["camera"])
            assert restored.get("frame") == original.get("frame")
            assert page.evaluate("FastMDXMoleculeViewer.STATE.mode") == "structure"
            assert not page.errors
            page.locator("#clip-export-cancel").click()
            page.get_by_role("button", name="Next frame", exact=True).click()
            page.wait_for_function("FastMDXMoleculeViewer.STATE.mode === 'playback'")
            transitioned = page.evaluate("FastMDXResearch.capture()")
            assert transitioned["camera"][3:] == pytest.approx(original["camera"][3:])
            scene_png = page.locator("#viewer-canvas").screenshot()
            with Image.open(io.BytesIO(scene_png)) as image:
                colors = list(image.convert("RGB").getdata())
                assert sum(max(pixel) - min(pixel) > 30 for pixel in colors) > 20
        assert {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in sources} == before
    finally:
        session.server.shutdown()
        session.server.server_close()


@pytest.mark.parametrize("theme", ["graphite", "ink", "paper"])
def test_clip_dialog_controls_fit_with_double_text(clip_page, theme):
    page = clip_page
    page.locator("#settings-open").click()
    page.locator(f'.seg-btn[data-theme="{theme}"]').click()
    page.locator("#settings-open").click()
    page.set_viewport_size({"width": 390, "height": 900})
    page.add_style_tag(content="html {font-size: 200% !important;}")
    page.locator("#clip-export-open").click()
    outside = page.locator("#clip-export-dialog").evaluate("""dialog =>
        Array.from(dialog.querySelectorAll('button,input,select,textarea,fieldset label'))
        .filter(el => el.checkVisibility({checkVisibilityCSS:true,checkOpacity:true}))
        .filter(el => {const r=el.getBoundingClientRect(); return r.x < -1 || r.right > innerWidth+1;})
        .map(el => el.id || el.textContent.slice(0,60))""")
    assert not outside, outside
    page.locator("#clip-export-cancel").click()
    assert page.locator("#clip-export-dialog").is_hidden()
