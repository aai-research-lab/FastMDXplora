"""A view of the Viewer is saved with the study and shown again.

A figure of a trajectory is a camera, a frame and choices of how the
molecule is shown, made by turning and clicking, and lost with the page.
A view is saved under a name in the study (`viewer_views.json`), every value
checked, and choosing it again sets each choice and the camera as they were.
The publication look (a white ground, outlines and shading) is one of them.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from fastmdxplora.gui.saved_views import MOST_VIEWS, delete_view, save_view, views_of
from tests import viewer_hooks as hooks

CAMERA = {"position": [1.0, 2.0, 30.0], "target": [1.0, 2.0, 3.0], "up": [0.0, 1.0, 0.0],
          "radius": 12.5, "fov": 0.785, "mode": "perspective"}


def test_a_view_is_kept_checked_and_replaced_by_name(tmp_path):
    view = {"camera": CAMERA, "frame": 3, "representation": "sticks", "colour": "chain",
            "shown": {"protein": True, "water": False, "everything": True},
            "superposed": "backbone", "pocket_cutoff": 7.5, "publication": True,
            "script": "<script>alert(1)</script>"}
    assert save_view(tmp_path, "Pocket, frame 3", view)["ok"]
    kept = views_of(tmp_path)["views"]
    assert kept == [{"name": "Pocket, frame 3", "camera": CAMERA, "frame": 3,
                     "representation": "sticks", "colour": "chain",
                     "shown": {"protein": True, "water": False}, "superposed": "backbone",
                     "pocket_cutoff": 7.5, "publication": True}]
    save_view(tmp_path, "Pocket, frame 3", {"camera": CAMERA, "frame": 5})
    assert [v.get("frame") for v in views_of(tmp_path)["views"]] == [5]
    assert delete_view(tmp_path, "Pocket, frame 3")["views"] == []
    assert not delete_view(tmp_path, "Pocket, frame 3")["ok"]


@pytest.mark.parametrize("name,view", [
    ("", {"camera": CAMERA}),
    ("x" * 61, {"camera": CAMERA}),
    ("line\nbreak", {"camera": CAMERA}),
    ("ok", {"camera": {**CAMERA, "position": [1, 2]}}),
    ("ok", {"camera": {**CAMERA, "target": [1, "a", 3]}}),
    ("ok", {"camera": {**CAMERA, "up": [0, float("nan"), 1]}}),
    ("ok", {"frame": 2}),
    ("ok", "not a view"),
])
def test_what_is_not_a_view_is_refused(tmp_path, name, view):
    assert not save_view(tmp_path, name, view)["ok"]
    assert views_of(tmp_path)["views"] == []


def test_values_out_of_range_are_left_out(tmp_path):
    save_view(tmp_path, "v", {"camera": CAMERA, "frame": -1, "representation": "a b",
                              "superposed": "sideways", "pocket_cutoff": 99,
                              "publication": "yes", "shown": {"box": "on"}})
    assert views_of(tmp_path)["views"] == [{"name": "v", "camera": CAMERA, "shown": {}}]


def test_a_result_colouring_is_kept(tmp_path):
    """The colouring by one of the study's results is named "result:<key>",
    which a view did not keep."""
    save_view(tmp_path, "v", {"camera": CAMERA, "colour": "result:rmsf"})
    save_view(tmp_path, "w", {"camera": CAMERA, "colour": "result:rm sf"})
    kept = {view["name"]: view.get("colour") for view in views_of(tmp_path)["views"]}
    assert kept == {"v": "result:rmsf", "w": None}


def test_a_study_keeps_so_many(tmp_path):
    for i in range(MOST_VIEWS):
        assert save_view(tmp_path, f"v{i}", {"camera": CAMERA})["ok"]
    assert "at most" in save_view(tmp_path, "one more", {"camera": CAMERA})["reason"]
    assert save_view(tmp_path, "v3", {"camera": CAMERA, "frame": 1})["ok"]


def test_a_file_written_by_hand_is_read_as_far_as_it_is_a_view(tmp_path):
    (tmp_path / "viewer_views.json").write_text(json.dumps({"views": [
        {"name": "good", "camera": CAMERA}, {"name": "bad", "camera": "no"}, "x"]}),
        encoding="utf-8")
    assert [v["name"] for v in views_of(tmp_path)["views"]] == ["good"]
    (tmp_path / "viewer_views.json").write_text("{", encoding="utf-8")
    assert views_of(tmp_path) == {"ok": True, "views": []}


@pytest.fixture(scope="module")
def study(tmp_path_factory) -> Path:
    from tests.test_the_cartoon_is_dssp_of_each_frame import _helical_study

    return _helical_study(tmp_path_factory.mktemp("views") / "study", analyse=False)


def test_the_server_reads_and_writes_them(study):
    from fastmdxplora.gui.server import (
        GETS_ANSWERED_BEYOND_LOOPBACK,
        POSTS_ANSWERED_BEYOND_LOOPBACK,
        start_dashboard_session,
    )

    assert "/api/views" in GETS_ANSWERED_BEYOND_LOOPBACK
    assert "/api/views" not in POSTS_ANSWERED_BEYOND_LOOPBACK
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    base = session.url.rstrip("/")

    def post(body):
        request = urllib.request.Request(base + "/api/views", data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json",
                                                  "Origin": base}, method="POST")
        return json.loads(urllib.request.urlopen(request, timeout=30).read())

    try:
        saved = post({"action": "save", "name": "one", "view": {"camera": CAMERA}})
        listed = json.loads(urllib.request.urlopen(base + "/api/views", timeout=30).read())
        gone = post({"action": "delete", "name": "one"})
    finally:
        session.server.shutdown()
    assert saved["ok"] and listed["views"][0]["name"] == "one"
    assert gone == {"ok": True, "views": []}
    assert (study / "viewer_views.json").is_file()


def test_without_a_study_nothing_is_saved(tmp_path):
    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(tmp_path / "nothing"), host="127.0.0.1",
                                      port=0)
    base = session.url.rstrip("/")
    try:
        request = urllib.request.Request(
            base + "/api/views", data=json.dumps({"action": "save", "name": "one",
                                                  "view": {"camera": CAMERA}}).encode(),
            headers={"Content-Type": "application/json", "Origin": base}, method="POST")
        said = json.loads(urllib.request.urlopen(request, timeout=30).read())
    finally:
        session.server.shutdown()
    assert said == {"ok": False, "reason": "No study is open to save it in."}


def test_a_view_is_saved_and_shown_again_in_the_viewer(study):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    state = "window.FastMDXMoleculeViewer.STATE"
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 1000})
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
                          "'dashboard:trajectory-seek', {detail: {frame: 4}}))")
            page.wait_for_function(f"() => {state}.engine.frame() === 4")
            page.select_option("#viewer-rep", "sticks")
            hooks.tool(page, "side-view")
            page.click('[data-action="publication"]')
            page.evaluate(f"() => {state}.engine.restoreCamera({{position: [30, 30, 140],"
                          " target: [30, 32, 12], up: [0, 1, 0]})")
            page.wait_for_timeout(400)
            hooks.tool(page, "side-saved")
            page.click("#viewer-view-save")
            page.fill("#viewer-view-name", "Frame four, sticks")
            page.press("#viewer-view-name", "Enter")
            page.wait_for_function("() => document.getElementById('viewer-views').value"
                                   " === 'Frame four, sticks'")
            # Everything changed back.
            hooks.tool(page, "side-display")
            page.select_option("#viewer-rep", "cartoon")
            hooks.tool(page, "side-view")
            page.click('[data-action="publication"]')
            page.evaluate("() => window.dispatchEvent(new CustomEvent("
                          "'dashboard:trajectory-seek', {detail: {frame: 1}}))")
            page.wait_for_function(f"() => {state}.engine.frame() === 1")
            page.click('[data-action="reset-view"]')
            page.wait_for_timeout(600)
            hooks.tool(page, "side-saved")
            page.select_option("#viewer-views", "")
            page.select_option("#viewer-views", "Frame four, sticks")
            # Shown once it is said: the camera is set last.
            page.wait_for_function("() => (document.getElementById('sr-live')?.textContent"
                                   " || '') === 'Showing the view Frame four, sticks.'")
            shown = page.evaluate(f"""() => ({{camera: {state}.engine.cameraSnapshot(),
                publication: {state}.publication,
                background: {state}.engine.background,
                pressed: document.querySelector('[data-action="publication"]')
                    .getAttribute('aria-pressed'),
                rep: document.getElementById('viewer-rep').value}})""")
            saved = views_of(study)["views"][0]
            page.click("#viewer-view-forget")
            page.wait_for_function("() => document.getElementById('viewer-views').options"
                                   ".length === 1")
            browser.close()
    finally:
        session.server.shutdown()
    assert shown["publication"] is True and shown["background"] == 0xFFFFFF
    assert shown["pressed"] == "true" and shown["rep"] == "sticks"
    assert saved["frame"] == 4 and saved["representation"] == "sticks"
    assert saved["camera"]["target"] == pytest.approx([30, 32, 12], abs=1e-3)
    for key in ("position", "target", "up"):
        assert shown["camera"][key] == pytest.approx(saved["camera"][key], abs=1e-3)
    assert views_of(study)["views"] == []
    assert errors == []
