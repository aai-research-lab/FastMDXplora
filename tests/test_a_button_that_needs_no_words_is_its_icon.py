"""A button whose icon says what it does is that icon, named on hover.

Asked for (10-06): Close, Cards and Table, and Clear selection as line
icons; each Close in its dialog's top right corner; Clear selection beside
the sequence's words, not on a row of its own; a magnifier inside each
search field, on the left.
"""

from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_the_drawing_scripts_run_in_a_browser import _write_study  # noqa: E402

_CORNER = """(id) => {
    const panel = document.querySelector('#' + id + ' .agent-dialog-panel').getBoundingClientRect();
    const x = document.querySelector('#' + id + ' .dialog-x');
    const box = x.getBoundingClientRect();
    return {icon: !!x.querySelector('svg.line-icon'), words: x.textContent.trim(),
            name: x.getAttribute('aria-label'),
            right: panel.right - box.right < 24, top: box.top - panel.top < 24};
}"""


def test_the_buttons_are_their_icons(tmp_path) -> None:
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    study = _write_study(tmp_path / "study")
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.set_default_timeout(60000)
            page.goto(session.url + "#studies", wait_until="domcontentloaded")
            page.wait_for_function("() => window.FastMDXDialog")
            corners = {}
            for dialog in ("prefs-dialog", "cite-dialog", "viewer-help", "agent-settings"):
                page.evaluate(f"() => window.FastMDXDialog.open('{dialog}')")
                corners[dialog] = page.evaluate(_CORNER, dialog)
                page.click(f"#{dialog} .dialog-x")
                corners[dialog]["closed"] = page.evaluate(
                    f"() => document.getElementById('{dialog}').hidden")
            page.wait_for_selector("#studies-search", state="visible")
            views = page.evaluate("""() => [...document.querySelectorAll('[data-studies-view]')]
                .map(b => [b.getAttribute('aria-label'), !!b.querySelector('svg'), b.textContent.trim()])""")
            search = page.evaluate("""() => {
                const field = document.getElementById('studies-search').getBoundingClientRect();
                const glass = document.getElementById('studies-search').parentNode
                    .querySelector('.search-icon').getBoundingClientRect();
                return glass.left > field.left && glass.right < field.left + 30
                    && glass.top > field.top && glass.bottom < field.bottom;
            }""")
            clear = page.evaluate("""() => {
                const b = document.getElementById('seq-clear');
                return {inHead: !!b.closest('summary.seq-head'), hidden: b.hidden,
                        icon: !!b.querySelector('svg'), name: b.getAttribute('aria-label')};
            }""")
            browser.close()
    finally:
        session.server.shutdown()
    for dialog, said in corners.items():
        assert said == {"icon": True, "words": "", "name": "Close", "right": True, "top": True,
                        "closed": True}, dialog
    assert views == [["Cards", True, ""], ["Table", True, ""]]
    assert search
    # Shown once residues are chosen (test_the_sequence_is_above_the_molecule).
    assert clear == {"inHead": True, "hidden": True, "icon": True, "name": "Clear selection"}
