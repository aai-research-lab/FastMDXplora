"""Every scheme can be read, on every page.

Paper redefined the ground and the text and left the cards at the dark
scheme's colour, so every card on a Paper page was a near-black slab with
near-black text on it: "Completed", the figure captions and the chart titles
measured 1.1 to 1.6 to 1. Its status colours were the pastels that read on
black, and the charts drew their axes in the dark scheme's grey whatever the
scheme. Nothing looked, because every browser test ran in Graphite.

Here each page is opened in each scheme and every piece of visible text is
measured against what is behind it, by the WCAG formula, and held to WCAG
AA: 4.5 to 1 for text, 3 to 1 for large text (24 px, or 18.66 px bold). The
floor was 3 to 1 for all of it, which passed the muted labels of every
scheme at 3.6 to 4.5 to 1 on their own grounds; an accessibility audit of
every page (axe-core 4.13) found them. A control that cannot be used is
dimmed on purpose and is not measured.
"""

from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_the_drawing_scripts_run_in_a_browser import _write_study  # noqa: E402

PAGES = ("overview", "viewer", "analysis", "report", "files", "run", "agent")
#: Preferences and the citation, which were pages, and the Agent's settings.
DIALOGS = ("prefs-dialog", "cite-dialog", "agent-settings")
SCHEMES = ("dark", "light")

#: The text on the page measured against what is behind it; returns what
#: falls under 3:1. Text under a picture is not measured, since what is
#: behind it is not known.
UNREADABLE = r"""
() => {
  function parse(c) {
    const m = c.match(/rgba?\(([^)]+)\)/);
    if (m) {
      const p = m[1].split(/[ ,\/]+/).filter(Boolean).map(Number);
      return {r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1};
    }
    // A tint mixed from a scheme's token computes as color(srgb r g b / a),
    // channels from 0 to 1; read as nothing, it was left out of the sum.
    const s = c.match(/color\(srgb ([^)]+)\)/); if (!s) return null;
    const q = s[1].split(/[ \/]+/).filter(Boolean).map(Number);
    return {r: 255 * q[0], g: 255 * q[1], b: 255 * q[2], a: q.length > 3 ? q[3] : 1};
  }
  function lum(c) {
    const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
  }
  function over(top, under) {
    const a = top.a;
    return {r: top.r * a + under.r * (1 - a), g: top.g * a + under.g * (1 - a), b: top.b * a + under.b * (1 - a), a: 1};
  }
  // What is behind an element, as the colours it may be: one, or each
  // colour of a gradient under it (a primary button's), so text on a
  // gradient is held to its worst colour rather than read over the card.
  function backgrounds(el) {
    const stack = [];
    let stops = null;
    for (let e = el; e; e = e.parentElement) {
      const cs = getComputedStyle(e);
      if (cs.backgroundImage && cs.backgroundImage.includes("url(")) return null;
      if (cs.backgroundImage && cs.backgroundImage.includes("gradient(")) {
        stops = (cs.backgroundImage.match(/rgba?\([^)]+\)|color\(srgb [^)]+\)/g) || [])
          .map(parse).filter(Boolean);
        if (stops.length && stops.every(c => c.a >= 1)) break;
        stops = null;
      }
      const c = parse(cs.backgroundColor);
      if (c && c.a > 0) { stack.push(c); if (c.a >= 1) break; }
    }
    let base = {r: 255, g: 255, b: 255, a: 1};
    const bodyBg = parse(getComputedStyle(document.body).backgroundColor);
    if (bodyBg && bodyBg.a > 0) base = over(bodyBg, base);
    for (let i = stack.length - 1; i >= 0; i--) base = over(stack[i], base);
    return stops || [base];
  }
  const bad = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const seen = new Set();
  while (walker.nextNode()) {
    const t = walker.currentNode; const el = t.parentElement;
    if (!el || seen.has(el) || !t.textContent.trim()) continue;
    seen.add(el);
    if (el.closest("svg, canvas, [hidden], .sr-only")) continue;
    // A control that cannot be used is dimmed on purpose, as WCAG allows.
    if (el.closest(":disabled, [aria-disabled='true']")) continue;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === "hidden" || +cs.opacity === 0) continue;
    let hidden = false;
    for (let e = el; e; e = e.parentElement) { if (getComputedStyle(e).display === "none") { hidden = true; break; } }
    if (hidden) continue;
    if (!/[A-Za-z0-9]/.test(t.textContent)) continue;
    const bgs = backgrounds(el); if (!bgs) continue;
    const ink = parse(cs.color); if (!ink) continue;
    let op = 1; for (let e = el; e; e = e.parentElement) op *= +getComputedStyle(e).opacity;
    if (op < 0.05) continue;
    const ratio = Math.min(...bgs.map((bg) => {
      const fg = over({...ink, a: ink.a * op}, bg);
      const L1 = lum(fg), L2 = lum(bg);
      return (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
    }));
    const size = parseFloat(cs.fontSize);
    const large = size >= 24 || (size >= 18.66 && +cs.fontWeight >= 700);
    if (ratio < (large ? 3 : 4.5)) bad.push({text: t.textContent.trim().slice(0, 40), ratio: Math.round(ratio * 100) / 100, cls: el.className && String(el.className).slice(0, 50), id: el.id, tag: el.tagName});
  }
  return bad;
}
"""


@pytest.fixture(scope="module")
def dashboard(tmp_path_factory):
    from fastmdxplora.gui.server import start_dashboard_session

    study = _write_study(tmp_path_factory.mktemp("schemes") / "study")
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    yield session
    session.server.shutdown()


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        launched = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        yield launched
        launched.close()


def _choose(page, scheme: str) -> None:
    """As the settings popup's buttons do."""
    page.evaluate(f"() => document.querySelector('.seg-btn[data-theme={scheme}]').click()")


def _settled(page) -> None:
    """Every finite animation over: a page fading in is measured when it
    has, not half way."""
    page.wait_for_timeout(100)
    page.evaluate("""() => Promise.all(document.getAnimations()
        .filter(a => a.effect && a.effect.getComputedTiming().iterations !== Infinity)
        .map(a => a.finished.catch(() => null)))""")


def _in(browser, dashboard, scheme: str | None = None, **context):
    page = browser.new_context(viewport={"width": 1440, "height": 900}, **context).new_page()
    # Sixty seconds, as the other browser tests allow: the first page of a
    # session beside a full suite took longer than thirty to be ready.
    page.set_default_timeout(60000)
    page.goto(dashboard.url + "#overview", wait_until="domcontentloaded")
    page.wait_for_function("() => window.FastMDXDashboard && document.body.dataset.theme")
    # The loading screen gone, not fading: read half way through its fade,
    # its text was half transparent over the page, and CI read it so.
    page.wait_for_function("() => document.body.classList.contains('state-ready') && "
                           "getComputedStyle(document.querySelector('.loading-screen'))"
                           ".opacity === '0'")
    if scheme:
        _choose(page, scheme)
    return page


@pytest.mark.parametrize("scheme", SCHEMES)
def test_every_page_can_be_read(browser, dashboard, scheme) -> None:
    page = _in(browser, dashboard, scheme)
    unreadable = []
    for name in PAGES:
        page.evaluate(f"() => window.FastMDXDashboard.navigate('{name}')")
        _settled(page)
        unreadable += [(name, row["text"], row["ratio"], row["cls"])
                       for row in page.evaluate(UNREADABLE)]
    for name in DIALOGS:
        page.evaluate(f"() => window.FastMDXDialog.open('{name}')")
        _settled(page)
        unreadable += [(name, row["text"], row["ratio"], row["cls"])
                       for row in page.evaluate(UNREADABLE)]
        page.evaluate(f"() => window.FastMDXDialog.close('{name}')")
    page.context.close()
    assert unreadable == []


def test_the_status_colours_follow_the_scheme(browser, dashboard) -> None:
    """Declared on :root from the accents, so the scheme is set there too."""
    page = _in(browser, dashboard, "light")
    root = page.evaluate("() => [document.documentElement.dataset.theme, getComputedStyle("
                         "document.body).getPropertyValue('--status-completed').trim()]")
    page.context.close()
    assert root == ["light", "#1b7a45"]


def test_the_first_visit_takes_the_systems_scheme(browser, dashboard) -> None:
    light = _in(browser, dashboard, color_scheme="light")
    assert light.evaluate("() => document.body.dataset.theme") == "light"
    light.context.close()
    dark = _in(browser, dashboard, color_scheme="dark")
    assert dark.evaluate("() => document.body.dataset.theme") == "dark"
    dark.context.close()


def test_a_chosen_scheme_is_kept(browser, dashboard) -> None:
    page = _in(browser, dashboard, "dark", color_scheme="light")
    page.reload(wait_until="domcontentloaded")
    page.wait_for_function("() => document.body.dataset.theme")
    assert page.evaluate("() => document.body.dataset.theme") == "dark"
    page.context.close()


@pytest.mark.parametrize("former,now", [("graphite", "dark"), ("ink", "dark"), ("paper", "light")])
def test_a_scheme_chosen_before_is_its_new_name(browser, dashboard, former, now) -> None:
    """Graphite and Ink were dark schemes and Paper the light one; a
    person who chose one keeps it, under its new name, whatever the
    computer asks for."""
    context = browser.new_context(viewport={"width": 1440, "height": 900},
                                  color_scheme="light" if now == "dark" else "dark")
    context.add_init_script(f"try {{ localStorage.setItem('fmx.theme', '{former}'); }} catch (e) {{}}")
    page = context.new_page()
    page.set_default_timeout(60000)
    page.goto(dashboard.url + "#overview", wait_until="domcontentloaded")
    page.wait_for_function("() => window.FastMDXDashboard && document.body.dataset.theme")
    said = page.evaluate("""() => [document.documentElement.dataset.theme,
        document.body.dataset.theme, localStorage.getItem('fmx.theme'),
        document.querySelector('.seg-btn.active[data-theme]').dataset.theme]""")
    context.close()
    assert said == [now, now, now, now]


def test_the_dark_ground_is_neutral(browser, dashboard) -> None:
    """The dark scheme's ground was #050505 under cyan and violet glows,
    which read as green: now the lab website's #1c1c1d, flat, with cards
    #212529 and its cyan."""
    page = _in(browser, dashboard, "dark")
    said = page.evaluate("""() => {
        const shell = getComputedStyle(document.querySelector('.app-shell'));
        const card = document.querySelector('.card');
        return [shell.backgroundColor, shell.backgroundImage,
                getComputedStyle(card).backgroundColor,
                getComputedStyle(document.body).getPropertyValue('--accent-cyan').trim()];
    }""")
    page.context.close()
    assert said == ["rgb(28, 28, 29)", "none", "rgb(33, 37, 41)", "#2698ba"]


def test_the_charts_redraw_in_the_new_scheme(browser, dashboard) -> None:
    page = _in(browser, dashboard, "dark")
    # As long as the other browser tests wait: beside a full suite on two
    # cores the chart's first value took over thirty seconds.
    page.wait_for_function("() => document.querySelector('[data-chart-value=\"temperature\"]')"
                           ".textContent !== '\u2014'", timeout=60000)

    def brightness():
        """The mean brightness of what the temperature chart has drawn."""
        return page.evaluate("""() => {
            const c = document.querySelector('canvas[data-chart="temperature"]');
            const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
            let sum = 0, n = 0;
            for (let i = 0; i < d.length; i += 4) {
                if (d[i + 3] < 128) continue;
                sum += (d[i] + d[i + 1] + d[i + 2]) / 3; n += 1;
            }
            return n ? sum / n : null;
        }""")

    before = brightness()
    _choose(page, "light")
    page.wait_for_timeout(200)
    after = brightness()
    page.context.close()
    # The series and labels were light, for a dark ground; on Light they are
    # drawn dark.
    assert before is not None and after is not None
    assert after < before - 60
