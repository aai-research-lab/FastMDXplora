"""All studies as a table, sorted by any column.

Two cards a row show a study's figure and its means; a workspace of forty
studies is read faster as rows. **Table** lists each study as a row (its
name, system, state as its light, when it began, when it was last
active, its first means and its tags), sorted by a column's head, and is
kept as chosen. A row is tagged as a card is, its tags changed under it.

Since 10-18 the list is what All studies opens on (cards where a person
chose them), its columns the molecule, Study, State, System, Simulated,
Means (every mean in one cell, sorted by the one chosen beside its head)
and Active, and Open, Tag and Files come on hover in a column of their own.
"""

from __future__ import annotations

import json
import os
import time

import pytest

from tests.test_the_workspace_says_its_studies import _study

NAMES = ("() => [...document.querySelectorAll('#studies-table tbody .studies-name-text')]"
         ".map((n) => n.textContent)")


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
            # The list first, the cards not drawn.
            page.wait_for_selector("#studies-table tbody tr.studies-row")
            cards_gone = page.locator("#studies-grid").is_hidden()
            heads = page.eval_on_selector_all(
                "#studies-table thead th", "(all) => all.map((h) => "
                "(h.querySelector('.studies-sort') || h).textContent.trim())")
            page.click('#studies-table thead .studies-sort:text-is("Study")')
            by_name = page.evaluate(NAMES)
            page.click('#studies-table thead .studies-sort:text-is("Means")')
            by_rmsd = page.evaluate(NAMES)
            # Built again, the table keeps the keyboard where it was.
            focused = page.evaluate("() => document.activeElement.dataset.key")
            page.click('#studies-table thead .studies-sort:text-is("Means")')
            by_rmsd_down = page.evaluate(NAMES)
            chooser = page.eval_on_selector_all("#studies-table thead .studies-means-sort option",
                                                "(all) => all.map((o) => o.value)")
            lights = page.eval_on_selector_all(
                "#studies-table tbody .recent-light", "(all) => all.map((l) => l.dataset.state)")
            # Narrowed by the search as the cards are.
            page.fill("#studies-search", "1AKE")
            narrowed = page.evaluate(NAMES)
            page.fill("#studies-search", "")
            page.click('[data-studies-view="cards"]')
            page.reload(wait_until="domcontentloaded")
            page.evaluate(f"() => window.FastMDXStudies.load({str(tmp_path)!r})")
            page.wait_for_selector(".study-card")
            kept_cards = page.get_attribute('[data-studies-view="cards"]', "aria-pressed")
            page.click('[data-studies-view="table"]')
            page.reload(wait_until="domcontentloaded")
            page.evaluate(f"() => window.FastMDXStudies.load({str(tmp_path)!r})")
            page.wait_for_selector("#studies-table tbody tr.studies-row")
            kept = page.get_attribute('[data-studies-view="table"]', "aria-pressed")
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert cards_gone
    assert heads[2:8] == ["Study", "State", "System", "Simulated", "Means", "Active"]
    assert by_name == ["failed", "large", "small"]
    # Smallest first, then largest first; the study with none last either way.
    assert by_rmsd == ["small", "large", "failed"]
    assert focused == "mean:RMSD"
    assert by_rmsd_down == ["large", "small", "failed"]
    assert chooser == ["RMSD"]
    assert "failed" in lights
    assert narrowed == ["large"]
    assert kept_cards == "true" and kept == "true"


def test_a_row_is_tagged_and_says_when_it_was_last_active(tmp_path) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session
    from fastmdxplora.gui.workspace import card_of

    _study(tmp_path / "old", system="1UAO", started="2026-09-03T10:00:00+00:00")
    _study(tmp_path / "touched", system="1AKE", started="2026-09-01T10:00:00+00:00")
    long_ago = time.time() - 30 * 86400
    for path in tmp_path.rglob("*"):
        os.utime(path, (long_ago, long_ago))
    # Begun first and run as long ago, but looked at since: a view kept now.
    (tmp_path / "touched" / "viewer_views.json").write_text(json.dumps({"views": []}))
    assert card_of(tmp_path / "touched")["last_active"] > card_of(tmp_path / "old")["last_active"]

    session = start_dashboard_session(output=str(tmp_path / "old"), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.set_default_timeout(60000)
            page.goto(session.url + "#studies", wait_until="domcontentloaded")
            page.evaluate(f"() => window.FastMDXStudies.load({str(tmp_path)!r})")
            page.wait_for_selector("#studies-table tbody tr.studies-row")
            # Most recently active first, as the list opens.
            by_activity = page.evaluate(NAMES)
            said = page.eval_on_selector_all(
                "#studies-table tbody tr.studies-row td.studies-active",
                "(all) => all.map((c) => c.textContent)")
            row = '#studies-table tr.studies-row[data-path$="touched"]'
            page.hover(row)
            page.click(row + " .study-tag-edit")
            page.fill("#studies-table .studies-editing .study-tag-input", "wild type")
            page.click("#studies-table .studies-editing button[type=submit]")
            page.wait_for_selector(row + " .studies-row-tag")
            tags = page.eval_on_selector_all(row + " .studies-row-tag",
                                             "(all) => all.map((t) => t.textContent)")
            editing = page.locator("#studies-table .studies-editing").count()
            browser.close()
    finally:
        session.server.shutdown()
    assert by_activity == ["touched", "old"]
    assert said[0] in ("Just now", "1 min ago") and said[1] != said[0]
    assert tags == ["wild type"] and editing == 0


def test_a_mean_not_determined_is_not_cut_under_open_and_tag(tmp_path) -> None:
    """"0.06237 nm, not determined" on one line ran under the pinned Open
    and Tag, its words cut, and nothing showed the table went on beneath.
    Now Open and Tag have their own column, and a mean not determined is a
    ring before its value, why in its title and in every mean's list."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    study = _study(tmp_path / "thin", system="1L2Y", means={"rmsd": (0.06237, None)})
    record = json.loads((study / "analysis" / "rmsd" / "options.json").read_text())
    record["findings"]["mean"]["not_a_measurement"] = "Too short against its correlation."
    (study / "analysis" / "rmsd" / "options.json").write_text(json.dumps(record))
    for name in ("rg", "sasa", "hbonds", "qvalue"):
        folder = study / "analysis" / name
        folder.mkdir(parents=True)
        (folder / "options.json").write_text(json.dumps({"analysis": name, "findings": {
            "mean": {"mean": 1.0, "standard_error": 0.01, "unit": "nm",
                     "effective_samples": 40}}}))
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1100, "height": 900})
            page.set_default_timeout(60000)
            page.goto(session.url + "#studies", wait_until="domcontentloaded")
            # Settled: a list read again under the pointer is a row not
            # hovered yet.
            page.wait_for_selector("body.state-ready")
            page.evaluate(f"() => window.FastMDXStudies.load({str(tmp_path)!r})")
            row = "#studies-table tbody tr.studies-row"
            page.wait_for_selector(row)
            page.hover(row)
            page.wait_for_function("() => getComputedStyle(document.querySelector("
                                   "'.studies-row .studies-actions')).opacity === '1'")
            cell = page.eval_on_selector(row, """(r) => {
                const item = r.querySelector('.studies-mean-item');
                const means = r.querySelector('.studies-means-cell').getBoundingClientRect();
                const acts = r.querySelector('.studies-actions').getBoundingClientRect();
                const c = r.querySelector('.studies-means-cell');
                return {value: item.querySelector('.studies-mean-v').textContent,
                        determined: item.dataset.determined, title: item.title,
                        more: (r.querySelector('.studies-means-more') || {}).textContent,
                        fits: c.scrollWidth <= c.clientWidth + 1,
                        apart: acts.left >= means.right - 1,
                        shown: getComputedStyle(r.querySelector('.studies-actions')).opacity};
            }""")
            more = page.get_attribute("#studies-table-wrap", "data-more")
            browser.close()
    finally:
        session.server.shutdown()
    assert cell["value"] == "0.06237 nm" and cell["determined"] == "false"
    assert cell["title"].startswith("Not determined: Too short against its correlation.")
    assert cell["more"] == "+3"
    assert cell["fits"] and cell["apart"] and cell["shown"] == "1"
    # The whole row fits: nothing hidden to the right.
    assert more == ""


def test_a_folder_asked_for_while_another_is_read_is_the_one_listed(tmp_path) -> None:
    """All studies opened on its page reads the workspace; another folder
    asked for meanwhile was answered with the workspace's list."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    _study(tmp_path / "here" / "near", system="1UAO")
    _study(tmp_path / "there" / "far", system="1AKE")
    session = start_dashboard_session(output=str(tmp_path / "here" / "near"),
                                      host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#studies", wait_until="domcontentloaded")
            page.wait_for_selector("#studies-table tbody tr.studies-row")
            names = page.evaluate(f"""async () => {{
                window.FastMDXStudies.load();
                await window.FastMDXStudies.load({str(tmp_path / "there")!r});
                return ({NAMES})();
            }}""")
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert names == ["far"]
