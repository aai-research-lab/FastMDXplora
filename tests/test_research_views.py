"""Research evidence is study scoped, persisted, and restored by a real page."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from fastmdxplora.gui.research import bookmarks_endpoint, clean_view, context_for
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

    save(study)
    with urlopen(served + "/api/research/bookmarks") as response:
        assert len(json.load(response)["bookmarks"]) == 1
    with urlopen(served + "/api/artifacts") as response:
        assert ".research" not in json.dumps(json.load(response))
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
                        Request(public + "/api/research/bookmarks", data=b"{}", method="POST",
                                headers={"Content-Type": "application/json"})):
            with pytest.raises(HTTPError) as denied:
                urlopen(request)
            assert denied.value.code == 403


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
        page.locator("#research-bookmarks-toggle").click()
        page.locator("#research-title").fill("My RMSD range")
        page.locator("#research-note").fill("Does this plateau persist?")
        page.locator("#research-save").click()
        page.locator(".research-bookmark").wait_for()
        page.reload()
        page.locator("#research-bookmarks-toggle").click()
        page.locator(".research-bookmark").wait_for()
        assert "Does this plateau persist?" in page.locator(".research-bookmark").inner_text()
        page.locator(".research-bookmark button", has_text="Restore").click()
        assert page.evaluate("FastMDXSeries.getRange('rmsd')") == [1, 3]
        with page.expect_download() as download:
            page.locator("#research-export").click()
        exported = tmp_path / "export.json"
        download.value.save_as(exported)
        assert json.loads(exported.read_text())["bookmarks"][0]["view"]["range"] == [1, 3]
        page.locator("#research-bookmarks-close").click()
        page.locator("#research-agent-toggle").click()
        page.screenshot(path=str(tmp_path / "research-dashboard.png"))
        assert not errors
        browser.close()


def test_viewer_bookmark_restores_frame_camera_and_selection(tmp_path):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    from tests.test_the_drawing_scripts_run_in_a_browser import _write_study

    root = _write_study(tmp_path / "viewer-study")
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
            assert view["selection"]["resseq"] == 1
            assert view["camera"] == [0, 0, 0, -50, 0, 0, 0, 1]
            result = page.evaluate("v => FastMDXMoleculeViewer.restoreResearchView(v)",
                                   {**view, "frame": 6})
            assert result == "Bookmark restored."
            assert page.evaluate("FastMDXResearch.capture().frame") == 6
            assert page.evaluate("v => FastMDXMoleculeViewer.restoreResearchView(v)", view) == "Bookmark restored."
            assert page.evaluate("FastMDXResearch.capture().frame") == 3
            assert "changed" in page.evaluate("v => FastMDXMoleculeViewer.restoreResearchView(v)",
                                                {**view, "playback_signature": "stale"})
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
