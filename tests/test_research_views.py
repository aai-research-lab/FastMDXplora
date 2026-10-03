"""Research evidence is study scoped, persisted, and restored by a real page."""
from __future__ import annotations

import base64
import io
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from fastmdxplora.gui.research import (
    bookmarks_endpoint,
    clean_view,
    context_for,
    screenshot_endpoint,
)
from fastmdxplora.gui.server import start_test_server


@pytest.fixture
def study(tmp_path):
    from PIL import Image

    (tmp_path / "manifest.json").write_text('{"phases": []}', encoding="utf-8")
    folder = tmp_path / "analysis" / "rmsd"
    folder.mkdir(parents=True)
    (folder / "rmsd.dat").write_text("0 0.1\n1 0.2\n2 0.3\n3 0.2\n", encoding="utf-8")
    Image.new("RGB", (100, 100), "white").save(folder / "rmsd.png")
    return SimpleNamespace(active_root=tmp_path, workspace_root=tmp_path)


def save(runtime, **changes):
    return bookmarks_endpoint(runtime, {"study": str(runtime.active_root.resolve()),
        "title": "Binding pocket", "note": "Check this residue",
        "view": {"page": "viewer", "frame": 2, "camera": [0, 0, 0, 100, 0, 0, 0, 1],
                 "selection": {"chain": "A", "resseq": 1, "resname": "ALA", "atom": "CA"}},
        **changes})


def test_views_are_bounded_and_cannot_carry_actions():
    assert clean_view({"page": [], "frame": -1, "camera": [float("nan")] * 8,
                       "analysis": "../../secrets", "action": "run"}) == {"page": "overview"}
    assert clean_view({"page": "analysis", "range": [2, 1]}) == {"page": "analysis"}
    assert "figure" not in clean_view({"figure": "../../private.png"})
    assert "figure" not in clean_view({"figure": "https://example.com/image.png"})
    assert "field_value" not in clean_view({"field_value": "x" * 2001})
    assert clean_view({"field_value": False})["field_value"] is False


def test_current_draft_setting_is_labelled_separately_from_applied_values(study):
    text = context_for(study.active_root, {"page": "run", "field": "temperature_kelvin", "field_value": "310"})
    assert '"field_value": "310"' in text
    assert "current browser draft value" in text
    assert "not an applied" in text


def test_bookmarks_survive_a_new_runtime_and_update_and_delete(study):
    answer = save(study)
    assert answer["ok"]
    row = answer["bookmarks"][0]
    fresh = SimpleNamespace(active_root=study.active_root)
    assert bookmarks_endpoint(fresh)["bookmarks"][0] == row
    updated = save(fresh, id=row["id"], note="Revised observation")
    assert len(updated["bookmarks"]) == 1
    assert updated["bookmarks"][0]["created_at"] == row["created_at"]
    assert bookmarks_endpoint(fresh, {"study": str(fresh.active_root), "action": "delete",
                                      "id": row["id"]})["bookmarks"] == []


def test_stale_study_and_no_study_are_refused(study):
    assert not save(study, study="another study")["ok"]
    assert not bookmarks_endpoint(SimpleNamespace(active_root=None))["ok"]
    assert not (study.active_root / ".research").exists()


def test_tags_survive_edits_without_implicit_replacement(study):
    first = save(study, tags=[" Graph ", "Graph", "Observation", "Custom research tag"])
    row = first["bookmarks"][0]
    assert row["tags"] == ["Graph", "Observation", "Custom research tag"]
    assert row["version"] == 2
    edited = save(study, id=row["id"], note="Edited")
    assert edited["bookmarks"][0]["tags"] == row["tags"]
    assert "Trajectory frame" in bookmarks_endpoint(study)["tags"]


def test_stale_edit_and_delete_preserve_the_newer_note(study):
    row = save(study)["bookmarks"][0]
    updated = save(study, id=row["id"], expected_updated_at=row["updated_at"], note="Newer note")
    assert updated["ok"]
    assert not save(study, id=row["id"], expected_updated_at=row["updated_at"], note="Stale note")["ok"]
    assert not bookmarks_endpoint(study, {"study": str(study.active_root), "action": "delete",
        "id": row["id"], "expected_updated_at": row["updated_at"]})["ok"]
    assert bookmarks_endpoint(study)["bookmarks"] == updated["bookmarks"]


@pytest.mark.parametrize("tags", [None, "Graph", [""], ["x" * 49], [False], ["tag"] * 17])
def test_invalid_tags_do_not_change_existing_bookmarks(study, tags):
    first = save(study)
    assert not save(study, tags=tags)["ok"]
    assert bookmarks_endpoint(study)["bookmarks"] == first["bookmarks"]


def test_display_state_keeps_only_supported_view_controls():
    view = clean_view({"page": "viewer", "display": {"representation": "ballAndStick",
        "colorMode": "secondary_structure", "visibility": {"protein": True,
            "water": False, "ions": "yes", "action": "run"}, "action": "run"}})
    assert view["display"] == {"representation": "ballAndStick",
        "colorMode": "secondary_structure", "visibility": {"protein": True, "water": False}}
    assert "display" not in clean_view({"display": {"representation": [], "colorMode": {}}})


def png_data(width=100, height=50):
    from PIL import Image, PngImagePlugin

    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("private", "must not survive image normalization")
    stream = io.BytesIO()
    Image.new("RGB", (width, height), "blue").save(stream, format="PNG", pnginfo=metadata)
    return "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode()


def test_screenshot_survives_note_edits_and_metadata_is_removed(study):
    from PIL import Image

    answer = save(study, screenshot=png_data())
    assert answer["ok"]
    row = answer["bookmarks"][0]
    image = screenshot_endpoint(study, row["id"])
    assert image["ok"]
    with Image.open(io.BytesIO(base64.b64decode(image["image"].partition(",")[2]))) as pixels:
        assert pixels.size == (100, 50)
        assert pixels.getpixel((0, 0)) == (0, 0, 255)
        assert "private" not in pixels.info
    assert save(study, id=row["id"], note="Edited")["bookmarks"][0]["screenshot"] == row["screenshot"]
    removed = save(study, id=row["id"], remove_screenshot=True)
    assert "screenshot" not in removed["bookmarks"][0]
    assert not screenshot_endpoint(study, row["id"])["ok"]


@pytest.mark.parametrize("screenshot", ["data:image/svg+xml,<svg/>", "data:image/png;base64,invalid", png_data(1601, 1)])
def test_invalid_screenshot_does_not_replace_saved_notes(study, screenshot):
    first = save(study)
    assert not save(study, id=first["bookmarks"][0]["id"], screenshot=screenshot)["ok"]
    assert bookmarks_endpoint(study)["bookmarks"] == first["bookmarks"]
    assert not screenshot_endpoint(study, "../../secrets")["ok"]


def test_hosted_path_policy_and_stale_data_apply(study):
    answer = bookmarks_endpoint(study, {"study": "~/study", "title": "Hosted note",
        "view": {"page": "analysis"}}, path_for=lambda path: study.active_root if path == "~/study" else None)
    assert answer["ok"]
    assert not bookmarks_endpoint(study, {"study": "~/outside", "title": "Outside",
        "view": {}}, path_for=lambda path: None)["ok"]
    study.data_stale = True
    assert not bookmarks_endpoint(study)["ok"]


def test_a_malformed_store_is_preserved(study):
    path = study.active_root / ".research" / "bookmarks.json"
    path.parent.mkdir()
    path.write_text("broken", encoding="utf-8")
    assert not save(study)["ok"]
    assert path.read_text() == "broken"


def test_concurrent_saves_keep_all_notes(study):
    with ThreadPoolExecutor(max_workers=4) as pool:
        answers = list(pool.map(lambda i: save(study, title=f"Note {i}"), range(12)))
    assert all(answer["ok"] for answer in answers), [a for a in answers if not a["ok"]]
    assert len(bookmarks_endpoint(study)["bookmarks"]) == 12


def test_context_reads_the_selected_analysis_and_schema(study):
    context = context_for(study.active_root, {"page": "analysis", "analysis": "rmsd"})
    assert "read from this study" in context
    assert '"available_range": [0.0, 3.0]' in context
    assert "no readable series" in context_for(study.active_root, {"analysis": "missing"})
    assert "Setting setup.ph:" in context_for(study.active_root, {"field": "setup.ph"})


def test_agent_receives_structured_context(study, monkeypatch):
    import fastmdxplora.agent as agent
    from fastmdxplora.gui.agent_panel import propose_endpoint

    prompts = []
    def complete(prompt):
        prompts.append(prompt)
        return "SAY: This is the selected RMSD series."
    monkeypatch.setattr(agent, "completion_for", lambda: complete)
    answer = propose_endpoint({"request": "Explain this plot", "view_context": {
        "page": "analysis", "analysis": "rmsd", "range": [1, 3]}}, study)
    assert answer["answer"]
    assert "Dashboard view" in prompts[0]
    assert '"range": [1, 3]' in prompts[0]
    assert "Selected analysis, read from this study" in prompts[0]


@pytest.fixture
def served(study):
    server, url = start_test_server(study.active_root)
    yield url
    server.shutdown()
    server.server_close()


def test_api_does_not_expose_bookmarks_as_artifacts(served, study):
    from fastmdxplora.gui.exploration import DashboardRuntime
    from tests.test_a_public_dashboard_answers_only_what_it_lists import _serving

    row = save(study, screenshot=png_data())["bookmarks"][0]
    with urlopen(served + "/api/research/bookmarks") as response:
        assert len(json.load(response)["bookmarks"]) == 1
    with urlopen(served + "/api/artifacts") as response:
        assert ".research" not in json.dumps(json.load(response))
    with urlopen(served + "/api/research/bookmarks/image?id=" + row["id"]) as response:
        assert json.load(response)["image"].startswith("data:image/png;base64,")
    request = Request(served + "/api/research/bookmarks", data=b"{}", method="POST",
                      headers={"Origin": "https://example.com", "Content-Type": "application/json"})
    with pytest.raises(HTTPError) as exc:
        urlopen(request)
    assert exc.value.code == 403
    runtime = DashboardRuntime(workspace_root=study.active_root,
                               exploration_root=study.active_root.parent,
                               active_root=study.active_root)
    with _serving(runtime, allow_control=False) as public:
        for request in (public + "/api/research/bookmarks",
                        public + "/api/research/bookmarks/image?id=" + row["id"],
                        public + "/api/research/bookmarks/restore?id=" + row["id"],
                        public + "/api/research/bookmarks/export",
                        Request(public + "/api/research/bookmarks/import-preview", data=b"{}", method="POST"),
                        Request(public + "/api/research/bookmarks/import", data=b"{}", method="POST"),
                        Request(public + "/api/research/bookmarks", data=b"{}", method="POST",
                                headers={"Content-Type": "application/json"})):
            with pytest.raises(HTTPError) as denied:
                urlopen(request)
            assert denied.value.code == 403


def test_import_api_checks_origin_and_size_before_preview(served, study, monkeypatch):
    import fastmdxplora.gui.research_bundle as bundles

    request = Request(served + "/api/research/bookmarks/import-preview", data=b"{}", method="POST",
                      headers={"Origin": "https://example.com", "Content-Type": "application/octet-stream"})
    with pytest.raises(HTTPError) as denied:
        urlopen(request)
    assert denied.value.code == 403
    monkeypatch.setattr(bundles, "MAX_BUNDLE_BYTES", 100)
    request = Request(served + "/api/research/bookmarks/import-preview", data=b"x" * 101, method="POST")
    with pytest.raises(HTTPError) as oversized:
        urlopen(request)
    assert oversized.value.code == 400
    assert not (study.active_root / ".research").exists()


@pytest.mark.parametrize("theme", ["graphite", "ink", "paper"])
def test_populated_bookmark_and_import_preview_layouts(served, study, tmp_path, theme):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    for index in range(18):
        result = save(study, title=f"Observation {index}: " + "LongUnbrokenResearchTitle" * 5,
                    note="A detailed scientific observation with retained uncertainty. " * 12,
                    tags=["Graph", "Figure"], view={"page": "analysis", "analysis": "rmsd"},
                    **({"screenshot": png_data()} if index == 17 else {}))
        assert result["ok"]
    # Simulate an unavailable saved image inside this disposable study only.
    for image in (study.active_root / ".research/screenshots").glob("*.png"):
        image.unlink()
    missing = next(row for row in result["bookmarks"] if row.get("screenshot"))
    missing_message = screenshot_endpoint(study, missing["id"])["error"]
    before = (study.active_root / "analysis/rmsd/rmsd.dat").read_bytes()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.add_init_script("localStorage.setItem('fmx.theme', " + json.dumps(theme) + ")")
        page.goto(served + "/#analysis")
        page.locator("#research-bookmarks-toggle").click()
        page.locator(".research-bookmark").first.wait_for()
        page.get_by_text(missing_message, exact=True).wait_for()
        assert page.locator(".research-screenshot").count() == 0
        with page.expect_download() as download:
            page.locator("#research-export").click()
        exported = tmp_path / "populated-bookmarks.json"
        download.value.save_as(exported)
        for state in ("populated", "import-preview"):
            if state == "import-preview":
                page.locator("#research-import-file").set_input_files(exported)
                page.locator("#research-import-preview").wait_for(state="visible")
                assert "18 matching IDs" in page.locator("#research-import-summary").inner_text()
            for zoom in (100, 200):
                page.evaluate("size => document.documentElement.style.fontSize = size + '%'", zoom)
                for width in (1440, 1280, 1024, 768, 390):
                    page.set_viewport_size({"width": width, "height": 1000})
                    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
                    outside = page.locator("#research-bookmarks").evaluate("""el =>
                        Array.from(el.querySelectorAll('button,input,select,textarea'))
                        .filter(node => node.checkVisibility())
                        .filter(node => {const r=node.getBoundingClientRect(); return r.x < -1 || r.right > innerWidth+1;})
                        .map(node => node.id || node.textContent)""")
                    assert not outside, (theme, state, zoom, width, outside)
                    assert page.locator(".research-bookmark").first.evaluate("el => el.scrollWidth <= el.clientWidth + 1"), (theme, state, zoom, width)
                    assert len(bookmarks_endpoint(study)["bookmarks"]) == 18
        assert (study.active_root / "analysis/rmsd/rmsd.dat").read_bytes() == before
        assert not errors
        browser.close()


def test_browser_docks_agent_saves_restores_and_exports(served, study, tmp_path):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(served + "/#analysis")
        page.wait_for_function("window.FastMDXDashboard.state.appState.active_run")
        page.evaluate("FastMDXDashboard.showAnalysis('rmsd')")
        page.locator("#research-agent-toggle").click()
        assert page.locator("#research-agent-dock [data-page=agent]").is_visible()
        assert "rmsd" in page.locator("#research-context").inner_text()
        page.evaluate("FastMDXDashboard.navigate('viewer')")
        assert page.locator("#research-agent-dock [data-page=agent]").is_visible()
        page.locator("#research-agent-close").click()
        page.evaluate("FastMDXDashboard.showAnalysis('rmsd'); FastMDXSeries.setRange('rmsd', [1,3])")
        page.locator(".series-range").wait_for()
        assert "View cropped" in page.locator(".series-chart").inner_text()
        page.locator('.page[data-page="analysis"] [data-research-bookmark="rmsd"]').click()
        page.locator("#research-title").fill("My RMSD range")
        page.locator("#research-note").fill("Does this plateau persist?")
        page.locator('#research-tags input[value="Graph"]').check()
        page.locator('#research-tags input[value="Figure"]').check()
        page.locator("#research-screenshot").check()
        page.locator("#research-save").click()
        page.locator(".research-bookmark").wait_for()
        assert "without screenshot" not in page.locator("#research-status").inner_text(), page.locator("#research-status").inner_text()
        page.wait_for_function("document.querySelector('.research-screenshot')?.complete && document.querySelector('.research-screenshot')?.naturalWidth > 0")
        page.reload()
        page.locator("#research-bookmarks-toggle").click()
        page.locator(".research-bookmark").wait_for()
        assert "Does this plateau persist?" in page.locator(".research-bookmark").inner_text()
        assert "Load a study" not in page.locator("#research-status").inner_text()
        assert "Capture current view:" in page.locator("#research-capture-scope").inner_text()
        page.locator(".research-bookmark button", has_text="Edit").click()
        assert "Editing saved view:" in page.locator("#research-capture-scope").inner_text()
        page.locator("#research-screenshot").check()
        assert "Capture current view:" in page.locator("#research-capture-scope").inner_text()
        page.locator("#research-screenshot").uncheck()
        assert "Saved selection is retained" in page.locator("#research-capture-scope").inner_text()
        page.locator("#research-cancel-edit").click()
        assert "Capture current view:" in page.locator("#research-capture-scope").inner_text()
        page.locator("#research-tag-filter").select_option("Structure")
        assert page.locator(".research-bookmark").count() == 0
        page.locator("#research-tag-filter").select_option("Figure")
        assert page.locator(".research-bookmark").count() == 1
        page.locator("#research-search").fill("missing phrase")
        assert page.locator(".research-bookmark").count() == 0
        page.locator("#research-search").fill("plateau")
        assert page.locator(".research-bookmark").count() == 1
        page.locator(".research-bookmark button", has_text="Restore").click()
        page.wait_for_function("JSON.stringify(FastMDXSeries.getRange('rmsd')) === '[1,3]'")
        assert page.evaluate("FastMDXSeries.getRange('rmsd')") == [1, 3]
        with page.expect_download() as download:
            page.locator("#research-export").click()
        exported = tmp_path / "export.json"
        download.value.save_as(exported)
        assert json.loads(exported.read_text())["bookmarks"][0]["view"]["range"] == [1, 3]
        assert json.loads(exported.read_text())["bookmarks"][0]["tags"] == ["Graph", "Figure"]
        assert "screenshot" not in json.loads(exported.read_text())["bookmarks"][0]
        with page.expect_download() as download:
            page.locator("#research-export-bundle").click()
        bundle = tmp_path / "research.zip"
        download.value.save_as(bundle)
        assert page.locator("#research-export-download").is_visible()
        assert page.locator("#research-export-download").get_attribute("href").startswith(
            "/api/research/bookmarks/export?images=1"
        )
        page.locator("#research-import-file").set_input_files(bundle)
        page.locator("#research-import-preview").wait_for(state="visible")
        assert "1 matching IDs" in page.locator("#research-import-summary").inner_text()
        assert page.locator(".research-bookmark").count() == 1
        page.locator("#research-import-apply").click()
        page.wait_for_function("document.querySelectorAll('.research-bookmark').length === 2")
        assert page.locator("#research-export-download").is_hidden()
        page.locator("#research-bookmarks-close").click()
        page.locator("#research-agent-toggle").click()
        page.screenshot(path=str(tmp_path / "research-dashboard.png"))
        assert not errors
        browser.close()


def test_viewer_bookmark_restores_frame_camera_and_selection(tmp_path):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    from fastmdxplora.gui.live_frames import write_live_frame
    from tests.test_the_drawing_scripts_run_in_a_browser import _write_study

    root = _write_study(tmp_path / "viewer-study")
    write_live_frame(root / "simulation", pdb_text=(root / "setup/topology.pdb").read_text(),
                     frame_index=2500, stage="production", simulation_time_ns=0.005)
    server, url = start_test_server(root)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.goto(url + "/#viewer")
            page.wait_for_function("FastMDXMoleculeViewer.STATE.model !== null")
            page.evaluate("""async () => {
              const v = FastMDXMoleculeViewer;
              await v.loadPlayback();
              await v.restoreResearchView({page:'viewer', frame:3,
                selection:{chain:'A', resseq:1, resname:'ALA', atom:'CA'},
                camera:[0,0,0,-50,0,0,0,1]});
            }""")
            view = page.evaluate("FastMDXResearch.capture()")
            assert view["frame"] == 3
            page.evaluate("FastMDXMoleculeViewer.pollLiveFrame()")
            assert page.evaluate("FastMDXResearch.capture().frame") == 3
            assert page.locator("#overlay-frame").inner_text() == "frame 3"
            time_label = page.locator("#overlay-simtime").inner_text()
            page.evaluate("window.dispatchEvent(new CustomEvent('dashboard:status-updated', {detail:{status:{status:'completed',stage:'report',simulation_time_completed_ns:9}}}))")
            assert page.locator("#overlay-frame").inner_text() == "frame 3"
            assert page.locator("#overlay-simtime").inner_text() == time_label
            assert view["selection"]["resseq"] == 1
            assert view["camera"] == [0, 0, 0, -50, 0, 0, 0, 1]
            page.locator("#viewer-rep").select_option("sticks")
            page.locator('input[data-vis="hydrogens"]').check()
            result = page.evaluate("v => FastMDXMoleculeViewer.restoreResearchView(v)",
                                   {**view, "frame": 6})
            assert result == "Bookmark restored."
            assert page.evaluate("FastMDXResearch.capture().frame") == 6
            assert page.evaluate("v => FastMDXMoleculeViewer.restoreResearchView(v)", view) == "Bookmark restored."
            assert page.evaluate("FastMDXResearch.capture().frame") == 3
            assert page.locator("#viewer-rep").input_value() == view["display"]["representation"]
            assert not page.locator('input[data-vis="hydrogens"]').is_checked()
            residue = {**view, "selection": {"chain": "A", "resseq": 1, "resname": "ALA", "atom": ""}}
            assert page.evaluate("v => FastMDXMoleculeViewer.restoreResearchView(v)", residue) == "Bookmark restored."
            assert page.evaluate("FastMDXResearch.capture().selection.atom") == ""
            page.evaluate("v => FastMDXResearch.restore(v)", {**view, "study": "different-study"})
            assert "study changed" in page.locator("#research-status").inner_text().lower()
            assert page.evaluate("FastMDXResearch.capture().frame") == 3
            assert "changed" in page.evaluate("v => FastMDXMoleculeViewer.restoreResearchView(v)",
                                                {**view, "playback_signature": "stale"})
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
