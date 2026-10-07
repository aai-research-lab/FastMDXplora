"""A scene is opened on a page of its own, and written by an AI app.

The scenes written with a study are listed beside its saved views, and one
opens on a page of its own where the Viewer's engine is given the scene and
nothing else, as any viewer built on Mol* shows it. An AI app writes one
through `fastmdx mcp` to show the person what an answer is about: a frame, a
colouring, atoms highlighted.

Haemoglobin (1HHO), its frames as the GUI plays them.
"""

from __future__ import annotations

import json
import urllib.request
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("mdtraj")

from fastmdxplora.mcp import App, Workspace  # noqa: E402
from fastmdxplora.scenes import write_scene  # noqa: E402
from tests._mcp_wire import Wire  # noqa: E402
from tests import viewer_hooks as hooks


@pytest.fixture(scope="module")
def workspace(tmp_path_factory) -> Path:
    from fastmdxplora.gui.trajectory_frames import frames_info
    from tests.test_the_cartoon_is_dssp_of_each_frame import _helical_study

    root = tmp_path_factory.mktemp("scenes") / "work"
    root.mkdir()
    study = _helical_study(root / "haemoglobin", analyse=False)
    (study / "resolved_config.yml").write_text("systems:\n  - system: 1HHO\n",
                                               encoding="utf-8")
    assert frames_info(study)["available"]
    return root


@pytest.fixture
def wire(workspace, monkeypatch, tmp_path):
    monkeypatch.setenv("FASTMDXPLORA_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path / "settings"))
    wire = Wire(App(Workspace.at(workspace)).server())
    yield wire
    wire.close()


def _call(wire: Wire, **arguments) -> dict:
    return wire.request("tools/call", {"name": "write_scene", "arguments": arguments})["result"]


def _text(result: dict) -> str:
    return "\n".join(part["text"] for part in result["content"] if part["type"] == "text")


def test_an_ai_app_writes_a_scene_of_a_frame_with_atoms_highlighted(wire, workspace):
    said = _call(wire, study="haemoglobin", name="helix A", frame=4, colour="spectrum",
                 representation="cartoon", highlight="chainid 0 and resSeq 20 to 25",
                 labels=True)
    assert not said.get("isError"), said
    text = _text(said)
    assert "Wrote the scene helix A at haemoglobin/scenes/helix A.mvsx, frame 4" in text
    assert "molstar.org" in text
    archive = workspace / "haemoglobin" / "scenes" / "helix A.mvsx"
    state = json.loads(zipfile.ZipFile(archive).read("index.mvsj"))
    custom = state["root"]["custom"]["fastmdxplora"]
    assert custom["frame"] == 4 and custom["view"]["colour"] == "spectrum"
    highlight = [s for s in custom["selections"] if s["name"] == "highlight"][0]
    assert highlight["colour"] == "#e69f00" and highlight["labelled"] is True
    structure = state["root"]["children"][0]["children"][0]["children"][0]
    labels = [c for c in structure["children"]
              if c.get("children") and c["children"][0]["kind"] == "label"]
    assert len(labels) == 6


def test_a_saved_view_is_its_start(wire, workspace):
    from fastmdxplora.gui.saved_views import save_view

    save_view(workspace / "haemoglobin", "late", {
        "camera": {"position": [0, 0, 80], "target": [0, 0, 0], "up": [0, 1, 0]},
        "frame": 5, "colour": "chain"})
    said = _call(wire, study="haemoglobin", name="late", view="late")
    assert "frame 5" in _text(said)
    refused = _call(wire, study="haemoglobin", name="x", view="nothing")
    assert refused.get("isError") and "no view named 'nothing'" in _text(refused)
    assert "late" in _text(refused)


@pytest.mark.parametrize("arguments, said", [
    ({"frame": "4"}, "`frame` is a whole number."),
    ({"frame": True}, "`frame` is a whole number."),
    ({"representation": "ribbons"}, "`representation` is one of: cartoon,"),
    ({"superposed": "sideways"}, "`superposed` is one of: none, backbone, pocket"),
    ({"superposed_to": "crystal"}, "`superposed_to` is one of: first, start, deposited"),
    ({"smoothed_over": 4}, "`smoothed_over` is one of: 1, 3, 5, 9, 15."),
    ({"name": "../up"}, "A scene is named in 1 to 60"),
])
def test_what_is_not_a_scene_is_said(wire, arguments, said):
    result = _call(wire, **{"study": "haemoglobin", "name": "x", **arguments})
    assert result.get("isError") and said in _text(result)


def test_a_study_outside_the_workspace_is_not_written(wire, tmp_path):
    result = _call(wire, study=str(tmp_path), name="x")
    assert result.get("isError") and "outside the workspace" in _text(result)


def test_a_scene_opens_on_a_page_of_its_own(workspace):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    study = workspace / "haemoglobin"
    assert write_scene(study, "page one", {"frame": 2, "colour": "chain"})["ok"]
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        page_html = urllib.request.urlopen(
            session.url.rstrip("/") + "/scenes/page%20one/view", timeout=30).read().decode()
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            context = browser.new_context(viewport={"width": 1400, "height": 900})
            page = context.new_page()
            page.set_default_timeout(90000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#viewer", wait_until="domcontentloaded")
            if not page.evaluate("() => !!document.createElement('canvas').getContext('webgl')"):
                pytest.skip("this browser has no WebGL, so the viewer cannot render")
            page.wait_for_function("() => document.querySelectorAll('#viewer-scenes option')"
                                   ".length > 1")
            hooks.tool(page, "side-saved")
            page.select_option("#viewer-scenes", "page one")
            with context.expect_page() as opened:
                page.click("#viewer-scene-open")
            scene = opened.value
            scene.on("pageerror", lambda error: errors.append(str(error)))
            scene.wait_for_function("() => window.FastMDXScene && window.FastMDXScene.loaded",
                                    timeout=90000)
            atoms = scene.evaluate("""() => {
                const h = window.FastMDXScene.engine.plugin.managers.structure.hierarchy.current;
                return h.structures[0].cell.obj.data.elementCount;
            }""")
            title = scene.text_content("#scene-title")
            missing = context.new_page()
            missing.goto(session.url.rstrip("/") + "/scenes/nothing/view")
            body = missing.text_content("body")
            browser.close()
    finally:
        session.server.shutdown()
    import mdtraj as md

    assert 'data-scene="page one"' in page_html
    assert "/artifacts/scenes/page%20one.mvsx?download=1" in page_html
    assert title == "page one"
    assert atoms == md.load_topology(str(study / "simulation" / "frames_topology.pdb")).n_atoms
    assert "Scene not found" in body
    assert errors == []


def test_a_scene_is_shown_in_the_viewer_again(workspace):
    """Chosen in the list, a scene is shown in this Viewer as it was written:
    its frame, camera, representation and colouring; written without a
    camera, as an AI app writes one, it keeps the camera shown."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    study = workspace / "haemoglobin"
    camera = {"position": [10.0, 20.0, 90.0], "target": [1.0, 2.0, 3.0], "up": [0.0, 1.0, 0.0]}
    assert write_scene(study, "back again", {"frame": 4, "colour": "chain",
                                             "representation": "sticks", "camera": camera})["ok"]
    assert write_scene(study, "no camera", {"frame": 1, "colour": "element"})["ok"]
    state = "window.FastMDXMoleculeViewer.STATE"
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.set_default_timeout(90000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#viewer", wait_until="domcontentloaded")
            if not page.evaluate("() => !!document.createElement('canvas').getContext('webgl')"):
                pytest.skip("this browser has no WebGL, so the viewer cannot render")
            page.wait_for_function(f"() => window.FastMDXMoleculeViewer && {state}.model")
            page.wait_for_function("() => document.querySelectorAll('#viewer-scenes option')"
                                   ".length > 2")
            hooks.tool(page, "side-saved")
            assert page.is_disabled("#viewer-scene-show")
            page.select_option("#viewer-scenes", "back again")
            page.click("#viewer-scene-show")
            page.wait_for_function(f"() => {state}.colorMode === 'chain'"
                                   f" && {state}.representation === 'sticks'"
                                   f" && {state}.engine.frame() === 4")
            page.wait_for_function("() => document.getElementById('sr-live').textContent"
                                   " === 'Showing the scene back again.'")
            shown = page.evaluate(f"() => {state}.engine.cameraSnapshot()")
            page.select_option("#viewer-scenes", "no camera")
            page.click("#viewer-scene-show")
            page.wait_for_function(f"() => {state}.colorMode === 'element'"
                                   f" && {state}.engine.frame() === 1")
            kept = page.evaluate(f"() => {state}.engine.cameraSnapshot()")
            browser.close()
    finally:
        session.server.shutdown()
    for key in ("position", "target", "up"):
        assert shown[key] == pytest.approx(camera[key], abs=1e-3), key
        assert kept[key] == pytest.approx(shown[key], abs=1e-3), key
    assert errors == []


def test_an_ai_app_reads_the_views_and_scenes_of_a_study(wire, workspace):
    from fastmdxplora.gui.saved_views import save_view
    from fastmdxplora.gui.viewer_selections import save_selection

    study = workspace / "haemoglobin"
    save_view(study, "fitted late", {
        "camera": {"position": [0, 0, 80], "target": [0, 0, 0], "up": [0, 1, 0]},
        "frame": 5, "colour": "chain", "representation": "sticks", "superposed": "backbone",
        "superposed_to": "deposited", "smoothed_over": 3, "publication": True,
        "note": "The \"open\" loop"})
    save_view(study, "as it opens", {
        "camera": {"position": [0, 0, 80], "target": [0, 0, 0], "up": [0, 1, 0]}})
    save_selection(study, "helix", {"kind": "expression", "expression": "resSeq 3 to 18"})
    write_scene(study, "for the person", {"frame": 2})
    said = wire.request("tools/call", {"name": "views_of_study",
                                       "arguments": {"study": "haemoglobin"}})["result"]
    text = _text(said)
    assert not said.get("isError"), text
    assert ("- fitted late: frame 5, representation sticks, colour chain, superposed on "
            "backbone, fitted to deposited, smoothed over 3, publication look. "
            'The person\'s note: "The \\"open\\" loop"') in text
    assert "- as it opens: the structure as it opens\n" in text
    assert "- helix: resSeq 3 to 18" in text
    assert "- for the person: haemoglobin/scenes/for the person.mvsx" in text
