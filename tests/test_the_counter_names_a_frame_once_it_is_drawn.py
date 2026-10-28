"""The counter names a frame opened from a chart only once it is drawn.

Frames 0, 5 and 10 of eleven are played; frame 7, opened from a chart point
or a saved view, is read from the trajectory and put among them, so the
counter's total goes from 3 to 4. Every line the counter shows from the
page's first load on is recorded: none gives the frame shown before (or any
frame but 7) against the new total, on a page that opens it first and on a
fresh page that opens it from a saved view.
"""

from __future__ import annotations

import re

import pytest

pytest.importorskip("mdtraj")

VIEWER = "window.FastMDXMoleculeViewer"

#: Each text the counter line shows, recorded as it changes from the start.
RECORD = """
window.__counterSaid = [];
document.addEventListener("DOMContentLoaded", () => {
  const line = document.getElementById("traj-current")?.closest(".traj-time");
  if (!line) return;
  const note = () => {
    const said = line.textContent.replace(/\\s+/g, " ").trim();
    const all = window.__counterSaid;
    if (all[all.length - 1] !== said) all.push(said);
  };
  new MutationObserver(note).observe(line, {subtree: true, childList: true,
                                            characterData: true});
  note();
});
try { localStorage.setItem('fmx.viewerPlaybackOpen', '1'); } catch (e) {}
"""

LINE = re.compile(r"^(\S+) / (\d+) · (.*?) · ")


def _against_the_new_total(said: list[str]) -> list[str]:
    return [line for line in said if (found := LINE.match(line)) and found[2] == "4"]


def _shown_once_drawn(said: list[str]) -> None:
    newer = _against_the_new_total(said)
    assert newer, said
    wrong = [line for line in newer if "trajectory frame 7 ·" not in line + " ·"]
    assert wrong == [], said
    assert newer[-1].startswith("3 / 4 · trajectory frame 7"), said


def _opened(page) -> None:
    page.wait_for_function(f"() => {VIEWER}?.STATE.exactFrame?.frame === 7")
    page.wait_for_function("() => document.getElementById('traj-current').textContent === '3'"
                           " && document.getElementById('traj-total').textContent === '4'")


def test_a_frame_opened_is_named_once_it_is_drawn_on_a_fresh_page_and_from_a_saved_view(
        tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import DashboardConfig, start_dashboard_session
    from tests.test_a_graph_opens_exact_source_frame import _click_chart, _series, _study

    root = _study(tmp_path / "study")
    _series(root)
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0,
                                     config=DashboardConfig(max_browser_frames=3))
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            context.add_init_script(RECORD)
            page = context.new_page()
            page.set_default_timeout(90000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#analysis", wait_until="domcontentloaded")
            if not page.evaluate("() => !!document.createElement('canvas').getContext('webgl')"):
                pytest.skip("this browser has no WebGL, so the viewer cannot render")
            svg = page.locator('.series-chart[data-analysis="rmsd"] svg')
            svg.wait_for(state="visible")
            # Opened from a chart point on a page that has played nothing yet.
            with page.expect_request("**/api/exact-frame?frame=7*"):
                _click_chart(svg, 0.7)
            _opened(page)
            from_the_chart = page.evaluate("() => window.__counterSaid.slice()")
            saved = page.evaluate("""async () => {
              const response = await fetch('/api/views', {method: 'POST',
                headers: {'content-type': 'application/json'}, body: JSON.stringify({
                  action: 'save', name: 'Frame 7', view: window.FastMDXMoleculeViewer.viewNow()})});
              return response.json();
            }""")
            view = next(view for view in saved["views"] if view["name"] == "Frame 7")
            assert view.get("frame_source") is True

            # A fresh page, its frames played first, then the saved view.
            fresh = context.new_page()
            fresh.set_default_timeout(90000)
            fresh.on("pageerror", lambda error: errors.append(str(error)))
            fresh.goto(session.url + "#viewer", wait_until="domcontentloaded")
            fresh.wait_for_function(f"() => !!{VIEWER}")
            assert fresh.evaluate(f"async () => !!(await {VIEWER}.movie.frames())")
            assert fresh.evaluate(f"(view) => {VIEWER}.showView(view)", view) is True
            _opened(fresh)
            from_a_view = fresh.evaluate("() => window.__counterSaid.slice()")
            browser.close()
    finally:
        session.server.shutdown()
    _shown_once_drawn(from_the_chart)
    _shown_once_drawn(from_a_view)
    assert errors == []
