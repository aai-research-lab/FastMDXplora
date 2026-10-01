"""An Agent's answer about a study is followed by what the study recorded.

An answer quoted the analyses' numbers in prose, and a reader had no way to
check one short of finding the figure and reading its caption. Each analysis
an answer names is now listed under it with the mean its analysis recorded
(with its error and unit, or said not to be a measurement), and choosing one
opens its figure on the Analysis page.

The analyses are found by what the answer says, and the value shown is the
record's, whatever the answer said.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from fastmdxplora.gui.citations import cited_findings


def _analysis(root: Path, name: str, mean: dict | None = None, *, figure: bool = True) -> None:
    folder = root / "analysis" / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "options.json").write_text(json.dumps(
        {"analysis": name, "findings": {} if mean is None else {"mean": mean}}),
        encoding="utf-8")
    if figure:
        (folder / f"{name}.png").write_bytes(b"\x89PNG\r\n\x1a\n")


@pytest.fixture
def study(tmp_path) -> Path:
    root = tmp_path / "study"
    _analysis(root, "rmsd", {"mean": 0.112, "standard_error": 0.002,
                             "effective_samples": 11.0, "unit": "nm"})
    _analysis(root, "rg", {"mean": 0.3281, "standard_error": float("nan"), "unit": "nm",
                           "not_a_measurement": "80 frames cannot measure how correlated they are"})
    _analysis(root, "ligand_rmsd", {"mean": 0.21, "standard_error": 0.01, "unit": "nm"})
    _analysis(root, "rmsf")
    _analysis(root, "hbonds", {"not_a_measurement": "12 frames: too short", "n_frames": 12})
    return root


class TestWhatIsCited:
    def test_in_the_order_the_answer_names_them(self, study):
        cites = cited_findings("The radius of gyration held, and the RMSD settled.", study)
        assert [c["analysis"] for c in cites] == ["rg", "rmsd"]

    def test_the_value_is_the_records_with_its_error_and_unit(self, study):
        """Whatever the answer said: here it misquotes the RMSD."""
        (cite,) = cited_findings("The RMSD settled at 1.1 Angstrom.", study)
        assert cite == {"analysis": "rmsd", "label": "RMSD",
                        "value": "0.1120 ± 0.0020 nm", "withheld": ""}

    def test_a_mean_that_is_not_a_measurement_says_so(self, study):
        (cite,) = cited_findings("Rg was 0.33 nm.", study)
        assert cite["value"] == "0.3281 nm"
        assert cite["withheld"].startswith("80 frames")

    def test_an_analysis_that_withheld_its_mean(self, study):
        (cite,) = cited_findings("There were few hydrogen bonds.", study)
        assert cite["value"] == "no mean"
        assert cite["withheld"] == "12 frames: too short"

    def test_an_analysis_with_no_mean_is_cited_for_its_figure(self, study):
        (cite,) = cited_findings("The RMSF peaks at the loop.", study)
        assert cite == {"analysis": "rmsf", "label": "RMSF", "value": "", "withheld": ""}

    def test_the_ligands_rmsd_is_not_also_the_proteins(self, study):
        assert [c["analysis"] for c in cited_findings("The ligand RMSD stayed low.", study)] == [
            "ligand_rmsd"]
        both = cited_findings("The ligand RMSD stayed low while the RMSD rose.", study)
        assert [c["analysis"] for c in both] == ["ligand_rmsd", "rmsd"]

    def test_an_acronym_is_matched_as_written(self, study):
        """"rg" inside a word, or "RG" shouted, is not the radius of gyration;
        "Rg" is."""
        assert cited_findings("The merger was large.", study) == []
        assert [c["analysis"] for c in cited_findings("Rg is 0.33 nm.", study)] == ["rg"]

    def test_only_what_the_study_holds(self, study):
        assert cited_findings("The SASA and the Q value.", study) == []

    def test_nothing_to_cite_from(self, tmp_path):
        assert cited_findings("The RMSD settled.", tmp_path) == []
        assert cited_findings("The RMSD settled.", None) == []
        assert cited_findings("", tmp_path) == []

    def test_a_broken_record_is_cited_for_its_figure(self, study):
        (study / "analysis" / "rmsd" / "options.json").write_text("{not json", encoding="utf-8")
        (cite,) = cited_findings("The RMSD settled.", study)
        assert cite["value"] == ""

    def test_at_most_a_few(self, tmp_path):
        from fastmdxplora.gui.citations import MOST, NAMES

        for name in NAMES:
            _analysis(tmp_path, name)
        text = " ".join(spoken[0] for spoken in NAMES.values())
        assert len(cited_findings(text, tmp_path)) == MOST


def test_an_answer_comes_back_with_them(monkeypatch, study) -> None:
    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", tempfile.mkdtemp())
    import fastmdxplora.agent as agent_mod
    from fastmdxplora.gui import agent_panel

    monkeypatch.setattr(agent_mod, "completion_for", lambda *a, **k: (
        lambda prompt: "SAY: The RMSD settled at 0.112 nm after equilibration."))

    class Runtime:
        active_root = study

    answer = agent_panel.propose_endpoint({"request": "did the RMSD settle?"}, Runtime())
    assert answer["answer"].startswith("The RMSD settled")
    assert [c["analysis"] for c in answer["cites"]] == ["rmsd"]

    # With no study open there is nothing to cite, and the answer stands.
    alone = agent_panel.propose_endpoint({"request": "did the RMSD settle?"}, None)
    assert alone["answer"] and alone["cites"] == []


# --------------------------------------------------------------------------
# In a browser
# --------------------------------------------------------------------------

def test_the_page_shows_them_and_one_opens_its_figure(tmp_path) -> None:
    pytest.importorskip("playwright.sync_api")
    pytest.importorskip("mdtraj")
    import urllib.request

    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_an_analysis_is_drawn_from_its_numbers import _analysis as _drawn
    from tests.test_an_analysis_is_drawn_from_its_numbers import _manifest, _series
    from tests.test_the_drawing_scripts_run_in_a_browser import FRAMES, _write_study

    root = _write_study(tmp_path / "study")
    _manifest(root, saving_interval_ps=2.0)
    for name in ("rmsf", "rg", "rmsd"):
        _drawn(root, name, _series(FRAMES), {"mean": 0.112, "standard_error": 0.002,
                                             "effective_samples": 11.0, "discard": 5,
                                             "n_frames": FRAMES, "unit": "nm"})
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    entries = [
        {"role": "user", "text": "did the RMSD settle?"},
        {"role": "agent", "kind": "answer", "text": "The RMSD settled at 0.112 nm.",
         "cites": cited_findings("The RMSD settled at 0.112 nm.", root)},
    ]
    request = urllib.request.Request(
        session.url + "/api/agent/conversation", data=json.dumps({"entries": entries}).encode(),
        headers={"Content-Type": "application/json", "Origin": session.url}, method="POST")
    urllib.request.urlopen(request, timeout=10).read()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1280, "height": 700})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#agent", wait_until="domcontentloaded")
            chip = page.locator('#agent-thread .agent-cite[data-analysis="rmsd"]')
            chip.wait_for()
            name = chip.locator(".agent-cite-name").text_content()
            value = chip.locator(".agent-cite-value").text_content()
            chip.click()
            # What the page holds, said if a wait runs out: which page is
            # open, the figure it was opened on, and each card's place.
            why = """() => JSON.stringify({
              page: document.documentElement.getAttribute('data-page'),
              cited: document.querySelector('.page[data-page="analysis"]')?.dataset.cited,
              scrolled: document.querySelector('.main')?.scrollTop,
              cards: [...document.querySelectorAll(
                '.page[data-page="analysis"] .analysis-card[data-analysis]')].map((c) => {
                  const r = c.getBoundingClientRect();
                  return [c.getAttribute('data-analysis'), c.offsetParent !== null,
                          Math.round(r.top), Math.round(r.bottom)];
                }),
            })"""
            try:
                # Waited for on what the page keeps, not on the mark, which
                # fades after a moment.
                page.wait_for_function(
                    "() => document.querySelector('.page[data-page=\"analysis\"]')"
                    "?.dataset.cited === 'rmsd'")
                on = page.evaluate("document.documentElement.getAttribute('data-page')")
                # The scroll is smooth, and on a loaded machine slow: waited
                # for rather than given a fixed time. The card in view is the
                # one shown, not one in the grid the sections replace.
                page.wait_for_function(
                    "() => { const c = [...document.querySelectorAll("
                    "'.page[data-page=\"analysis\"] .analysis-card[data-analysis=\"rmsd\"]')]"
                    ".find((e) => e.offsetParent !== null);"
                    " if (!c) return false; const r = c.getBoundingClientRect();"
                    " return r.top < innerHeight && r.bottom > 0; }", timeout=60000)
            except Exception as error:
                raise AssertionError(f"{error}\n{page.evaluate(why)}") from None
            # And stays there once the figures around it have taken their
            # height: it ended a card above the view, here and on CI.
            page.wait_for_timeout(6000)
            settled = page.evaluate(why)
            # Results that have not changed leave the cards as they are,
            # with or without the sections: every poll replaced the flat
            # grid's cards, which reloaded each figure under the reader.
            kept = page.evaluate(
                """async () => {
                  const results = await (await fetch('/api/results')).json();
                  const flat = Object.assign({}, results, {analysis_sections: []});
                  const pick = () => [...document.querySelectorAll(
                    '.page[data-page="analysis"] .analysis-card[data-analysis="rmsd"]')]
                    .find((e) => e.offsetParent !== null);
                  window.FastMDXDashboard.applyResults(results);
                  const shown = pick();
                  window.FastMDXDashboard.applyResults(results);
                  const sections = pick() === shown;
                  const first = () => document.querySelector('#analysis-grid .analysis-card');
                  window.FastMDXDashboard.applyResults(flat);
                  const grid = first();
                  window.FastMDXDashboard.applyResults(flat);
                  return [sections, grid !== null && first() === grid];
                }""")
            seen = True
            browser.close()
    finally:
        session.server.shutdown()
    assert (name, value) == ("RMSD", "0.1120 ± 0.0020 nm")
    assert on == "analysis"
    assert kept == [True, True], "the same results replaced the cards"
    place = next(c for c in json.loads(settled)["cards"] if c[0] == "rmsd" and c[1])
    assert place[2] < 700 and place[3] > 0, settled
    assert seen
    assert errors == []
