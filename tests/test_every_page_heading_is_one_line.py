"""Every page's heading is one line, small, as a chat's title is.

The page's name at body size, what the page is beside it, smaller and
muted, cut with an ellipsis rather than wrapped, and the page's actions at
the right in compact controls: one height on every page. On a phone only
the name, with the actions beside it where they fit.
"""

from __future__ import annotations

import pytest

from tests.test_the_workspace_says_its_studies import _study

PAGES = ("studies", "run", "overview", "viewer", "analysis", "report", "files", "agent")

_MEASURED = """() => {
    const header = document.querySelector('.page:not([hidden]) .page-header');
    const lead = header.firstElementChild;
    const title = lead.querySelector('.page-title');
    const rest = [...lead.children].filter((c) => c !== title);
    const actions = header.querySelector(':scope > .page-header-actions');
    const top = (el) => Math.round(el.getBoundingClientRect().top);
    const mid = (el) => { const r = el.getBoundingClientRect(); return (r.top + r.bottom) / 2; };
    return {
        height: Math.round(header.getBoundingClientRect().height),
        size: getComputedStyle(title).fontSize,
        weight: getComputedStyle(title).fontWeight,
        lines: Math.round(lead.getBoundingClientRect().height),
        shown: rest.filter((c) => getComputedStyle(c).display !== 'none').length,
        cut: rest.every((c) => getComputedStyle(c).textOverflow === 'ellipsis'),
        actionsBeside: !actions || !actions.offsetWidth
            || Math.abs(mid(actions) - mid(title)) < 8,
        wider: document.scrollingElement.scrollWidth - innerWidth,
        titleTop: top(title),
    };
}"""


def test_every_page_heading_is_one_line(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    study = _study(tmp_path / "ubiquitin", means={"rmsd": (0.1, 0.01)})
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    wide: dict[str, dict] = {}
    phone: dict[str, dict] = {}
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#overview", wait_until="domcontentloaded")
            page.wait_for_function("() => window.FastMDXDashboard")
            for into, width in ((wide, 1440), (phone, 390)):
                page.set_viewport_size({"width": width, "height": 900})
                for name in PAGES:
                    page.evaluate(f"() => window.FastMDXDashboard.navigate('{name}')")
                    page.wait_for_function(
                        f"() => !document.querySelector('.page[data-page=\"{name}\"]').hidden")
                    page.wait_for_timeout(150)
                    into[name] = page.evaluate(_MEASURED)
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    # One height on every page, the name small and on one line.
    assert len({m["height"] for m in wide.values()}) == 1, wide
    for name, measured in wide.items():
        assert measured["size"] == "14.5px" and measured["weight"] == "600", name
        assert measured["lines"] <= 24, (name, measured)
        assert measured["shown"] >= 1 and measured["cut"], name
        assert measured["actionsBeside"], name
    # On a phone the name alone, no page wider than the screen.
    for name, measured in phone.items():
        assert measured["shown"] == 0 and measured["lines"] <= 24, name
        assert measured["wider"] <= 0, name
