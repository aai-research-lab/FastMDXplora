"""A button whose icon says what it does is that icon, named on hover.

Asked for (10-06): Close, Cards and Table, and Clear selection as line
icons; each Close in its dialog's top right corner; Clear selection beside
the sequence's words, not on a row of its own; a magnifier inside each
search field, on the left.

And (10-07): Look in another folder and Open a shared study as line icons,
"so there should be enough space" for the header on one line.
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
            # Both side columns open, the narrowest the centre column is.
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            context.add_init_script(
                "try { localStorage.setItem('fmx.panelCollapsed', '0'); } catch (e) {}")
            page = context.new_page()
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
            folders = page.evaluate("""() => ['studies-look-in', 'studies-open-shared'].map(id => {
                const b = document.getElementById(id);
                return [b.getAttribute('aria-label'), b.title, !!b.querySelector('svg.line-icon'),
                        b.textContent.trim()];
            })""")
            page.click("#studies-open-shared")
            pressed = page.get_attribute("#studies-open-shared", "aria-expanded")
            page.click("#studies-open-shared")
            # The header's actions beside the name, the search field not over
            # the Cards and Table buttons (1469: it ran 64 px over them), none
            # over the name or out of the band (as words, Look in another
            # folder and Open a shared study ran over the name): on one line
            # where they fit, in rows within their room where they do not.
            def laid_out():
                return page.evaluate("""() => {
                    const head = document.querySelector('section[data-page="studies"] .page-header');
                    const band = head.getBoundingClientRect();
                    const name = head.querySelector('.page-title').getBoundingClientRect();
                    const field = document.getElementById('studies-search').getBoundingClientRect();
                    const views = document.querySelector('.studies-view').getBoundingClientRect();
                    const all = [...head.querySelectorAll('.page-header-actions > *')]
                        .filter(e => e.offsetParent).map(e => e.getBoundingClientRect());
                    const over = (a, b) => a.left < b.right && a.right > b.left
                        && a.top < b.bottom && a.bottom > b.top;
                    return {clear: all.every(r => !over(r, name)),
                            inside: all.every(r => r.left >= band.left && r.right <= band.right + 0.5),
                            apart: !over(field, views),
                            // A row is the items that overlap it top to bottom.
                            rows: all.filter((r, i) => !all.slice(0, i).some(
                                q => r.top < q.bottom && r.bottom > q.top)).length};
                }""")
            row = {}
            for width in (1440, 1200, 1150, 1100, 1000):
                page.set_viewport_size({"width": width, "height": 900})
                page.wait_for_timeout(300)
                row[width] = laid_out()
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
            # New study's header (Reset, and its draft said) on one row at
            # 900 px: a second row slid its steps bar, held under the top
            # bar, under the header (the actions held to 70% of the band).
            page.set_viewport_size({"width": 900, "height": 900})
            page.evaluate("() => window.FastMDXDashboard.navigate('run')")
            page.evaluate("() => { document.getElementById('run-draft').hidden = false; }")
            page.wait_for_timeout(300)
            draft = page.evaluate("""() => {
                const head = document.querySelector('section[data-page="run"] .page-header');
                const all = [...head.querySelectorAll('.page-header-actions > *')]
                    .filter(e => e.offsetParent).map(e => e.getBoundingClientRect());
                return [all.length, all.filter((r, i) => !all.slice(0, i).some(
                    q => r.top < q.bottom && r.bottom > q.top)).length];
            }""")
            context.close()
            browser.close()
    finally:
        session.server.shutdown()
    for dialog, said in corners.items():
        assert said == {"icon": True, "words": "", "name": "Close", "right": True, "top": True,
                        "closed": True}, dialog
    assert views == [["Cards", True, ""], ["Table", True, ""]]
    assert folders == [["Look in another folder", "Look in another folder", True, ""],
                       ["Open a shared study", "Open a shared study", True, ""]]
    assert pressed == "true"
    assert row[1440] == {"clear": True, "inside": True, "apart": True, "rows": 1}
    # The field starts at its least width, so one row holds them while that
    # fits, and grows where there is room.
    assert row[1200] == {"clear": True, "inside": True, "apart": True, "rows": 1}
    # The actions take the room the subtitle gives up before they wrap.
    assert row[1150] == {"clear": True, "inside": True, "apart": True, "rows": 1}
    assert draft == [3, 1]
    for width in (1100, 1000):
        assert {k: v for k, v in row[width].items() if k != "rows"} == {
            "clear": True, "inside": True, "apart": True}, (width, row[width])
    assert search
    # Shown once residues are chosen (test_the_sequence_is_above_the_molecule).
    assert clear == {"inHead": True, "hidden": True, "icon": True, "name": "Clear selection"}
