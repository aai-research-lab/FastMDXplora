"""The Report page prints the report, all of it, in black on white.

The shell is the viewport and each column scrolls on its own, so a page
printed from the browser came out as one sheet: the sidebar, the first
screen of the report and the Log, the rest cut off, on the theme's ground.
And a study whose report phase had no WeasyPrint had no PDF and no way to
make one from the page: its notice said so, and nothing was offered.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_the_drawing_scripts_run_in_a_browser import _write_study  # noqa: E402

PARAGRAPH = ("The backbone RMSD rose over the first nanosecond and stayed near "
             "0.18 nm after it, within the spread of its own blocks. ")


def _with_report(root: Path, *, pdf: bool) -> Path:
    report = root / "report"
    report.mkdir(parents=True, exist_ok=True)
    sections = "\n\n".join(f"## Section {n}\n\n" + PARAGRAPH * 8 for n in range(1, 13))
    (report / "report.md").write_text(f"# The study's report\n\n{sections}\n",
                                      encoding="utf-8")
    if pdf:
        (report / "report.pdf").write_bytes(b"%PDF-1.4\n")
    else:
        (report / "not_produced.json").write_text(json.dumps([
            {"artifact": "report.pdf", "reason": "WeasyPrint is not installed."}]),
            encoding="utf-8")
    return root


@pytest.fixture(scope="module")
def sessions(tmp_path_factory):
    from fastmdxplora.gui.server import start_dashboard_session

    without = _write_study(tmp_path_factory.mktemp("print") / "without")
    with_pdf = without.parent / "with"
    shutil.copytree(without, with_pdf)
    _with_report(without, pdf=False)
    _with_report(with_pdf, pdf=True)
    started = {name: start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
               for name, root in (("without", without), ("with", with_pdf))}
    yield started
    for session in started.values():
        session.server.shutdown()


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        launched = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        yield launched
        launched.close()


def _report(browser, session, scheme="light"):
    page = browser.new_page(viewport={"width": 1440, "height": 900}, color_scheme=scheme)
    page.set_default_timeout(60000)
    page.goto(session.url + "#report", wait_until="domcontentloaded")
    page.wait_for_selector("#report-document:not([hidden]) h2")
    return page


def test_without_a_pdf_the_page_offers_to_print_one(browser, sessions) -> None:
    page = _report(browser, sessions["without"])
    try:
        button = page.locator("#report-downloads #report-print")
        assert button.is_visible()
        assert button.inner_text() == "Print or save as PDF"
    finally:
        page.close()


def test_with_a_pdf_its_download_is_the_one_offered(browser, sessions) -> None:
    page = _report(browser, sessions["with"])
    try:
        assert page.locator("#report-print").count() == 0
        assert page.locator("#report-downloads a", has_text="PDF").is_visible()
    finally:
        page.close()


def test_printed_it_is_the_document_alone_in_black_on_white(browser, sessions) -> None:
    # Followed from a dark system, the page is in the dark scheme.
    page = _report(browser, sessions["without"], scheme="dark")
    try:
        page.emulate_media(media="print")
        shown = page.evaluate("""() => {
            const shown = (sel) => {
                const el = document.querySelector(sel);
                return !!el && getComputedStyle(el).display !== 'none'
                    && el.getBoundingClientRect().height > 0;
            };
            const doc = document.getElementById('report-document');
            return {
                sidebar: shown('.sidebar'), log: shown('#side-panel'),
                notices: shown('#report-notices'), buttons: shown('#report-downloads'),
                document: shown('#report-document'),
                ink: getComputedStyle(doc.querySelector('h2')).color,
                ground: getComputedStyle(document.body).backgroundColor,
                // Declared on :root from the accents; a page printed as it
                // stands carries its status words in it.
                done: getComputedStyle(document.body).getPropertyValue('--status-completed').trim(),
            };
        }""")
        assert shown == {"sidebar": False, "log": False, "notices": False,
                         "buttons": False, "document": True,
                         "ink": "rgb(0, 0, 0)", "ground": "rgb(255, 255, 255)",
                         "done": "#1b7a45"}
    finally:
        page.close()


def test_printed_it_runs_to_its_last_section(browser, sessions) -> None:
    page = _report(browser, sessions["without"])
    try:
        page.emulate_media(media="print")
        # Under the shell, the page was a column of the window's height
        # scrolled within: printed, its first screen, then blank sheets.
        clipped = page.evaluate("""() => [...document.querySelectorAll(
                '.app-shell, .main, .page-shell, #report-document')]
            .filter(el => el.scrollHeight > el.clientHeight + 1)
            .map(el => el.className || el.id)""")
    finally:
        page.close()
    assert clipped == []
