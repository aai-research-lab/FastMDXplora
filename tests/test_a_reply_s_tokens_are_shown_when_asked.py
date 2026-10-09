"""What a reply took, in tokens, is shown when asked, on the row of its tools.

Every reply carried a line such as "60,049 tokens in (58,416 from the
cache, 1,625 written to the cache) · 1,601 out" under it, with Useful and
Wrong on a row of their own above Copy. User, 10-09: "There should be a
button that user needs to click first to reveal the token info ... that
button should be on the same row with copy, retry, thumbs up/down". The
reply's Copy, Useful, Wrong and a tokens icon are one row; the line is
shown once the icon is pressed, and hidden again by a second press.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_the_agent_page_on_a_phone_and_its_settings import _page, session  # noqa: F401

USAGE = {"calls": 1, "input_tokens": 8, "cache_read_tokens": 58416,
         "cache_write_tokens": 1625, "output_tokens": 1601}
SAID = "60,049 tokens in (58,416 from the cache, 1,625 written to the cache) · 1,601 out"


def _row(page):
    return page.evaluate("""() => {
      const replies = document.querySelectorAll('#agent-thread .agent-msg-agent');
      const reply = replies[replies.length - 1];
      const rows = reply.querySelectorAll('.agent-msg-tools');
      const row = rows[rows.length - 1];
      const box = el => el.getBoundingClientRect();
      // One line: the buttons' middles level (the marks are 28 px, Copy 24).
      const mid = el => { const r = box(el); return { top: r.top + r.height / 2 }; };
      const copy = row.querySelector('button[aria-label="Copy"]');
      const toggle = row.querySelector('.agent-usage-toggle');
      const said = row.querySelector('.agent-usage');
      return {
        rows: rows.length,
        meta: reply.querySelectorAll('.agent-meta').length,
        kinds: [...row.querySelectorAll('button')].map(b => b.getAttribute('aria-label')),
        sameLine: !!copy && !!toggle && Math.abs(mid(copy).top - mid(toggle).top) < 2
          && [...row.querySelectorAll('[data-feedback]')].every(
            b => Math.abs(mid(b).top - mid(copy).top) < 2),
        rowShown: getComputedStyle(row).visibility === 'visible',
        expanded: toggle && toggle.getAttribute('aria-expanded'),
        controls: toggle && toggle.getAttribute('aria-controls') === said.id,
        shown: !!said && !said.hidden && said.getClientRects().length > 0,
        text: said ? said.textContent : null,
      };
    }""")


def test_the_tokens_are_shown_once_asked_on_copy_s_row(session) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    replies = [{"ok": False, "answer": "It has 76 residues.", "cites": [], "attempts": [],
                "usage": USAGE}]
    with sync_playwright() as pw:
        browser, page, errors = _page(pw, session.url, 1400)
        page.route("**/api/agent/propose*", lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(replies.pop(0))))
        page.fill("#agent-request", "How many residues?")
        page.keyboard.press("Enter")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('76 residues')")
        page.mouse.move(0, 0)
        before = _row(page)
        page.locator("#agent-thread .agent-usage-toggle").last.click()
        opened = _row(page)
        page.locator("#agent-thread .agent-usage-toggle").last.click()
        closed = _row(page)
        page.wait_for_timeout(600)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#agent-thread .agent-usage-toggle >> nth=0", state="attached")
        restored = _row(page)
        page.locator("#agent-thread .agent-usage-toggle").last.click()
        reopened = _row(page)
        browser.close()
    # One row: Copy, the two marks and the tokens icon, in view without a hover.
    assert before["rows"] == 1 and before["meta"] == 1
    assert before["kinds"] == ["Copy", "Useful", "Wrong", "Tokens this reply took"]
    assert before["sameLine"] and before["rowShown"]
    # The line only once asked, and hidden again by a second press.
    assert not before["shown"] and before["expanded"] == "false" and before["controls"]
    assert opened["shown"] and opened["expanded"] == "true" and opened["text"] == SAID
    assert opened["sameLine"]
    assert not closed["shown"] and closed["expanded"] == "false"
    # As it was kept: hidden until asked.
    assert not restored["shown"] and restored["kinds"][-1] == "Tokens this reply took"
    assert reopened["shown"] and reopened["text"] == SAID
    assert errors == []


def test_a_reply_without_its_tokens_has_no_icon_for_them(session) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page, errors = _page(pw, session.url, 1400)
        page.wait_for_selector("#agent-thread .agent-answer:has-text('An earlier answer')")
        row = _row(page)
        browser.close()
    assert row["kinds"] == ["Copy", "Useful", "Wrong"]
    assert row["text"] is None
    assert errors == []
