"""The Config Builder's From a paper, driven in a browser.

A paper FastMDXplora read lists its MD studies, each with its
state and how each setting came from the paper's words. One opens in the
builder with every setting the paper states; one that needs the person
opens too, and the builder says it will not run until what it needs is
supplied, which a button then says it is, naming first what is still as
the paper left it. Several chosen are downloaded as
a zip.
"""

from __future__ import annotations

import copy
import io
import zipfile

import pytest

from tests._a_paper import STUDIES, field, jats, scripted


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    sync_api = pytest.importorskip("playwright.sync_api")
    from fastmdxplora.gui.server import start_dashboard_session
    from fastmdxplora.paper import studies as paper_studies

    root = tmp_path_factory.mktemp("paper")
    (root / "paper.xml").write_bytes(jats())
    studies = copy.deepcopy(STUDIES)
    studies[1]["fields"].pop("pdb_id")
    studies[1]["fields"]["structure_source"] = field(
        "built in silico", "The L50A mutant was built from it in silico")
    mp = pytest.MonkeyPatch()
    mp.setenv("FASTMDXPLORA_CACHE_DIR", str(root / "cache"))
    mp.setenv("FASTMDXPLORA_CONFIG_DIR", str(root / "config"))
    mp.setattr(paper_studies, "the_ai_model", lambda: (scripted(studies=studies), "test/model"))
    session = start_dashboard_session(output=str(root / "study"), host="127.0.0.1", port=0)
    try:
        with sync_api.sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            yield {"browser": browser, "url": session.url.rstrip("/") + "/#run", "root": root}
            browser.close()
    finally:
        session.server.shutdown()
        mp.undo()


def _read(site):
    page = site["browser"].new_context(viewport={"width": 1440, "height": 900},
                                       accept_downloads=True).new_page()
    page.set_default_timeout(60000)
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.errors = errors
    page.goto(site["url"], wait_until="domcontentloaded")
    page.wait_for_function("() => window.FastMDXRun && window.FastMDXRun.state.schema")
    assert page.is_hidden("#run-paper-card")
    page.click("#run-paper-toggle")
    assert page.get_attribute("#run-paper-toggle", "aria-expanded") == "true"
    page.fill("#run-paper-source", str(site["root"] / "paper.xml"))
    page.click("#run-paper-read")
    page.wait_for_selector("#run-paper-list .paper-study")
    return page


def test_the_studies_are_listed_with_their_states(site):
    page = _read(site)
    states = page.eval_on_selector_all(
        "#run-paper-list .paper-study", "items => items.map(i => [i.dataset.study, i.dataset.state])")
    assert states == [["S1", "with_differences"], ["S2", "needs_you"]]
    assert "3 x 100 ns" in page.inner_text("[data-study='S1'] .paper-study-length")
    page.click("[data-study='S1'] .paper-choices summary")
    temperature = page.inner_text("[data-study='S1'] .paper-choices-table")
    assert "simulation.temperature_K" in temperature and "at 300 K" in temperature
    assert "built in silico" in page.inner_text("[data-study='S2'] .paper-needs")
    assert not page.errors


def test_a_study_opens_in_the_builder_with_the_paper_s_settings(site):
    page = _read(site)
    opener = "[data-study='S1'] .paper-open"
    # An everyday button: the line icon, named on hover and to a screen reader.
    assert page.get_attribute(opener, "aria-label") == "Open S1 in the builder"
    assert page.eval_on_selector(opener, "b => b.classList.contains('line-btn') && !!b.querySelector('svg')")
    page.click(opener)
    page.wait_for_function("() => window.FastMDXRun.state.systems.length "
                           "&& window.FastMDXRun.state.systems[0].system === '1UBQ'")
    temperature = page.evaluate("() => window.FastMDXRun.state.values.simulation.temperature_K")
    assert float(temperature) == 300
    page.wait_for_function("() => (document.getElementById('run-status').textContent || '')"
                           ".startsWith('Checks pass')")
    assert not page.errors


def test_a_study_that_needs_the_person_will_not_run_until_supplied(site):
    page = _read(site)
    page.click("[data-study='S2'] .paper-open")
    page.wait_for_function("() => (document.getElementById('run-status').textContent || '')"
                           ".startsWith('Will not run')")
    assert "not complete" in page.inner_text("#run-status")
    page.click("#run-paper-supplied")
    # The structure is still the one to give: said, and not yet cleared.
    page.wait_for_function("() => (document.getElementById('run-paper-said').textContent || '')"
                           ".includes('Still as the paper left them: systems')")
    assert "not complete" in page.inner_text("#run-status")
    page.click("#run-paper-supplied")
    page.wait_for_function("() => !(document.getElementById('run-status').textContent || '')"
                           ".includes('not complete')")
    assert not page.errors


def test_the_chosen_studies_download_as_a_zip(site):
    page = _read(site)
    assert page.is_disabled("#run-paper-download")
    page.click("#run-paper-all")
    with page.expect_download() as waiting:
        page.click("#run-paper-download")
    download = waiting.value
    assert download.suggested_filename == "paper-studies.zip"
    with open(download.path(), "rb") as handle:
        names = zipfile.ZipFile(io.BytesIO(handle.read())).namelist()
    assert sorted(names) == ["paper-s1.yml", "paper-s2.yml"]
