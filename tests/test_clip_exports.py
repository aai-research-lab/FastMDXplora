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
from tests.test_the_drawing_scripts_run_in_a_browser import _write_study, dashboard, page as _page  # noqa: F401

clip_page = _page


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


def test_browser_exports_real_rotating_labeled_clip_and_restores_view(clip_page):
    page = clip_page
    page.locator("#clip-export-open").click()
    page.wait_for_function("document.querySelector('#clip-status').textContent.includes('saved browser frames available')")
    page.locator("#clip-last").fill("2")
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
    with Image.open(io.BytesIO(response.body())) as image:
        assert image.n_frames == 3
        assert image.size == (640, 480)
    metadata_link = page.locator("#clip-metadata").get_attribute("href")
    metadata = page.request.get(page.url.split("/#")[0] + metadata_link).json()
    assert metadata["dimensions"] == metadata["render_size"] == [640, 480]
    assert metadata["captions"] == {"study": "Test study", "caption": "Saved molecular frames"}
    assert page.evaluate("FastMDXMoleculeViewer.STATE.mode") == "structure"
    assert not page.evaluate("document.querySelector('.viewer-layout').inert")
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
