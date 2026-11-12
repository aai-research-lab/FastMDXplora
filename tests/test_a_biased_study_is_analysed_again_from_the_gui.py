"""The GUI's Analyze again offers a biased study's analyses alone.

A metadynamics study's analysis manifest lists the reweighting pass
(`reweighted`) among its results. Analyze again offered the results ticked,
and confirming was refused ("'reweighted' is not an analysis this release
has"); the Agent's offer named it too.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent))
from test_a_biased_study_reads_its_own_record import biased  # noqa: E402,F401


def test_the_page_offers_the_analyses_ticked_and_they_run(biased):  # noqa: F811
    from fastmdxplora import again
    from fastmdxplora.gui.server import _again_answer

    manifest = json.loads((biased / "analysis" / "analysis_manifest.json").read_text())
    assert "reweighted" in manifest["results"]
    offered = _again_answer(biased)
    assert offered["recorded"] == ["rmsd", "rg"], offered
    planned = again.plan(biased, ["analysis"], offered["recorded"])
    assert planned.analyses == ("rmsd", "rg")


def test_the_agent_s_offer_names_the_analyses_alone(biased):  # noqa: F811
    from fastmdxplora.gui.again_view import again_fix

    said = again_fix(biased, "analyze again", {})
    assert "fix" in said, said
    assert said["fix"]["fix"].startswith("Analyze this study again with rmsd, rg,")
    assert "reweighted" not in said["fix"]["fix"]


def test_a_record_saved_on_main_offers_them_too(biased):  # noqa: F811
    """No manifest read: the analyses the record asked for, as main saved
    it with the pass among them."""
    from fastmdxplora import again

    path = biased / "resolved_config.yml"
    saved = yaml.safe_load(path.read_text())
    saved["analysis"]["include"] = ["rmsd", "rg", "reweighted"]
    path.write_text(yaml.safe_dump(saved, sort_keys=False))
    (biased / "analysis" / "analysis_manifest.json").unlink()
    assert again.recorded_analyses(biased) == ["rmsd", "rg"]


@pytest.mark.parametrize("plan", [["rmsd", "reweighted"], ["rmsd"]])
def test_the_workspace_says_the_analyses_that_ran_alone(tmp_path, plan):
    from fastmdxplora.gui.workspace import _ran_analyses

    (tmp_path / "analysis").mkdir()
    (tmp_path / "analysis" / "analysis_manifest.json").write_text(json.dumps({"plan": plan}))
    (tmp_path / "manifest.json").write_text(json.dumps({"phases": [{"name": "analysis"}]}))
    assert _ran_analyses(tmp_path) == ["rmsd"]


def test_the_reweighting_s_row_is_not_offered_alone(biased):  # noqa: F811
    from fastmdxplora.gui.analysis_overview import overview_of

    rows = {row["analysis"]: row for row in overview_of(biased)["rows"]}
    assert rows["reweighted"]["again"] is False
    assert rows["rmsd"]["again"] is True


@pytest.fixture(scope="module")
def browser():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        launched = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        yield launched
        launched.close()


def test_the_reweighting_s_section_has_no_analyze_again(browser, biased):  # noqa: F811
    """Its section's button opened the panel with nothing ticked, the
    confirm disabled: a button that led nowhere."""
    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(biased), host="127.0.0.1", port=0)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.set_default_timeout(60000)
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.goto(session.url + "#analysis", wait_until="domcontentloaded")
        page.wait_for_selector('.analysis-cell-head[data-analysis="rmsd"] .cell-again',
                               state="attached")
        page.wait_for_selector('.analysis-section[data-cell="reweighted"]', state="attached")
        # The cells are rendered again once the overview's rows are read, as
        # its table is: read after them.
        page.wait_for_function(
            "() => window.FastMDXAnalysisPage && window.FastMDXAnalysisPage.overviewRead()")
        page.wait_for_selector('#analysis-results-table tr[data-analysis="rmsd"]',
                               state="attached")
        heads = page.evaluate("""() => Object.fromEntries([...document.querySelectorAll(
            '.analysis-section[data-cell]')].map((s) => [s.dataset.cell,
            !!s.querySelector('.cell-again')]))""")
    finally:
        page.close()
        session.stop()
    assert not errors, errors
    assert heads.get("rmsd") is True and heads.get("reweighted") is False, heads
