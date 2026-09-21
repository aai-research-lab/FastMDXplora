"""The composer sits at the foot, with its buttons centred, in any browser.

Four faults, two symptoms. The Agent page's height subtracted a copied
88px for the shell's padding, which left the shell's 64px of bottom
padding under a composer pinned to the foot -- empty space that only
lifted it. The textarea was an inline block, so Chrome left a 5px gap
under it and Firefox did not, and the buttons, measured from the box
around it, sat low in Chrome alone. Two rules from the old single-field
design, written against the id, outranked the composer's own and set its
size, line height and a resize handle. And the auto-size set the height
to scrollHeight, which leaves out the border of a border-box, so every
line was two pixels short.

Measured as rendered, not read from the stylesheet: the computed layout
is what a person sees, and what differed between browsers.
"""

from __future__ import annotations

import tempfile
import unittest

try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as _pw:
        _pw.chromium.launch().close()
    HAVE_BROWSER = True
except Exception:  # pragma: no cover - the browser is optional
    HAVE_BROWSER = False

MEASURE = """() => {
  const q = s => document.querySelector(s), r = s => q(s).getBoundingClientRect();
  const ta = r('#agent-request'), box = r('.agent-composer-box');
  const send = r('#agent-propose'), plus = r('#agent-attach');
  const page = r('.page[data-page="agent"]'), cs = getComputedStyle(q('#agent-request'));
  const t = q('#agent-request');
  return {ta_h: ta.height, box_h: box.height,
          send_centre: (send.top + send.bottom) / 2, plus_centre: (plus.top + plus.bottom) / 2,
          ta_centre: (ta.top + ta.bottom) / 2, send_to_foot: ta.bottom - send.bottom,
          below_page: innerHeight - page.bottom,
          scrolls: document.documentElement.scrollHeight > innerHeight + 1,
          display: cs.display, resize: cs.resize, font: cs.fontSize, lh: cs.lineHeight,
          clipped: t.scrollHeight > t.clientHeight + 1};
}"""


@unittest.skipUnless(HAVE_BROWSER, "a Chromium from Playwright is needed")
class TestTheComposerSitsRight(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from fastmdxplora.gui.server import start_dashboard_session

        cls.session = start_dashboard_session(output=tempfile.mkdtemp(),
                                              host="127.0.0.1", port=0)
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()
        cls.page = cls.browser.new_page(viewport={"width": 1400, "height": 900})
        cls.page.goto(cls.session.url + "#agent", wait_until="domcontentloaded")
        cls.page.wait_for_selector("#agent-request", timeout=20000)
        cls.page.wait_for_timeout(500)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.session.server.shutdown()

    def type(self, text: str) -> dict:
        self.page.fill("#agent-request", text)
        self.page.dispatch_event("#agent-request", "input")
        self.page.wait_for_timeout(120)
        return self.page.evaluate(MEASURE)

    def test_the_box_is_the_textarea_with_no_gap_under_it(self):
        m = self.type("")
        self.assertEqual(m["display"], "block")
        self.assertAlmostEqual(m["box_h"], m["ta_h"], delta=0.5)

    def test_both_buttons_are_centred_on_a_single_line(self):
        m = self.type("simulate trp-cage")
        self.assertAlmostEqual(m["send_centre"], m["ta_centre"], delta=0.5)
        self.assertAlmostEqual(m["plus_centre"], m["ta_centre"], delta=0.5)

    def test_the_buttons_stay_at_the_foot_as_it_grows(self):
        one = self.type("one line")
        three = self.type("line one\nline two\nline three")
        self.assertGreater(three["ta_h"], one["ta_h"])
        self.assertAlmostEqual(three["send_to_foot"], one["send_to_foot"], delta=0.5)

    def test_what_is_typed_is_not_clipped(self):
        # The border is counted when it grows, so no line is cut short.
        for text in ("one line", "line one\nline two\nline three"):
            with self.subTest(text=text):
                self.assertFalse(self.type(text)["clipped"])

    def test_it_sits_near_the_foot_of_the_window(self):
        m = self.type("")
        self.assertLessEqual(m["below_page"], 16)
        self.assertFalse(m["scrolls"], "the page scrolls instead of filling the window")

    def test_the_rules_in_force_are_the_composers(self):
        # The old single-field rules set 14px, a 1.55 line height and a
        # resize handle; the composer's own are these.
        m = self.type("")
        self.assertEqual(m["resize"], "none")
        self.assertEqual(m["font"], "14.5px")
        self.assertEqual(m["lh"], "21.75px")

    def test_the_send_button_is_inside_the_box_at_its_right_end(self):
        edges = self.page.evaluate("""() => {
          const t = document.querySelector('#agent-request').getBoundingClientRect();
          const s = document.querySelector('#agent-propose').getBoundingClientRect();
          return {t_left: t.left, t_right: t.right, s_left: s.left, s_right: s.right,
                  t_top: t.top, t_bottom: t.bottom, s_top: s.top, s_bottom: s.bottom}; }""")
        self.assertGreaterEqual(edges["s_left"], edges["t_left"])
        self.assertLessEqual(edges["s_right"], edges["t_right"])
        self.assertGreaterEqual(edges["s_top"], edges["t_top"])
        self.assertLessEqual(edges["s_bottom"], edges["t_bottom"])
        self.assertLess(edges["t_right"] - edges["s_right"], 16, "not at the right end")

    def test_what_is_typed_clears_both_buttons(self):
        # The text starts after the + and stops before the send, so neither
        # button sits on top of a word.
        gaps = self.page.evaluate("""() => {
          const t = document.querySelector('#agent-request'), cs = getComputedStyle(t);
          const r = t.getBoundingClientRect();
          const plus = document.querySelector('#agent-attach').getBoundingClientRect();
          const send = document.querySelector('#agent-propose').getBoundingClientRect();
          return {text_starts: r.left + parseFloat(cs.borderLeftWidth) + parseFloat(cs.paddingLeft),
                  text_ends: r.right - parseFloat(cs.borderRightWidth) - parseFloat(cs.paddingRight),
                  plus_right: plus.right, send_left: send.left}; }""")
        self.assertGreaterEqual(gaps["text_starts"], gaps["plus_right"])
        self.assertLessEqual(gaps["text_ends"], gaps["send_left"])

    def test_a_long_thread_scrolls_and_the_page_does_not(self):
        # The page has a fixed height so the thread scrolls inside it and
        # the composer stays at the foot. With a minimum height instead, the
        # page grew with the thread and the composer left the window.
        state = self.page.evaluate("""() => {
          const thread = document.querySelector('#agent-thread');
          const saved = thread.innerHTML;
          for (let i = 0; i < 60; i++) {
            const m = document.createElement('div'); m.className = 'agent-msg agent-msg-user';
            m.innerHTML = '<div>message ' + i + '</div>'; thread.appendChild(m);
          }
          const out = {thread_scrolls: thread.scrollHeight > thread.clientHeight + 1,
                       page_scrolls: document.documentElement.scrollHeight > innerHeight + 1,
                       composer_in_view: document.querySelector('.agent-composer')
                                           .getBoundingClientRect().bottom <= innerHeight};
          thread.innerHTML = saved;
          return out; }""")
        self.assertTrue(state["thread_scrolls"])
        self.assertFalse(state["page_scrolls"])
        self.assertTrue(state["composer_in_view"])
