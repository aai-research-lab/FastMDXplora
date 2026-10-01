"""The Report page's figures carry the chip the Analysis page gives them.

1135 put a chip on each figure of the Analysis page: the release, the
packages, the frames, the selection and options, and the command that
draws it again. The same figures in the report, on the Report page, had
none. Each figure an analysis drew now has the chip under it, the same
chip and panel, from the same record.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_a_figure_says_what_made_it import _trajectory

md = pytest.importorskip("mdtraj")


@pytest.fixture(scope="module")
def study(tmp_path_factory) -> Path:
    import logging

    from fastmdxplora.cli.main import main
    from fastmdxplora.utils import logging as fastmdx_logging

    base = tmp_path_factory.mktemp("reported")
    dcd, pdb = _trajectory(base / "input")
    root = base / "study"
    logger = logging.getLogger("fastmdx")
    kept = (logger.propagate, logger.level, list(logger.handlers),
            fastmdx_logging._console_handler)
    try:
        assert main(["explore", "-s", str(pdb), "--output", str(root),
                     "--include-phase", "analysis", "report", "--analyze-trajectory", str(dcd),
                     "--analyze-topology", str(pdb), "--analyze-analyses", "rmsd", "rg",
                     "--analyze-stride", "2"]) == 0
    finally:
        logger.propagate = kept[0]
        logger.setLevel(kept[1])
        logger.handlers[:] = kept[2]
        fastmdx_logging._console_handler = kept[3]
    return root


#: What the page holds when no chip appears: whether the report is shown,
#: the figures' addresses, whether the chip code is there, and what the
#: report route said. CI failed waiting for a chip, and passing everywhere
#: else, it could not say why.
_WHY = """async () => {
  const doc = document.getElementById("report-document");
  const empty = document.getElementById("report-empty");
  const page = document.querySelector('.page[data-page="report"]');
  let route = null;
  try {
    const data = await (await fetch("/api/report")).json();
    route = {ok: data.ok, reason: data.reason, figures_under: data.figures_under,
             provenance: Object.keys(data.figure_provenance || {}),
             html: (data.html || "").length, rendered: data.rendered};
  } catch (e) { route = String(e); }
  return {
    page_hidden: page ? page.hidden : "no page", active: document.body.dataset.page || null,
    document_hidden: doc ? doc.hidden : "no document", empty_hidden: empty ? empty.hidden : null,
    images: doc ? Array.from(doc.querySelectorAll("img")).map((img) => img.src) : [],
    made: document.querySelectorAll(".report-figure-made").length,
    chips: document.querySelectorAll("#report-document .figure-chip").length,
    dashboard: typeof window.FastMDXDashboard,
    figure_chip: window.FastMDXDashboard ? typeof window.FastMDXDashboard.figureChip : null,
    route,
  };
}"""


def _wait_for_the_chip(page, chip: str, said: list[str]) -> None:
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    try:
        page.wait_for_selector(chip)
    except PlaywrightTimeout:
        raise AssertionError(f"no chip: {page.evaluate(_WHY)}; the page said: {said[-20:]}") from None


def test_the_report_is_given_what_made_each_figure(study) -> None:
    from fastmdxplora.gui.report_page import report_payload

    page = report_payload(study)
    assert page["ok"] and sorted(page["figure_provenance"]) == ["rg", "rmsd"]
    assert "../analysis/rmsd/" in (study / "report" / "report.md").read_text(encoding="utf-8")


def test_each_figure_in_the_report_carries_its_chip(study) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 1000})
            page.set_default_timeout(60000)
            errors: list[str] = []
            said: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("pageerror", lambda error: said.append(f"error: {error}"))
            page.on("console", lambda message: said.append(f"{message.type}: {message.text}"))
            page.goto(session.url + "#report", wait_until="domcontentloaded")
            chip = '#report-document .report-figure-made .figure-chip[data-provenance="rmsd"]'
            _wait_for_the_chip(page, chip, said)
            chips = page.locator("#report-document .report-figure-made .figure-chip").count()
            page.locator(chip).first.click()
            panel = page.locator(chip).first.locator(
                "xpath=ancestor::div[contains(@class,'report-figure-made')]"
                "//div[contains(@class,'figure-provenance')]").first
            panel.wait_for(state="visible")
            said = panel.text_content()
            panel.locator('.figure-provenance-widths [data-width="single_column"]').click()
            narrow = panel.locator(".figure-provenance-command").text_content()
            browser.close()
    finally:
        session.server.shutdown()
    assert chips >= 2
    assert "Made by" in said and "30 frames, every 2nd frame" in said
    assert "fastmdx explore " in said and "--analyze-analyses rmsd" in said
    assert "--analyze-figure-width single_column" in narrow
    assert errors == []


def test_a_failed_fetch_leaves_the_report_standing(study) -> None:
    """A report fetched, then a fetch that fails, then one that succeeds:
    the failure hid the document, and the success found the same text
    already rendered and returned, so the report and its figures stayed
    hidden. CI failed once waiting for a chip to be visible. A fetch that
    fails before any has succeeded is tried again."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    failing = {"next": 2}

    def report(route):
        if failing["next"] > 0:
            failing["next"] -= 1
            route.abort()
        else:
            route.continue_()

    chip = '#report-document .report-figure-made .figure-chip[data-provenance="rmsd"]'
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 1000})
            page.set_default_timeout(60000)
            said: list[str] = []
            page.on("pageerror", lambda error: said.append(f"error: {error}"))
            page.on("console", lambda message: said.append(f"{message.type}: {message.text}"))
            page.route("**/api/report", report)
            page.goto(session.url + "#report", wait_until="domcontentloaded")
            # The first two fetches fail; the page asks again by itself.
            _wait_for_the_chip(page, chip, said)
            failing["next"] = 1
            page.evaluate("() => window.FastMDXReport.load()")
            page.evaluate("() => window.FastMDXReport.load()")
            shown = page.evaluate("() => !document.getElementById('report-document').hidden")
            visible = page.locator(chip).first.is_visible()
            browser.close()
    finally:
        session.server.shutdown()
    assert shown and visible
