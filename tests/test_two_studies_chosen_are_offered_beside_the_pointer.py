"""Two studies chosen are offered Compare and Clear beside the pointer.

The Compare and Clear icons were at the top of All studies, above the
cards: a second study chosen far down the page was compared only after
scrolling back to the top for them. User, 10-09: "compare and clear icons
on the studies page should pop up beside the cursor only when 2 studies are
selected .... not that the user has to scroll all the way back to the top
of the page to find the buttons". They come beside the pointer that chose
the second (beside the box, chosen from the keyboard), only while two are
chosen, and Compare brings the comparison into view.
"""

from __future__ import annotations

import pytest

from tests.test_the_workspace_says_its_studies import _study

pytest.importorskip("playwright.sync_api")


@pytest.fixture
def many(tmp_path):
    for k in range(12):
        _study(tmp_path / f"study{k:02d}", duration=10 + k,
               means={"rmsd": (0.1 + 0.01 * k, 0.004)},
               started=f"2026-09-{k + 1:02d}T10:00:00+00:00")
    return tmp_path


def _bar(page):
    return page.evaluate("""() => {
      const bar = document.getElementById('studies-compare-bar');
      const r = bar.getBoundingClientRect();
      return {hidden: bar.hidden || r.width === 0, left: r.left, top: r.top,
              right: r.right, bottom: r.bottom,
              inView: r.left >= 0 && r.top >= 0 && r.right <= innerWidth && r.bottom <= innerHeight,
              compare: !document.getElementById('studies-compare').disabled,
              said: document.getElementById('studies-chosen-said').textContent};
    }""")


def _box(page, name):
    return page.locator(f'.study-card[data-path$="/{name}"] .study-pick input')


def _open(many):
    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(many / "study00"), host="127.0.0.1", port=0)
    return session


def _page(pw, session, many, width=1400, height=800):
    browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
    page = browser.new_page(viewport={"width": width, "height": height})
    page.set_default_timeout(60000)
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(session.url + "#studies", wait_until="domcontentloaded")
    page.evaluate(f"() => window.FastMDXStudies.load({str(many)!r})")
    page.wait_for_selector(".study-card")
    # The cards are drawn again as their series arrive: wait for the last.
    page.wait_for_load_state("networkidle")
    return browser, page, errors


def _near(bar, x, y, within=60):
    return abs(bar["left"] - x) < within and abs(bar["top"] - y) < within


def test_compare_and_clear_come_beside_the_pointer_only_for_two(many) -> None:
    from playwright.sync_api import sync_playwright

    session = _open(many)
    try:
        with sync_playwright() as pw:
            browser, page, errors = _page(pw, session, many)
            # Far down the page: the oldest two.
            first, second = _box(page, "study01"), _box(page, "study00")
            second.scroll_into_view_if_needed()
            first.click()
            one = _bar(page)
            heard_one = page.text_content("#studies-chosen-heard")
            box = second.bounding_box()
            pointer = (box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            page.mouse.click(*pointer)
            page.wait_for_selector("#studies-compare-bar:not([hidden])")
            two = _bar(page)
            heard_two = page.text_content("#studies-chosen-heard")
            # Whichever column scrolls, it has: the cards chosen are far down.
            scrolled = page.evaluate(
                "() => Math.max(...[...document.querySelectorAll('*')].map(e => e.scrollTop))")
            page.click("#studies-compare")
            page.wait_for_selector(".studies-means")
            page.wait_for_timeout(1200)
            # Brought into view below the page's sticky bars, not under them.
            compared = page.evaluate("""() => {
              const host = document.getElementById('studies-compared');
              const r = host.getBoundingClientRect();
              const hit = document.elementFromPoint(r.left + 20, r.top + 10);
              return r.top >= 0 && r.top < innerHeight && host.contains(hit); }""")
            # The page moved: the bar is still in view, inside the column.
            after = _bar(page)
            # One taken away: the offer goes with it.
            second.uncheck()
            gone = _bar(page)
            browser.close()
    finally:
        session.server.shutdown()
    assert one["hidden"] and one["said"] == ""
    assert heard_one == "study01 chosen. Choose one more to compare."
    assert not two["hidden"] and two["inView"] and two["compare"] and two["said"] == "Two chosen."
    assert heard_two == "study01 and study00 chosen. Compare and Clear are next in the Tab order."
    assert scrolled > 0
    assert _near(two, *pointer), (two, pointer)
    assert compared
    assert after["inView"] and not after["hidden"]
    assert gone["hidden"]
    assert errors == []


def test_chosen_from_the_keyboard_it_comes_beside_the_box_and_tab_reaches_it(many) -> None:
    """A press elsewhere a moment before the key is not where it was chosen.
    The choice leaves the focus on the box (a second Space unticks it, and
    does not compare); Tab from it reaches Compare."""
    from playwright.sync_api import sync_playwright

    session = _open(many)
    try:
        with sync_playwright() as pw:
            browser, page, errors = _page(pw, session, many)
            first, second = _box(page, "study03"), _box(page, "study00")
            second.scroll_into_view_if_needed()
            first.scroll_into_view_if_needed()
            first.click()
            second.focus()
            page.keyboard.press("Space")
            page.wait_for_selector("#studies-compare-bar:not([hidden])")
            keyed = _bar(page)
            near = second.bounding_box()
            stays = page.evaluate("() => document.activeElement.dataset.path || ''")
            page.keyboard.press("Tab")
            focused = page.evaluate("() => document.activeElement.id")
            page.keyboard.press("Escape")
            back = page.evaluate("() => document.activeElement.dataset.path || ''")
            # Compared from the keyboard, read, and closed: back at the box.
            page.keyboard.press("Tab")
            page.keyboard.press("Enter")
            page.wait_for_selector(".studies-means")
            page.wait_for_timeout(1200)
            reading = page.evaluate("() => document.activeElement.id")
            page.keyboard.press("Tab")
            page.keyboard.press("Enter")
            page.wait_for_timeout(600)
            closed = page.evaluate("""() => {
              const a = document.activeElement, r = a.getBoundingClientRect();
              return (a.dataset.path || '') + (r.top >= 0 && r.bottom <= innerHeight + 1 ? '' : ' unseen'); }""")
            # Cleared from the keyboard with the study scrolled away: it is
            # brought back into view with the focus.
            page.evaluate("""() => { window.scrollTo(0, 0);
              document.querySelectorAll('*').forEach(n => { n.scrollTop = 0; }); }""")
            page.wait_for_timeout(200)
            page.focus("#studies-clear")
            page.keyboard.press("Enter")
            page.wait_for_timeout(600)
            cleared = _bar(page)
            heard = page.text_content("#studies-chosen-heard")
            refocused = page.evaluate("""() => {
              const a = document.activeElement, r = a.getBoundingClientRect();
              return (a.dataset.path || '') + (r.top >= 0 && r.bottom <= innerHeight + 1 ? '' : ' unseen'); }""")
            ticked = page.locator(".study-pick input:checked").count()
            browser.close()
    finally:
        session.server.shutdown()
    assert not keyed["hidden"] and keyed["inView"]
    assert _near(keyed, near["x"] + near["width"], near["y"] + near["height"]), (keyed, near)
    assert stays.endswith("/study00")
    assert focused == "studies-compare"
    assert back.endswith("study00")
    assert reading == "studies-compared" and closed.endswith("/study00"), (reading, closed)
    assert cleared["hidden"] and ticked == 0 and heard == "Cleared."
    assert refocused.endswith("/study00"), refocused
    assert errors == []


def _beside(page, name):
    """The bar beside the study's box: the gap between them, in pixels."""
    return page.evaluate("""(name) => {
      const box = [...document.querySelectorAll('.study-pick input, #studies-table tbody input')]
        .find(b => (b.dataset.path || '').endsWith('/' + name));
      const r = box.getBoundingClientRect();
      const bar = document.getElementById('studies-compare-bar').getBoundingClientRect();
      const main = document.querySelector('.main').getBoundingClientRect();
      const dx = Math.max(bar.left - r.right, r.left - bar.right, 0);
      const dy = Math.max(bar.top - r.bottom, r.top - bar.bottom, 0);
      return {gap: Math.hypot(dx, dy), insideColumn: bar.left >= main.left && bar.right <= main.right,
              shown: bar.width > 0};
    }""", name)


def test_it_stays_beside_the_second_as_the_page_changes_around_it(many) -> None:
    """The point it was chosen at is kept for that drawing only: the Agent
    opened beside the page, a tag edited, a search or the comparison drawn
    move the cards, and the bar goes with the second study chosen."""
    from playwright.sync_api import sync_playwright

    session = _open(many)
    try:
        with sync_playwright() as pw:
            browser, page, errors = _page(pw, session, many)
            _box(page, "study07").click()
            _box(page, "study06").click()
            page.wait_for_selector("#studies-compare-bar:not([hidden])")
            chosen = _beside(page, "study06")
            page.evaluate("() => window.FastMDXAgentBeside.open()")
            page.wait_for_timeout(800)
            drawer = _beside(page, "study06")
            page.evaluate("() => window.FastMDXAgentBeside.close()")
            page.wait_for_timeout(800)
            page.locator(
                         '.study-card[data-path$="/study06"] .study-tag-edit').first.click()
            page.wait_for_timeout(400)
            tagged = _beside(page, "study06")
            editor = page.evaluate(_OVER, ".study-tag-editor")
            page.keyboard.press("Escape")
            page.fill("#studies-search", "study06")
            page.wait_for_timeout(400)
            searched = _beside(page, "study06")
            page.fill("#studies-search", "")
            page.wait_for_timeout(400)
            page.click("#studies-compare")
            page.wait_for_selector(".studies-means")
            page.wait_for_timeout(1200)
            over = page.evaluate("""() => {
              const a = document.getElementById('studies-compare-bar').getBoundingClientRect();
              const b = document.getElementById('studies-compared').getBoundingClientRect();
              return a.right > b.left && a.left < b.right && a.bottom > b.top && a.top < b.bottom; }""")
            browser.close()
    finally:
        session.server.shutdown()
    assert chosen["gap"] < 30, chosen
    assert drawer["insideColumn"] and drawer["gap"] < 30, drawer
    assert tagged["gap"] < 30, tagged
    assert editor["shown"] and not editor["over"], editor
    assert searched["gap"] < 30, searched
    assert not over
    assert errors == []


def test_in_the_table_from_the_keyboard(many) -> None:
    from playwright.sync_api import sync_playwright

    session = _open(many)
    try:
        with sync_playwright() as pw:
            browser, page, errors = _page(pw, session, many)
            page.click('[data-studies-view="table"]')
            page.wait_for_selector("#studies-table tbody input")
            rows = page.locator("#studies-table tbody input")
            rows.nth(2).focus()
            page.keyboard.press("Space")
            rows.nth(5).focus()
            page.keyboard.press("Space")
            page.wait_for_selector("#studies-compare-bar:not([hidden])")
            page.keyboard.press("Tab")
            focused = page.evaluate("() => document.activeElement.id")
            name = page.evaluate(
                "() => document.querySelectorAll('#studies-table tbody input')[5].dataset.path")
            beside = _beside(page, name.rsplit("/", 1)[-1])
            page.keyboard.press("Escape")
            back = page.evaluate("() => document.activeElement.dataset.path")
            browser.close()
    finally:
        session.server.shutdown()
    assert focused == "studies-compare"
    assert beside["gap"] < 30, beside
    assert back == name
    assert errors == []


def test_kept_inside_the_window_near_its_edge(many) -> None:
    from playwright.sync_api import sync_playwright

    session = _open(many)
    try:
        with sync_playwright() as pw:
            browser, page, errors = _page(pw, session, many, width=390, height=760)
            _box(page, "study05").click()
            target = _box(page, "study04")
            target.scroll_into_view_if_needed()
            target.click()
            page.wait_for_selector("#studies-compare-bar:not([hidden])")
            shown = _bar(page)
            browser.close()
    finally:
        session.server.shutdown()
    assert shown["inView"], shown


_OVER = """(id) => {
  const bar = document.getElementById('studies-compare-bar');
  const a = bar.getBoundingClientRect();
  const shown = !bar.hidden && getComputedStyle(bar).visibility === 'visible' && a.width > 0;
  const over = [...document.querySelectorAll(id)].some(n => {
    const b = n.getBoundingClientRect();
    return b.width > 0 && a.right > b.left && a.left < b.right && a.bottom > b.top && a.top < b.bottom;
  });
  return {shown, over, right: a.right};
}"""


def test_it_does_not_cover_a_comparison_taller_than_the_window(many) -> None:
    """On a phone the comparison fills the window: the bar docked at the
    column's foot covered its rows. It is put aside while it would, and is
    back once the comparison is scrolled away."""
    from playwright.sync_api import sync_playwright

    session = _open(many)
    try:
        with sync_playwright() as pw:
            browser, page, errors = _page(pw, session, many, width=390, height=480)
            _box(page, "study05").click()
            target = _box(page, "study04")
            target.scroll_into_view_if_needed()
            target.click()
            page.wait_for_selector("#studies-compare-bar:not([hidden])")
            page.click("#studies-compare")
            page.wait_for_selector(".studies-means")
            page.wait_for_timeout(1200)
            reading = page.evaluate(_OVER, "#studies-compared")
            # Compare was pressed, then hidden: the focus is on the comparison.
            focus = page.evaluate("() => document.activeElement.id")
            heard = page.text_content("#studies-chosen-heard")
            tall = page.evaluate(
                "() => document.getElementById('studies-compared').getBoundingClientRect().height")
            target.scroll_into_view_if_needed()
            page.wait_for_timeout(400)
            back = page.evaluate(_OVER, "#studies-compared")
            browser.close()
    finally:
        session.server.shutdown()
    assert tall > 480
    assert not (reading["shown"] and reading["over"]), reading
    assert reading["shown"] or focus == "studies-compared", focus
    assert reading["shown"] or heard.endswith("come back as the page is scrolled."), heard
    assert back["shown"] and not back["over"], back
    assert errors == []


def test_the_agent_over_the_page_is_not_under_it(many) -> None:
    """Under 1100 pixels the Agent is drawn over the page: the bar beside a
    card under it was hidden beneath it."""
    from playwright.sync_api import sync_playwright

    session = _open(many)
    try:
        with sync_playwright() as pw:
            browser, page, errors = _page(pw, session, many, width=1000, height=800)
            # The two boxes furthest right, where the Agent will be.
            names = page.evaluate("""() => [...document.querySelectorAll('.study-pick input')]
              .filter(b => { const r = b.getBoundingClientRect();
                             return r.top > 0 && r.bottom < innerHeight; })
              .sort((a, b) => b.getBoundingClientRect().left - a.getBoundingClientRect().left)
              .slice(0, 2).map(b => b.dataset.path.split('/').pop())""")
            for name in names:
                _box(page, name).click()
            page.wait_for_selector("#studies-compare-bar:not([hidden])")
            page.evaluate("() => window.FastMDXAgentBeside.open()")
            page.wait_for_timeout(800)
            seen = page.evaluate(_OVER, "#agent-drawer")
            # Put where the Agent is, the Agent is what shows.
            under = page.evaluate("""() => {
              const bar = document.getElementById('studies-compare-bar');
              const d = document.getElementById('agent-drawer').getBoundingClientRect();
              bar.style.left = (d.left + 40) + 'px';
              bar.style.top = (d.top + 300) + 'px';
              const r = bar.getBoundingClientRect();
              const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
              return !!hit.closest('#agent-drawer'); }""")
            browser.close()
    finally:
        session.server.shutdown()
    assert len(names) == 2
    assert seen["shown"] and not seen["over"], seen
    assert under
    assert errors == []


def test_tab_goes_on_from_the_study_chosen(many) -> None:
    """The bar is first in the page's order: Tab from Clear went to the top
    of the page, and Shift+Tab from Compare to the search. They go on from
    the study chosen, and back to it."""
    from playwright.sync_api import sync_playwright

    session = _open(many)
    try:
        with sync_playwright() as pw:
            browser, page, errors = _page(pw, session, many)
            first, second = _box(page, "study03"), _box(page, "study00")
            first.scroll_into_view_if_needed()
            first.focus()
            page.keyboard.press("Space")
            second.scroll_into_view_if_needed()
            second.focus()
            page.keyboard.press("Space")
            page.wait_for_selector("#studies-compare-bar:not([hidden])")
            where = "() => Math.max(...[...document.querySelectorAll('*')].map(e => e.scrollTop))"
            before = page.evaluate(where)
            said = "() => document.activeElement.outerHTML.slice(0, 120)"
            page.keyboard.press("Tab")
            page.keyboard.press("Shift+Tab")
            back = page.evaluate("() => document.activeElement.dataset.path || ''")
            # From the box: Compare, Clear, then what follows it.
            page.keyboard.press("Tab")
            page.keyboard.press("Tab")
            page.keyboard.press("Tab")
            on = page.evaluate(said)
            after = page.evaluate(where)
            # What follows the study chosen in the page, the bar left out.
            natural = page.evaluate("""() => {
              const bar = document.getElementById('studies-compare-bar');
              const all = [...document.querySelectorAll(
                'a[href], button, input, select, textarea, [tabindex]')].filter(n =>
                !n.disabled && n.tabIndex >= 0 && !bar.contains(n) && n.getClientRects().length
                && getComputedStyle(n).visibility !== 'hidden');
              const box = all.find(n => (n.dataset.path || '').endsWith('/study00'));
              return all[all.indexOf(box) + 1].outerHTML.slice(0, 120); }""")
            browser.close()
    finally:
        session.server.shutdown()
    assert back.endswith("/study00")
    assert on == natural, (on, natural)
    assert before > 0 and abs(after - before) < 2, (before, after)
    assert errors == []


def test_in_the_table_it_does_not_cover_the_tags_being_changed(many) -> None:
    from playwright.sync_api import sync_playwright

    session = _open(many)
    try:
        with sync_playwright() as pw:
            browser, page, errors = _page(pw, session, many)
            page.click('[data-studies-view="table"]')
            page.wait_for_selector("#studies-table tbody input")
            rows = page.locator("#studies-table tbody input")
            rows.nth(1).click()
            rows.nth(3).click()
            page.wait_for_selector("#studies-compare-bar:not([hidden])")
            rows.nth(3).locator("xpath=ancestor::tr").locator(".study-tag-edit").click()
            page.wait_for_selector(".studies-editing")
            page.wait_for_timeout(300)
            seen = page.evaluate(_OVER, ".studies-editing")
            browser.close()
    finally:
        session.server.shutdown()
    assert seen["shown"] and not seen["over"], seen
    assert errors == []


def test_reached_down_the_page_tab_keeps_the_page_s_order(many) -> None:
    """Tab and Shift+Tab go on from the study chosen only where the bar was
    reached from it: reached down the page from its header, the cards and
    the header are reached as before, with no loop."""
    from playwright.sync_api import sync_playwright

    session = _open(many)
    act = """() => { const a = document.activeElement;
      const card = a.closest('.study-card');
      return a.id || (card ? 'card:' + card.dataset.path.split('/').pop() : a.tagName); }"""
    try:
        with sync_playwright() as pw:
            browser, page, errors = _page(pw, session, many)
            _box(page, "study01").click()
            _box(page, "study00").scroll_into_view_if_needed()
            _box(page, "study00").click()
            page.wait_for_selector("#studies-compare-bar:not([hidden])")
            page.focus("#studies-search")
            forward = []
            for _ in range(14):
                page.keyboard.press("Tab")
                forward.append(page.evaluate(act))
            first = page.evaluate("() => document.querySelector('.study-card').dataset.path")
            page.locator(f'.study-card[data-path="{first}"] .study-pick input').focus()
            backward = []
            for _ in range(14):
                page.keyboard.press("Shift+Tab")
                backward.append(page.evaluate(act))
            browser.close()
    finally:
        session.server.shutdown()
    at = forward.index("studies-clear")
    assert forward[at + 1].startswith("card:"), forward
    assert "studies-search" in backward, backward
    assert errors == []


def test_the_second_scrolled_away_it_is_not_left_at_the_top(many) -> None:
    """The second study scrolled out of view above: the bar goes to the
    column's foot, not to the top of the window where the study was."""
    from playwright.sync_api import sync_playwright

    session = _open(many)
    try:
        with sync_playwright() as pw:
            browser, page, errors = _page(pw, session, many)
            _box(page, "study11").click()
            _box(page, "study10").click()
            page.wait_for_selector("#studies-compare-bar:not([hidden])")
            _box(page, "study00").scroll_into_view_if_needed()
            page.wait_for_timeout(400)
            gone = page.evaluate("""() => {
              const box = [...document.querySelectorAll('.study-pick input')]
                .find(b => b.dataset.path.endsWith('/study10'));
              return box.getBoundingClientRect().bottom; }""")
            shown = _bar(page)
            browser.close()
    finally:
        session.server.shutdown()
    assert gone < 120
    assert shown["inView"] and shown["top"] > 800 / 2, shown
    assert errors == []


def test_back_on_the_page_it_is_beside_the_second_again(many) -> None:
    """Left for another page and the window resized there, the bar is
    placed again beside the second study when the page is shown again."""
    from playwright.sync_api import sync_playwright

    session = _open(many)
    try:
        with sync_playwright() as pw:
            browser, page, errors = _page(pw, session, many)
            _box(page, "study11").click()
            _box(page, "study10").click()
            page.wait_for_selector("#studies-compare-bar:not([hidden])")
            page.evaluate("() => { location.hash = '#overview'; }")
            page.wait_for_timeout(500)
            page.set_viewport_size({"width": 1150, "height": 800})
            page.wait_for_timeout(300)
            page.evaluate("() => { location.hash = '#studies'; }")
            page.wait_for_timeout(150)
            beside = _beside(page, "study10")
            seen = page.evaluate("""() => {
              const box = [...document.querySelectorAll('.study-pick input')]
                .find(b => b.dataset.path.endsWith('/study10')).getBoundingClientRect();
              return box.top > 0 && box.bottom < innerHeight; }""")
            browser.close()
    finally:
        session.server.shutdown()
    assert seen
    assert beside["shown"] and beside["gap"] < 30, beside
    assert errors == []


def test_two_chosen_whose_names_are_gone_are_said_as_two(many) -> None:
    from playwright.sync_api import sync_playwright

    session = _open(many)
    try:
        with sync_playwright() as pw:
            browser, page, errors = _page(pw, session, many)
            _box(page, "study07").click()
            _box(page, "study06").click()
            page.wait_for_selector("#studies-compare-bar:not([hidden])")
            page.evaluate("() => window.FastMDXStudies.load('/no/such/folder')")
            page.wait_for_timeout(800)
            heard = page.text_content("#studies-chosen-heard")
            browser.close()
    finally:
        session.server.shutdown()
    assert heard in ("", "Two chosen. Compare and Clear are next in the Tab order."), heard
    assert " and  chosen" not in heard


def test_cleared_with_the_studies_narrowed_away_the_focus_stays_on_the_page(many) -> None:
    from playwright.sync_api import sync_playwright

    session = _open(many)
    try:
        with sync_playwright() as pw:
            browser, page, errors = _page(pw, session, many)
            _box(page, "study07").click()
            _box(page, "study06").click()
            page.wait_for_selector("#studies-compare-bar:not([hidden])")
            page.fill("#studies-search", "study07")
            page.wait_for_timeout(300)
            page.focus("#studies-clear")
            page.keyboard.press("Enter")
            other = page.evaluate("() => document.activeElement.dataset.path || ''")
            _box(page, "study07").click()
            page.fill("#studies-search", "")
            _box(page, "study06").click()
            page.fill("#studies-search", "nothing matches this")
            page.wait_for_timeout(300)
            page.focus("#studies-clear")
            page.keyboard.press("Enter")
            none = page.evaluate("() => document.activeElement.id")
            browser.close()
    finally:
        session.server.shutdown()
    assert other.endswith("/study07"), other
    assert none == "studies-search", none
    assert errors == []


def test_a_tag_editor_that_pushes_the_study_away_does_not_put_it_aside(many) -> None:
    """The editor opened under the last card pushes its box below the
    window: the bar goes to another corner of the column, not aside."""
    from playwright.sync_api import sync_playwright

    session = _open(many)
    try:
        with sync_playwright() as pw:
            browser, page, errors = _page(pw, session, many)
            _box(page, "study01").scroll_into_view_if_needed()
            _box(page, "study01").click()
            _box(page, "study00").click()
            page.wait_for_selector("#studies-compare-bar:not([hidden])")
            page.locator('.study-card[data-path$="/study00"] .study-tag-edit').click()
            page.wait_for_selector(".study-tag-editor")
            page.wait_for_timeout(300)
            seen = page.evaluate(_OVER, ".study-tag-editor")
            browser.close()
    finally:
        session.server.shutdown()
    assert seen["shown"] and not seen["over"], seen
    assert errors == []


def test_on_a_phone_it_is_never_under_the_bars_kept_at_the_top(many) -> None:
    """The second study scrolled up under the phone's bar of links and the
    page's header, which stay at the top: the bar is not drawn under them."""
    from playwright.sync_api import sync_playwright

    session = _open(many)
    try:
        with sync_playwright() as pw:
            browser, page, errors = _page(pw, session, many, width=390, height=760)
            _box(page, "study05").click()
            target = _box(page, "study04")
            target.scroll_into_view_if_needed()
            target.click()
            page.wait_for_selector("#studies-compare-bar:not([hidden])")
            # The study's box scrolled up under the header, hidden by it.
            page.evaluate("""() => {
              const head = document.querySelector('.page[data-page="studies"] .page-header')
                .getBoundingClientRect();
              const box = [...document.querySelectorAll('.study-pick input')]
                .find(b => b.dataset.path.endsWith('/study04'));
              const by = box.getBoundingClientRect().top - (head.bottom - 50);
              let n = box.parentElement;
              while (n && !(n.scrollHeight > n.clientHeight + 2 &&
                     /auto|scroll/.test(getComputedStyle(n).overflowY))) n = n.parentElement;
              (n || document.scrollingElement).scrollBy(0, by); }""")
            page.wait_for_timeout(400)
            seen = page.evaluate("""() => {
              const head = document.querySelector('.page[data-page="studies"] .page-header')
                .getBoundingClientRect();
              const bar = document.getElementById('studies-compare-bar').getBoundingClientRect();
              const hit = document.elementFromPoint(bar.left + bar.width / 2, bar.top + bar.height / 2);
              const box = [...document.querySelectorAll('.study-pick input')]
                .find(b => b.dataset.path.endsWith('/study04')).getBoundingClientRect();
              return {below: bar.top >= head.bottom - 1, hit: !!hit && !!hit.closest('#studies-compare-bar'),
                      boxUnder: box.top > 0 && box.bottom < head.bottom}; }""")
            browser.close()
    finally:
        session.server.shutdown()
    assert seen["boxUnder"], seen
    assert seen["below"] and seen["hit"], seen
    assert errors == []


def test_closed_with_no_study_chosen_left_the_focus_stays_on_the_page(many) -> None:
    """Both unticked with the comparison open: Close from the keyboard has
    no study chosen to go back to, and Compare is hidden; the focus goes to
    the search, not to nothing."""
    from playwright.sync_api import sync_playwright

    session = _open(many)
    try:
        with sync_playwright() as pw:
            browser, page, errors = _page(pw, session, many)
            _box(page, "study11").click()
            _box(page, "study10").click()
            page.wait_for_selector("#studies-compare-bar:not([hidden])")
            page.click("#studies-compare")
            page.wait_for_selector(".studies-means")
            _box(page, "study11").uncheck()
            _box(page, "study10").uncheck()
            page.focus('#studies-compared button[aria-label="Close"]')
            page.keyboard.press("Enter")
            focus = page.evaluate("() => document.activeElement.id || document.activeElement.tagName")
            browser.close()
    finally:
        session.server.shutdown()
    assert focus == "studies-search", focus
    assert errors == []


def test_closed_with_the_second_narrowed_away_the_focus_goes_to_the_first(many) -> None:
    from playwright.sync_api import sync_playwright

    session = _open(many)
    try:
        with sync_playwright() as pw:
            browser, page, errors = _page(pw, session, many)
            _box(page, "study11").click()
            _box(page, "study10").click()
            page.wait_for_selector("#studies-compare-bar:not([hidden])")
            page.click("#studies-compare")
            page.wait_for_selector(".studies-means")
            # The second narrowed away: the first is the one left to go to.
            page.fill("#studies-search", "study11")
            page.wait_for_timeout(300)
            page.focus('#studies-compared button[aria-label="Close"]')
            page.keyboard.press("Enter")
            focus = page.evaluate("() => document.activeElement.dataset.path || ''")
            browser.close()
    finally:
        session.server.shutdown()
    assert focus.endswith("/study11"), focus
    assert errors == []


def test_closed_with_both_narrowed_away_and_the_bar_aside_the_focus_is_not_hidden(many) -> None:
    """On a phone the comparison fills the window and the bar is put aside:
    with both studies narrowed away, Close does not send the focus to the
    hidden Compare."""
    from playwright.sync_api import sync_playwright

    session = _open(many)
    try:
        with sync_playwright() as pw:
            browser, page, errors = _page(pw, session, many, width=390, height=480)
            _box(page, "study05").click()
            target = _box(page, "study04")
            target.scroll_into_view_if_needed()
            target.click()
            page.wait_for_selector("#studies-compare-bar:not([hidden])")
            page.click("#studies-compare")
            page.wait_for_selector(".studies-means")
            page.wait_for_timeout(1200)
            page.fill("#studies-search", "study07")
            page.wait_for_timeout(300)
            aside = page.evaluate("""() => document.getElementById('studies-compare-bar')
              .classList.contains('is-aside')""")
            page.focus('#studies-compared button[aria-label="Close"]')
            page.keyboard.press("Enter")
            focus = page.evaluate("() => document.activeElement.id || document.activeElement.tagName")
            browser.close()
    finally:
        session.server.shutdown()
    assert aside
    assert focus == "studies-search", focus
    assert errors == []


def test_put_aside_is_said_once_for_the_pair(many) -> None:
    """Scrolled up and down past a comparison taller than the window, the
    bar is put aside and back again each time; a screen reader hears that
    once, not at every turn."""
    from playwright.sync_api import sync_playwright

    session = _open(many)
    try:
        with sync_playwright() as pw:
            browser, page, errors = _page(pw, session, many, width=390, height=480)
            _box(page, "study05").click()
            target = _box(page, "study04")
            target.scroll_into_view_if_needed()
            target.click()
            page.wait_for_selector("#studies-compare-bar:not([hidden])")
            page.evaluate("""() => { window.heardSaid = [];
              new MutationObserver(() => window.heardSaid.push(
                document.getElementById('studies-chosen-heard').textContent))
                .observe(document.getElementById('studies-chosen-heard'),
                         {childList: true, characterData: true, subtree: true}); }""")
            page.click("#studies-compare")
            page.wait_for_selector(".studies-means")
            page.wait_for_timeout(1200)
            for _ in range(3):
                target.scroll_into_view_if_needed()
                page.wait_for_timeout(300)
                page.evaluate("() => document.getElementById('studies-compared').scrollIntoView()")
                page.wait_for_timeout(300)
            aside = page.evaluate("""() => document.getElementById('studies-compare-bar')
              .classList.contains('is-aside')""")
            said = page.evaluate("() => window.heardSaid")
            browser.close()
    finally:
        session.server.shutdown()
    assert aside
    assert sum(t.endswith("come back as the page is scrolled.") for t in said) == 1, said
    assert errors == []


def test_the_bar_is_not_printed(many) -> None:
    """Fixed to the window, it was printed on every page of a printout."""
    from playwright.sync_api import sync_playwright

    session = _open(many)
    try:
        with sync_playwright() as pw:
            browser, page, errors = _page(pw, session, many)
            _box(page, "study11").click()
            _box(page, "study10").click()
            page.wait_for_selector("#studies-compare-bar:not([hidden])")
            page.emulate_media(media="print")
            shown = page.evaluate("""() => getComputedStyle(
              document.getElementById('studies-compare-bar')).display""")
            browser.close()
    finally:
        session.server.shutdown()
    assert shown == "none"
    assert errors == []
