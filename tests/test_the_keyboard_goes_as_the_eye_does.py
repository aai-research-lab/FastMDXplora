"""The keyboard goes through the page as the eye does.

The study menu took no arrow keys; 13 to 24 Tab stops came before the page
(no way past the sidebar); and the Overview's blocks were put in their
order by CSS, so Tab went through them in another.
"""

from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_the_overview_leads_with_what_was_determined import (  # noqa: E402
    _browser_study, _open)


def test_the_study_menu_takes_the_arrow_keys(tmp_path):
    def check(page):
        page.click("#study-card")
        page.wait_for_function("() => !document.getElementById('study-menu').hidden")
        items = page.eval_on_selector_all(
            "#study-menu .study-menu-item",
            "(all) => all.filter((i) => !i.hidden && i.offsetParent).map((i) => i.textContent.trim())")
        seen = []
        focused = "() => document.activeElement.textContent.trim()"
        for key in ("ArrowDown", "End", "ArrowDown", "ArrowUp", "Home", "ArrowUp"):
            page.keyboard.press(key)
            seen.append(page.evaluate(focused))
        page.keyboard.press("Tab")
        closed = page.is_hidden("#study-menu")
        return items, seen, closed

    (items, seen, closed), errors = _open(_browser_study(tmp_path / "study", "completed"), check)
    assert not errors, errors
    assert len(items) >= 3
    assert seen == [items[1], items[-1], items[0], items[-1], items[0], items[-1]]
    assert closed


def test_the_first_tab_stop_skips_to_the_page(tmp_path):
    def check(page):
        page.evaluate("() => document.activeElement && document.activeElement.blur()")
        page.keyboard.press("Tab")
        first = page.evaluate("() => [document.activeElement.textContent.trim(), "
                              "document.activeElement.getBoundingClientRect().top >= 0]")
        page.keyboard.press("Enter")
        landed = page.evaluate("() => [document.activeElement.id, location.hash]")
        page.keyboard.press("Tab")
        inside = page.evaluate(
            "() => !!document.activeElement.closest('#page-shell')")
        return first, landed, inside

    (first, landed, inside), errors = _open(_browser_study(tmp_path / "study", "completed"), check)
    assert not errors, errors
    assert first == ["Skip to the page", True]
    assert landed == ["page-shell", "#overview"]
    assert inside


@pytest.mark.parametrize("status, order", [
    ("completed", ["overview-results", "overview-run", "overview-charts"]),
    ("running", ["overview-charts", "overview-run", "overview-results"]),
])
def test_the_overview_is_in_its_reading_order(tmp_path, status, order):
    def check(page):
        page.wait_for_function(
            f"() => document.getElementById('overview-body').dataset.lead === "
            f"'{'results' if status == 'completed' else 'health'}'")
        return page.evaluate("""(ids) => {
          const all = [...document.querySelectorAll('#overview-body [id]')].map((e) => e.id);
          const tops = ids.map((id) => document.getElementById(id).getBoundingClientRect().top);
          return [ids.map((id) => all.indexOf(id)), tops]; }""", order)

    (places, tops), errors = _open(_browser_study(tmp_path / "study", status), check)
    assert not errors, errors
    assert places == sorted(places), places
    visible = [top for top in tops if top]
    assert visible == sorted(visible)
