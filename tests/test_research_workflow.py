"""One study survives a server restart across research tools; no inference or MD."""

import hashlib
import shutil

import pytest
from PIL import Image

from fastmdxplora.gui.server import start_test_server
from tests.test_an_analysis_is_drawn_from_its_numbers import _analysis
from tests.test_the_drawing_scripts_run_in_a_browser import _write_study


def test_completed_study_research_workflow_survives_server_restart(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("Both-format acceptance requires ffmpeg")
    playwright = pytest.importorskip("playwright.sync_api")
    root = _write_study(tmp_path / "study")
    analysis = root / "analysis" / "rmsd"
    analysis.mkdir(parents=True)
    (analysis / "rmsd.dat").write_text("0 0.1\n1 0.2\n2 0.3\n3 0.2\n", encoding="utf-8")
    Image.new("RGB", (100, 100), "white").save(analysis / "rmsd.png")
    _analysis(root, "rmsf", "A 1 0.10\nA 2 0.25\n")
    for name in ("input", "prepared"):
        shutil.copyfile(root / "setup/topology.pdb", root / f"setup/{name}.pdb")
    original = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }
    server, url = start_test_server(root)
    try:
        with playwright.sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(url + "/#analysis")
            page.wait_for_function("FastMDXDashboard.state.appState.active_run")
            page.evaluate(
                "FastMDXDashboard.showAnalysis('rmsd'); FastMDXSeries.setRange('rmsd', [1,3])"
            )
            page.locator('.page[data-page="analysis"] [data-research-bookmark="rmsd"]').click()
            page.locator("#research-title").fill("Integrated graph range")
            page.locator("#research-save").click()
            page.locator(".research-bookmark").wait_for()
            with page.expect_download() as downloaded:
                page.locator("#research-export-bundle").click()
            bundle = tmp_path / "research.zip"
            downloaded.value.save_as(bundle)
            server.shutdown()
            server.server_close()
            server, url = start_test_server(root)
            page.goto(url + "/#overview")
            page.locator("#research-bookmarks-toggle").click()
            page.locator(".research-bookmark").wait_for()
            page.locator(".research-bookmark button", has_text="Restore").click()
            page.wait_for_function("JSON.stringify(FastMDXSeries.getRange('rmsd')) === '[1,3]'")
            assert page.locator("#research-bookmarks").is_visible()
            page.locator("#research-import-file").set_input_files(bundle)
            page.locator("#research-import-preview").wait_for(state="visible")
            page.locator("#research-import-apply").click()
            page.wait_for_function("document.querySelectorAll('.research-bookmark').length === 2")
            page.locator("#research-bookmarks-close").click()
            page.locator("#research-agent-toggle").click()
            page.locator("#agent-context-review summary").click()
            page.locator("#agent-inspect-context").click()
            page.wait_for_function(
                "document.querySelector('#agent-context-evidence').textContent.includes('rmsd')"
            )
            assert "1" in page.locator("#agent-context-evidence").inner_text()
            page.locator("#research-agent-close").click()
            page.evaluate("FastMDXDashboard.showAnalysis('rmsf')")
            chart = page.locator('.series-chart[data-analysis="rmsf"] svg')
            chart.wait_for()
            chart.focus()
            chart.press("Enter")
            page.wait_for_function("FastMDXMoleculeViewer.STATE.researchSelection?.resseq === 1")
            page.locator("#research-pin-residue").click()
            page.evaluate("FastMDXDashboard.navigate('analysis')")
            chart.focus()
            chart.press("ArrowRight")
            chart.press("Enter")
            page.wait_for_function("FastMDXMoleculeViewer.STATE.researchSelection?.resseq === 2")
            comparison = page.evaluate("FastMDXResearch.capture()")
            assert comparison["selection"]["resseq"] == 2
            assert comparison["comparison_selection"]["resseq"] == 1
            page.locator("#research-compare-residues").click()
            assert "pinned comparison residue" in page.locator("#agent-request").input_value()
            page.locator("#agent-inspect-context").click()
            page.wait_for_function(
                "document.querySelector('#agent-context-evidence').textContent"
                ".includes('Pinned comparison residue evidence')"
            )
            page.locator("#research-agent-close").click()
            page.locator("#research-clear-comparison").click()
            page.evaluate("async view => await FastMDXResearch.restore(view)", comparison)
            assert page.evaluate("FastMDXResearch.capture().comparison_selection.resseq") == 1
            assert page.locator("#research-compare-residues").is_enabled()
            page.evaluate("FastMDXDashboard.navigate('viewer')")
            page.wait_for_function("FastMDXMoleculeViewer.STATE.model !== null")
            page.evaluate("""async () => {
                await FastMDXMoleculeViewer.loadPlayback();
                await FastMDXMoleculeViewer.restoreResearchView({page:'viewer', frame:3,
                  selection:{chain:'A',resseq:2,resname:'ALA',atom:'CA'},
                  camera:[0,0,0,-50,0,0,0,1]});
            }""")
            page.locator("#research-bookmarks-toggle").click()
            page.locator("#research-title").fill("Residue comparison frame")
            page.locator("#research-save").click()
            page.wait_for_function("document.querySelectorAll('.research-bookmark').length === 3")
            page.locator("#research-bookmarks-close").click()
            page.locator("#research-clear-comparison").click()
            page.evaluate("FastMDXMoleculeViewer.restoreResearchView({page:'viewer',frame:6})")
            page.locator("#research-bookmarks-toggle").click()
            saved_frame = page.locator(".research-bookmark").filter(
                has_text="Residue comparison frame"
            )
            saved_frame.locator("button", has_text="Restore").click()
            page.wait_for_function("FastMDXResearch.capture().frame === 3")
            restored = page.evaluate("FastMDXResearch.capture()")
            assert restored["selection"]["resseq"] == 2
            assert restored["comparison_selection"]["resseq"] == 1
            assert restored["camera"] == [0, 0, 0, -50, 0, 0, 0, 1]
            page.locator("#research-bookmarks-close").click()
            page.locator("#clip-export-open").click()
            page.wait_for_function(
                "document.querySelector('#clip-status').textContent"
                ".includes('saved browser frames available')"
            )
            page.locator("#clip-last").fill("2")
            page.locator("#clip-format").select_option("both")
            page.locator("#clip-export-start").click()
            page.wait_for_function(
                "document.querySelector('#clip-status').textContent.includes('metadata saved')",
                timeout=60000,
            )
            for selector in ("#clip-download", "#clip-download-mp4"):
                response = page.request.get(url + page.locator(selector).get_attribute("href"))
                assert response.ok and response.body()
            page.locator("#clip-export-cancel").click()
            page.evaluate("FastMDXDashboard.navigate('overview')")
            page.locator("#preparation-audit-panel > summary").click()
            page.wait_for_function(
                "document.querySelector('#preparation-event').options.length > 0"
            )
            page.locator("#preparation-event").select_option("view-prepared")
            page.wait_for_function("FastMDXResearch.capture().audit_source === 'prepared'")
            audit_view = page.evaluate("FastMDXResearch.capture()")
            page.locator("#preparation-event").select_option("view-input")
            page.evaluate("async view => await FastMDXResearch.restore(view)", audit_view)
            assert page.evaluate("FastMDXResearch.capture().audit_source") == "prepared"
            assert not errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
    assert {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in original} == original
