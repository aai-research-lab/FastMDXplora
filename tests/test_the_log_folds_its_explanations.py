"""The Log says what happened, one line each, and folds each explanation
behind the line it explains.

Every stage's explanation and its citation were printed in full in the
narration, in the fixed-width face: the Log of a short run was 420 pixels
of monospaced prose for each line of what happened, on every page.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_the_drawing_scripts_run_in_a_browser import _write_study  # noqa: E402

EVENTS = [
    {"timestamp": "2026-10-05T16:41:00", "level": "explain",
     "message": "An explanation with no line before it, at the top of the window."},
    {"timestamp": "2026-10-05T16:43:34", "level": "info", "message": "Production started"},
    {"timestamp": "2026-10-05T16:43:34", "level": "explain",
     "message": "This is the part that is analysed. Everything before it was getting the "
                "system into a state worth sampling."},
    {"timestamp": "2026-10-05T16:43:34", "level": "info",
     "message": "→ Grossfield et al., Best Practices, LiveCoMS 2018 (doi:10.33011/livecoms.1.1.5067)"},
    {"timestamp": "2026-10-05T16:59:01", "level": "error",
     "message": "simulation.stability.refused: the energy rose past its bound"},
    {"timestamp": "2026-10-05T16:59:01", "level": "info", "message": "Production completed"},
]


SHOWN = {"events": EVENTS}


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    study = _write_study(tmp_path_factory.mktemp("log") / "study")
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        opened = browser.new_page(viewport={"width": 1440, "height": 900})
        opened.set_default_timeout(60000)
        opened.route("**/api/events", lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(SHOWN)))
        opened.goto(session.url + "#overview", wait_until="domcontentloaded")
        # Opened once the page has followed the finished study, which
        # closes the panel.
        opened.wait_for_function("() => window.FastMDXFrame && "
                                 "document.body.classList.contains('state-ready') && "
                                 "document.getElementById('topbar-status-text').textContent !== 'Ready'")
        opened.wait_for_timeout(500)
        opened.evaluate("() => { window.FastMDXFrame.setCollapsed(false); window.FastMDXFrame.showTab('log'); }")
        opened.wait_for_selector("#side-log .side-log-entry")
        yield opened
        browser.close()
    session.server.shutdown()


def test_an_explanation_is_folded_behind_its_line(page) -> None:
    said = page.evaluate("""() => {
        const fold = document.querySelector('#side-log .side-log-entry');
        return {line: fold.querySelector('summary .side-log-text').textContent,
                mark: fold.querySelector('summary .side-log-why-mark').textContent,
                open: fold.open,
                said: [...fold.querySelectorAll('.side-log-block p')].map(p => p.textContent.slice(0, 20)),
                lines: [...document.querySelectorAll('#side-log .side-log-text')].map(t => t.textContent),
                standing: [...document.querySelectorAll('#side-log > .side-log-block')]
                    .map(b => b.dataset.kind)};
    }""")
    assert said == {"line": "Production started", "mark": "why", "open": False,
                    "said": ["This is the part tha", "→ Grossfield et al.,"],
                    "lines": ["Production started", "Production completed"],
                    "standing": ["why", "refused"]}


def test_opened_it_stays_open_as_the_log_is_drawn_again(page) -> None:
    page.click("#side-log .side-log-entry summary")
    assert page.evaluate("() => document.querySelector('#side-log .side-log-entry').open")
    # The panel redraws as events arrive; the one opened is opened again.
    page.click('.side-filter[data-log-filter="refusals"]')
    page.click('.side-filter[data-log-filter="all"]')
    assert page.evaluate("() => document.querySelector('#side-log .side-log-entry').open")


def test_it_stays_open_as_older_events_leave_the_window(page) -> None:
    """The panel is sent the last hundred events: as a run writes, the
    first leave, and an opened explanation stays with its line."""
    page.evaluate("() => { const f = document.querySelector('#side-log .side-log-entry');"
                  " if (!f.open) f.querySelector('summary').click(); }")
    SHOWN["events"] = EVENTS[1:] + [{"timestamp": "2026-10-05T17:00:00", "level": "info",
                                     "message": "Report phase started"}]
    try:
        page.click('.side-filter[data-log-filter="refusals"]')
        page.click('.side-filter[data-log-filter="all"]')
        page.wait_for_function("() => [...document.querySelectorAll('#side-log .side-log-text')]"
                               ".some(t => t.textContent === 'Report phase started')")
        said = page.evaluate("""() => [...document.querySelectorAll('#side-log .side-log-entry')]
            .map(f => [f.querySelector('.side-log-text').textContent, f.open])""")
    finally:
        SHOWN["events"] = EVENTS
    assert said == [["Production started", True]]


def test_under_why_the_explanations_stand_open(page) -> None:
    page.click('.side-filter[data-log-filter="why"]')
    # The whole list again, once the panel has asked for it.
    page.wait_for_function("() => document.querySelectorAll('#side-log > .side-log-block').length === 3")
    kinds = page.evaluate("""() => [...document.querySelectorAll('#side-log > *')]
        .map(e => e.className + ':' + (e.dataset.kind || ''))""")
    page.click('.side-filter[data-log-filter="all"]')
    assert kinds == ["side-log-block:why"] * 3


def test_the_narration_is_in_the_text_face_and_its_times_fixed_width(page) -> None:
    faces = page.evaluate("""() => {
        const face = (sel) => getComputedStyle(document.querySelector(sel)).fontFamily
            .split(',')[0].replace(/["']/g, '').trim();
        return [face('#side-log .side-log-text'), face('#side-log .side-log-ts')];
    }""")
    assert faces == ["Inter", "JetBrains Mono"]
