"""A cited figure opened from a restored answer stays in view.

Opening the Agent's page restores its conversation and scrolls to its end,
once after the next frame and twice more, at 120 and 400 ms, once the
replies have laid out. A cited figure clicked before those passes ran was
opened on the Analysis page, and the passes then took that page to its foot:
the RMSD card ended above the view (Tests, ubuntu-latest 3.11, `b2ebd2b`,
`test_the_page_shows_them_and_one_opens_its_figure`). The passes now act only
while the conversation is the page shown. The clock is held here so the click
lands before them every time.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("playwright.sync_api")
pytest.importorskip("mdtraj")


def test_the_conversation_does_not_scroll_another_page(tmp_path) -> None:
    import urllib.request

    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_an_analysis_is_drawn_from_its_numbers import _analysis as _drawn
    from tests.test_an_analysis_is_drawn_from_its_numbers import _manifest, _series
    from tests.test_an_answer_cites_what_the_study_recorded import cited_findings
    from tests.test_the_drawing_scripts_run_in_a_browser import FRAMES, _write_study

    root = _write_study(tmp_path / "study")
    _manifest(root, saving_interval_ps=2.0)
    for name in ("rmsd", "rmsf", "rg"):
        _drawn(root, name, _series(FRAMES), {"mean": 0.112, "standard_error": 0.002,
                                             "effective_samples": 11.0, "discard": 5,
                                             "n_frames": FRAMES, "unit": "nm"})
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    said = "The RMSD equilibrated at 0.112 nm."
    entries = [{"role": "user", "text": "did the RMSD equilibrate?"},
               {"role": "agent", "kind": "answer", "text": said,
                "cites": cited_findings(said, root)}]
    request = urllib.request.Request(
        session.url + "/api/agent/conversation", data=json.dumps({"entries": entries}).encode(),
        headers={"Content-Type": "application/json", "Origin": session.url}, method="POST")
    urllib.request.urlopen(request, timeout=10).read()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1280, "height": 500})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#analysis", wait_until="domcontentloaded")
            page.wait_for_selector('.page[data-page="analysis"] .analysis-card[data-analysis="rg"]')
            # Opened again on the conversation with the clock held: it is
            # restored, and its passes to the end are left waiting.
            page.clock.install()
            page.clock.pause_at(1_900_000_000_000)
            page.evaluate("() => window.FastMDXDashboard.navigate('agent')")
            page.reload(wait_until="domcontentloaded")
            page.wait_for_selector('#agent-thread .agent-cite[data-analysis="rmsd"]')
            # Taller than the view whatever has rendered while the clock is
            # held, so a pass that took it to its foot would be seen.
            page.evaluate("() => { const room = document.createElement('div');"
                          " room.style.height = '3000px';"
                          " document.querySelector('.page[data-page=\"analysis\"]')"
                          ".appendChild(room);"
                          " window.FastMDXDashboard.navigate('analysis');"
                          " document.querySelector('.main').scrollTop = 0; }")
            page.clock.run_for(1000)
            scrolled = page.evaluate("() => document.querySelector('.main').scrollTop")
            room = page.evaluate("() => { const m = document.querySelector('.main');"
                                 " return m.scrollHeight - m.clientHeight; }")
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert room > 0, "the Analysis page fits the view, so nothing could move it"
    assert scrolled == 0
