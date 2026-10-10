"""The Overview reads the study's records again as a run of it ends.

Analyze again rewrites a study's records; the Overview read them again only
at its next 20 s, so for that long it still said what the old records said:
"Analysed by an earlier version", for means just judged by this version's
rules. Now a run of the open study that ends, however it was started (the
Analysis page's button, the Agent, a fix, the command line) and however it
ended, has its records read at once. A read asked for while another is on
its way is made once more after it, so the newer records are not missed.
"""

from __future__ import annotations

import json

import pytest

from tests.test_a_mean_judged_by_earlier_rules_is_said import _resolved, _without_rules
from tests.test_the_analysis_page_reads_as_a_whole import _analysis, _ar1, _manifest

BEGAN = "2026-10-09T13:15:02+00:00"
EARLIER = "#overview-results-said .overview-judged-earlier"


def _study(tmp_path):
    from tests.test_an_analysis_is_drawn_from_its_numbers import _figure
    from tests.test_the_drawing_scripts_run_in_a_browser import _write_study

    root = _write_study(tmp_path / "study")
    _manifest(root, {"rg": {"status": "ok"}, "rmsd": {"status": "ok"}})
    _analysis(root, "rg", _resolved(1))
    _analysis(root, "rmsd", 0.3 + 0.002 * _ar1(4000, 0.5, 3))
    record = root / "analysis" / "rg" / "options.json"
    judged = record.read_text(encoding="utf-8")
    _without_rules(root, "rg")
    for name in ("rg", "rmsd"):
        _figure(root / "analysis" / name / f"{name}.png")
    return root, record, judged


def _app_state(page, root, *, running, returncode=None):
    page.evaluate("d => window.dispatchEvent(new CustomEvent('dashboard:app-state', "
                  "{detail: d}))", {"active_run": str(root), "process_running": running,
                                    "started_at": BEGAN, "returncode": returncode})


def _session(root):
    pytest.importorskip("playwright.sync_api")
    from fastmdxplora.gui.server import start_dashboard_session

    return start_dashboard_session(output=str(root), host="127.0.0.1", port=0)


def _page(pw, url, errors):
    browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
    page = browser.new_page(viewport={"width": 1400, "height": 1000})
    page.set_default_timeout(60000)
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(url + "#overview", wait_until="domcontentloaded")
    page.wait_for_selector(EARLIER, state="attached")
    return browser, page


def test_the_overview_drops_the_earlier_rules_as_analyze_again_ends(tmp_path) -> None:
    from playwright.sync_api import sync_playwright

    root, record, judged = _study(tmp_path)
    errors: list[str] = []
    session = _session(root)
    try:
        with sync_playwright() as pw:
            browser, page = _page(pw, session.url, errors)
            # Started, as far as the page is told; the records are written by
            # hand below, as the run would write them.
            page.route("**/api/again", lambda route: route.fulfill(
                json={"ok": True, "said": "", "state": {"started_at": BEGAN}})
                if route.request.method == "POST" else route.continue_())
            page.evaluate("() => { location.hash = '#analysis'; }")
            page.wait_for_selector("#analysis-again:not([hidden])")
            page.click("#analysis-again")
            page.click("#analysis-again-ask .fix-confirm")
            page.wait_for_selector("#analysis-again-ask:has-text('Analyzing again.')")
            record.write_text(judged, encoding="utf-8")
            # Well inside the Overview's 20 s, so the read is the end's.
            with page.expect_response("**/api/overview", timeout=5000):
                _app_state(page, root, running=False, returncode=0)
            ended = page.text_content("#analysis-again-ask .again-said")
            page.evaluate("() => { location.hash = '#overview'; }")
            page.wait_for_selector(EARLIER, state="detached", timeout=5000)
            browser.close()
    finally:
        session.server.shutdown()
    assert json.loads(judged)["findings"]["mean"]["rules"] >= 2
    assert ended.startswith("Analyzed again.")
    assert errors == []


def test_a_run_started_elsewhere_that_fails_is_read_too(tmp_path) -> None:
    """Started by the Agent, a fix or the command line, the Analysis page's
    button knows nothing of the run; and a run that fails has still moved
    the old records aside. Either way the Overview reads again as it ends."""
    from playwright.sync_api import sync_playwright

    root, record, judged = _study(tmp_path)
    errors: list[str] = []
    session = _session(root)
    try:
        with sync_playwright() as pw:
            browser, page = _page(pw, session.url, errors)
            _app_state(page, root, running=True)
            record.write_text(judged, encoding="utf-8")
            with page.expect_response("**/api/overview", timeout=5000):
                _app_state(page, root, running=False, returncode=1)
            page.wait_for_selector(EARLIER, state="detached", timeout=5000)
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []


def test_a_read_asked_for_while_one_is_on_its_way_is_made(tmp_path) -> None:
    """The end comes while a read begun before it is on its way: that read
    may hold the records as they were, so one more is made after it."""
    from playwright.sync_api import sync_playwright

    root, record, judged = _study(tmp_path)
    errors: list[str] = []
    session = _session(root)
    try:
        with sync_playwright() as pw:
            browser, page = _page(pw, session.url, errors)
            page.evaluate("""() => { const real = window.fetch; window.__reads = 0;
                window.fetch = function (url, ...rest) {
                    if (!String(url).includes('/api/overview')) return real(url, ...rest);
                    window.__reads += 1;
                    return new Promise((done) => setTimeout(done, 1500))
                        .then(() => real(url, ...rest)); }; }""")
            page.evaluate("() => window.dispatchEvent(new CustomEvent('dashboard:run-changed'))")
            page.wait_for_function("() => window.__reads === 1")
            record.write_text(judged, encoding="utf-8")
            _app_state(page, root, running=False, returncode=0)
            page.wait_for_function("() => window.__reads >= 2", timeout=10000)
            page.wait_for_selector(EARLIER, state="detached", timeout=10000)
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
