"""The three columns' top bars are one height, their rules on one line.

Asked for (10-05): each page's title bar the height of the side panel's
(Log and Files), its title centred in it; the sidebar folded by a button
like the side panel's; the product's name alone at the top of the sidebar,
without the lab's logo. The title bar was 63 pixels against the tabs' 52,
its title 11 pixels below the bar's middle.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from tests.test_the_workspace_says_its_studies import _study

PAGES = ("studies", "run", "overview", "viewer", "analysis", "report", "files", "agent")

_BARS = """() => {
    const box = (s) => document.querySelector(s).getBoundingClientRect();
    const header = box('.page:not([hidden]) .page-header');
    const title = box('.page:not([hidden]) .page-title');
    const tabs = box('.side-panel-tabs');
    const brand = box('.sidebar-brand');
    return {
        header: [header.top, header.bottom], tabs: [tabs.top, tabs.bottom],
        brand: [brand.top, brand.bottom],
        offCentre: (title.top + title.bottom) / 2 - (header.top + header.bottom) / 2,
    };
}"""


def test_the_three_bars_are_one_height_on_every_page(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    study = _study(tmp_path / "ubiquitin", means={"rmsd": (0.1, 0.01)})
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    found: dict[str, dict] = {}
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            context = browser.new_context(viewport={"width": 1680, "height": 900})
            context.add_init_script("localStorage.setItem('fmx.panelCollapsed', '0');"
                                    "localStorage.setItem('fmx.sidebarCollapsed', '0');")
            page = context.new_page()
            page.set_default_timeout(60000)
            page.goto(session.url + "#overview", wait_until="domcontentloaded")
            page.wait_for_function("() => window.FastMDXDashboard")
            page.wait_for_selector("body:not(.state-loading)")
            for name in PAGES:
                page.evaluate(f"() => window.FastMDXDashboard.navigate('{name}')")
                page.wait_for_function(
                    f"() => !document.querySelector('.page[data-page=\"{name}\"]').hidden")
                # The Viewer folds both columns; shown again, as on any page.
                page.evaluate("() => document.body.classList.remove("
                              "'sidebar-collapsed', 'panel-collapsed')")
                page.wait_for_timeout(150)
                found[name] = page.evaluate(_BARS)
            collapse = page.evaluate("""() => {
                const a = document.getElementById('sidebar-collapse');
                const b = document.getElementById('side-collapse');
                return [a.className, a.textContent, b.className,
                        a.getBoundingClientRect().height, b.getBoundingClientRect().height];
            }""")
            brand = page.inner_html(".sidebar-brand")
            browser.close()
    finally:
        session.server.shutdown()
    for name, bars in found.items():
        assert bars["header"] == bars["tabs"] == bars["brand"], (name, bars)
        assert bars["header"][1] - bars["header"][0] == 52, (name, bars)
        assert abs(bars["offCentre"]) <= 1, (name, bars)
    # Folded by a button like the side panel's, the name alone above it.
    assert collapse[0] == "ghost-btn sidebar-collapse" and collapse[1] == "❮"
    assert "ghost-btn side-collapse" in collapse[2] and collapse[3] == collapse[4]
    assert "<img" not in brand and "<svg" not in brand


def test_the_standalone_dashboard_has_the_name_alone(tmp_path):
    from fastmdxplora.gui.report_dashboard import build_dashboard

    (tmp_path / "manifest.json").write_text(json.dumps({"system": "1L2Y", "phases": []}),
                                            encoding="utf-8")
    build_dashboard(orchestrator=SimpleNamespace(output_dir=tmp_path, system="1L2Y"),
                    output_dir=tmp_path / "report", title="A study")
    html = (tmp_path / "report" / "dashboard.html").read_text(encoding="utf-8")
    top = html[html.index('<div class="sidebar-brand">'):html.index('<div class="sidebar-study">')]
    assert "brand-product" in top and "<img" not in top
    # The lab's mark is still the avatar at the foot.
    assert 'class="sidebar-account-logo"' in html
