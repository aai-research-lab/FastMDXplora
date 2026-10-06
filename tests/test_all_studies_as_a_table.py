"""All studies as a table, sorted by any column.

Two cards a row show a study's figure and its means; a workspace of forty
studies is read faster as rows. **Table** lists each study as a row (its
name, system, state as its light, when it began, its first means and its
tags), sorted by a column's head, and is kept as chosen.
"""

from __future__ import annotations

import pytest

from tests.test_the_workspace_says_its_studies import _study


def test_rows_sorted_by_a_column_and_kept(tmp_path) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    _study(tmp_path / "small", system="1UAO", means={"rmsd": (0.05, 0.01)},
           started="2026-09-03T10:00:00+00:00")
    _study(tmp_path / "large", system="1AKE", means={"rmsd": (0.30, 0.02)},
           started="2026-09-01T10:00:00+00:00")
    _study(tmp_path / "failed", system="1L2Y", failed="simulation.nan",
           started="2026-09-02T10:00:00+00:00")
    session = start_dashboard_session(output=str(tmp_path / "small"), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#studies", wait_until="domcontentloaded")
            page.evaluate(f"() => window.FastMDXStudies.load({str(tmp_path)!r})")
            page.wait_for_selector(".study-card")
            page.click('[data-studies-view="table"]')
            page.wait_for_selector("#studies-table tbody tr")
            cards_gone = page.locator("#studies-grid").is_hidden()

            def names():
                return page.eval_on_selector_all(
                    "#studies-table tbody .studies-name-text", "(all) => all.map((n) => n.textContent)")

            newest_first = names()
            heads = page.eval_on_selector_all("#studies-table thead th",
                                              "(all) => all.map((h) => h.textContent.trim())")
            page.click('#studies-table thead button:text-is("RMSD")')
            by_rmsd = names()
            # Built again, the table keeps the keyboard where it was.
            focused = page.evaluate("() => document.activeElement.dataset.key")
            page.click('#studies-table thead button:text-is("RMSD")')
            by_rmsd_down = names()
            lights = page.eval_on_selector_all(
                "#studies-table tbody .recent-light", "(all) => all.map((l) => l.dataset.state)")
            # Narrowed by the search as the cards are.
            page.fill("#studies-search", "1AKE")
            narrowed = names()
            page.fill("#studies-search", "")
            page.reload(wait_until="domcontentloaded")
            page.evaluate(f"() => window.FastMDXStudies.load({str(tmp_path)!r})")
            page.wait_for_selector("#studies-table tbody tr")
            kept = page.get_attribute('[data-studies-view="table"]', "aria-pressed")
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert cards_gone
    assert newest_first == ["small", "failed", "large"]
    assert heads[1:5] == ["Study", "System", "State", "Started"] and "RMSD" in heads
    # Smallest first, then largest first; the study with none last either way.
    assert by_rmsd == ["small", "large", "failed"]
    assert focused and focused.startswith("rmsd")
    assert by_rmsd_down == ["large", "small", "failed"]
    assert "failed" in lights
    assert narrowed == ["large"]
    assert kept == "true"
