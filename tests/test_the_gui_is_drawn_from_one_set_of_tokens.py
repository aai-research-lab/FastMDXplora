"""The GUI is drawn from one set of tokens.

Eighteen font sizes, from 10px to 28px, with the smallest on the uppercase
labels and file actions where reading was hardest; ninety-seven tints written
as the dark scheme's accents (rgba(99, 230, 255, ...)), so on Paper, whose
accent is blue, every hover, selection and active chip was still cyan; a
toast on a fixed near-black carrying the scheme's text, which on Paper is
near-black too; three `font` shorthands made invalid by an `inherit` inside
them, so their sizes never applied; and a focus ring on a few controls.

Now the sizes are a scale of eight tokens, every tint is mixed from the
scheme's own accent, the file actions are sentence case at the size of every
other control, and one rule rings whatever the keyboard is on.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "src" / "fastmdxplora" / "gui" / "static"
CSS = (STATIC / "dashboard.css").read_text(encoding="utf-8")
THEME = (STATIC / "theme.css").read_text(encoding="utf-8")
SCALE = ("2xs", "xs", "sm", "md", "lg", "xl", "2xl", "3xl")


class TestTheSheet:
    def test_every_size_is_on_the_scale(self):
        for token in SCALE:
            assert re.search(rf"--text-{token}:\s*[0-9.]+px;", THEME), token
        sizes = re.findall(r"font-size:\s*([^;]+);", CSS)
        assert sizes
        off = [s for s in sizes if not re.fullmatch(r"var\(--text-(%s)\)" % "|".join(SCALE), s.strip())
               and s.strip() not in ("inherit", "1em", "100%")]
        assert off == []

    def test_no_tint_is_the_dark_schemes_accent(self):
        for accent in ("99, *230, *255", "167, *139, *250", "103, *232, *163",
                       "255, *184, *107", "255, *114, *114", "255, *255, *255"):
            assert not re.search(rf"rgba\({accent},", CSS), accent

    def test_no_font_shorthand_is_invalid(self):
        for shorthand in re.findall(r"(?<![-\w])font:\s*([^;]+);", CSS):
            assert "inherit" not in shorthand.split() or shorthand.strip() == "inherit", shorthand

    def test_a_file_action_is_not_small_capitals(self):
        rule = re.search(r"\.file-action \{([^}]*)\}", CSS).group(1)
        assert "uppercase" not in rule and "var(--text-xs)" in rule

    def test_one_ring_for_the_keyboard(self):
        assert re.search(r":where\(button, a, input, select, textarea, summary, \[tabindex\]\)"
                         r":focus-visible \{\s*outline: var\(--focus-ring\);", CSS)
        assert "--focus-ring: 2px solid var(--accent-cyan);" in THEME


@pytest.fixture(scope="module")
def paper(tmp_path_factory):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(tmp_path_factory.mktemp("tokens")),
                                      host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1400, "height": 900}, color_scheme="light")
            page.set_default_timeout(60000)
            page.goto(session.url + "#overview", wait_until="domcontentloaded")
            page.wait_for_function("() => document.documentElement.dataset.theme === 'light'")
            yield page
            browser.close()
    finally:
        session.server.shutdown()


def _rgb(text: str) -> tuple[float, ...]:
    """A computed colour's red, green and blue, 0 to 255, whether the
    browser writes it rgb(...) or color(srgb ...) as it does a mix."""
    numbers = [float(v) for v in re.findall(r"[0-9.]+", text)]
    if text.startswith("color(srgb"):
        return tuple(255 * v for v in numbers[:3])
    return tuple(numbers[:3])


def test_a_tint_follows_the_scheme(paper) -> None:
    """An active chip on Light is tinted with Light's cyan, not the dark
    scheme's."""
    tint = paper.evaluate("""() => {
        const b = document.createElement('button');
        b.className = 'chip-btn active'; document.body.appendChild(b);
        const border = getComputedStyle(b).borderColor; b.remove(); return border; }""")
    accent = paper.evaluate(
        "() => getComputedStyle(document.documentElement).getPropertyValue('--accent-cyan').trim()")
    assert accent == "#1b7590"
    # 50% of #1b7590 over transparent: the cyan's own channels.
    red, green, blue = _rgb(tint)
    assert (round(red), round(green), round(blue)) == (27, 117, 144)


def test_the_toast_is_on_the_schemes_ground(paper) -> None:
    ground = paper.evaluate("""() => {
        const t = document.createElement('div'); t.className = 'dashboard-toast';
        document.body.appendChild(t); const bg = getComputedStyle(t).backgroundColor;
        t.remove(); return bg; }""")
    assert _rgb(ground) == (255.0, 255.0, 255.0)


def test_the_keyboard_is_ringed(paper) -> None:
    paper.keyboard.press("Tab")
    for _ in range(40):
        focused = paper.evaluate("() => document.activeElement && document.activeElement.tagName")
        if focused in ("BUTTON", "A", "INPUT", "SELECT"):
            break
        paper.keyboard.press("Tab")
    ring = paper.evaluate(
        "() => { const s = getComputedStyle(document.activeElement);"
        " return [s.outlineStyle, s.outlineWidth, s.outlineColor, s.boxShadow]; }")
    assert ring[0] != "none" or ring[3] != "none", ring
