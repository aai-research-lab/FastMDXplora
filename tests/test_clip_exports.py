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
                                     {"captions": {"caption": "x" * 241}}, {"captions": {"caption": "two\nlines"}}])
def test_invalid_export_options_are_refused(runtime, options):
    assert not start(runtime, **options)["ok"]


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
    with Image.open(io.BytesIO(response.body())) as image:
        assert image.n_frames == 3
        assert image.size == (640, 480)
        scene = image.convert("RGB").crop((0, 0, 640, 300))
        colored = sum(1 for red, green, blue in scene.getdata()
                      if max(red, green, blue) - min(red, green, blue) > 30)
        assert colored > 20, "Export must contain the colored molecular scene above captions"
    metadata_link = page.locator("#clip-metadata").get_attribute("href")
    metadata = page.request.get(page.url.split("/#")[0] + metadata_link).json()
    assert metadata["dimensions"] == metadata["render_size"] == [640, 480]
    assert metadata["captions"] == {"study": "Test study", "caption": "Saved molecular frames"}
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
