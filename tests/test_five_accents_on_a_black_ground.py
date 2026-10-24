"""A black ground under raised cards, and five accents to choose from.

The cards sat on a grey 1.10:1 from them (light 1.05:1), so the page read as
one flat surface; the ground is now black (light, a warm grey under white
cards with a hairline). The accent, Cyan alone before, is one of five
(Cyan, Blue, Violet, Magenta, Graphite), chosen by the person in the
settings popup or by the service hosting the GUI (`fastmdx gui --accent`),
the person's own choice first. Live stays cyan, and done, qualified and
refused keep their colours, whatever the accent.

The ratios are worked out from `theme.css` itself (WCAG 2 contrast), for
both schemes and all five accents.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "src" / "fastmdxplora" / "gui" / "static"
THEME = (STATIC / "theme.css").read_text(encoding="utf-8")
ACCENTS = ("cyan", "blue", "violet", "magenta", "graphite")
GROUNDS = ("--background-primary", "--background-secondary", "--panel-glass",
           "--background-elevated", "--background-soft", "--panel-hover")
STATUS = ("--status-live", "--accent-green", "--accent-orange", "--accent-red",
          "--accent-violet", "--accent-blue")


def _block(selector: str) -> dict[str, str]:
    """The custom properties a rule of theme.css declares."""
    at = THEME.index(selector + " {") if selector + " {" in THEME else THEME.index(selector)
    body = THEME[THEME.index("{", at) + 1:THEME.index("}", at)]
    return dict(re.findall(r"(--[\w-]+):\s*([^;]+);", body))


def _tokens(scheme: str, accent: str) -> dict[str, str]:
    """Every token as a page of that scheme and accent resolves it."""
    tokens: dict[str, str] = {}
    for plain in re.finditer(r"^:root \{([^}]*)\}", THEME, re.M):
        tokens.update(re.findall(r"(--[\w-]+):\s*([^;]+);", plain.group(1)))
    if accent != "cyan":
        tokens.update(_block(f':root[data-accent="{accent}"]'))
    if scheme == "light":
        tokens.update(_block(':root[data-theme="light"],\nbody[data-theme="light"]'))

    def value(name: str) -> str:
        found = tokens[name].strip()
        named = re.fullmatch(r"var\((--[\w-]+)(?:,[^)]*)?\)", found)
        return value(named.group(1)) if named else found

    return {name: value(name) for name in tokens}


def _luminance(colour: str) -> float:
    colour = colour.lstrip("#")
    channels = [int(colour[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _ratio(one: str, other: str) -> float:
    a, b = sorted((_luminance(one), _luminance(other)), reverse=True)
    return (a + 0.05) / (b + 0.05)


@pytest.mark.parametrize("scheme", ["dark", "light"])
@pytest.mark.parametrize("accent", ACCENTS)
def test_every_word_reads_on_every_ground(scheme, accent) -> None:
    t = _tokens(scheme, accent)
    lowest = {}
    for ground in GROUNDS:
        for words in ("--text-primary", "--text-secondary", "--text-muted", "--accent-text",
                      *STATUS):
            lowest[words] = min(lowest.get(words, 99), _ratio(t[words], t[ground]))
    assert all(ratio >= 4.5 for ratio in lowest.values()), lowest
    # The accents are matched in strength: words 5.0:1 at their lowest.
    assert lowest["--accent-text"] >= 5.0, lowest
    # Text on a fill (8.0:1 dark, 6.0:1 light, to two figures), the accent
    # on its tint, the main text on the tint.
    assert _ratio(t["--on-accent"], t["--accent"]) >= (8.0 if scheme == "dark" else 5.95)
    assert _ratio(t["--accent"], t["--accent-soft"]) >= 4.8
    assert _ratio(t["--text-primary"], t["--accent-soft"]) >= 4.5
    assert _ratio(t["--text-secondary"], t["--accent-soft"]) >= 4.5
    # The tint marks a choice on a card's ground (the Config Builder's
    # segmented choices sit on one): seen there, Graphite's grey too.
    assert _ratio(t["--accent-soft"], t["--panel-glass"]) >= 1.1


@pytest.mark.parametrize("scheme", ["dark", "light"])
def test_the_cards_stand_off_the_ground(scheme) -> None:
    t = _tokens(scheme, "cyan")
    assert _ratio(t["--panel-glass"], t["--background-primary"]) >= (1.25 if scheme == "dark"
                                                                       else 1.1)


# A state drawn on its own ground: hovered, chosen, pressed or open.
STATE = re.compile(r":hover|:focus-visible|:focus-within|:has\([^)]*:checked\)|\.active"
                   r"|\.selected|\.current|\[data-chosen|aria-(?:selected|pressed|expanded"
                   r"|current|checked)|is-(?:chosen|open|current|on|selected)|\[open\]")
WHITE_IN_LIGHT = ("--background-elevated", "--panel-glass", "--background-secondary")


def test_no_row_hovered_or_chosen_is_white_on_white() -> None:
    """The cards are now the secondary ground, white in light as the popups
    and panels are, so a row hovered or chosen there takes the soft ground."""
    text = re.sub(r"/\*.*?\*/", "", (STATIC / "dashboard.css").read_text(encoding="utf-8"),
                  flags=re.S)
    unseen = []
    for rule in re.finditer(r"([^{}]+)\{([^{}]*)\}", text):
        selector, body = " ".join(rule.group(1).split()), rule.group(2)
        painted = re.search(r"background(?:-color)?:\s*var\((--[\w-]+)[,)]", body)
        # A row on the elevated ground (a menu, the analyses to choose, the
        # ways to start) hovered or chosen needs the soft one in dark too:
        # the hover ground is 1.04:1 from it.
        if (selector.startswith((".files-menu", ".analyse-row", ".analyse-folder-row",
                                 ".run-start-choice", ".agent-starter"))
                and painted and painted.group(1) == "--panel-hover"):
            unseen.append(selector)
        # An icon button hovered keeps its border; it is seen on any ground.
        if (STATE.search(selector) and painted and painted.group(1) in WHITE_IN_LIGHT
                and selector != ".icon-btn:hover"):
            unseen.append(selector)
    assert unseen == []


def _mixed(colour: str, share: float, ground: str) -> str:
    one, two = (bytes.fromhex(c.lstrip("#")) for c in (colour, ground))
    return "#" + "".join(f"{round(a * share + b * (1 - share)):02x}" for a, b in zip(one, two))


@pytest.mark.parametrize("scheme", ["dark", "light"])
@pytest.mark.parametrize("accent", ACCENTS)
def test_a_tint_of_the_accent_shows_on_the_page_s_ground(scheme, accent) -> None:
    t = _tokens(scheme, accent)
    ground = t["--background-primary"]
    tint = _mixed(t["--accent"], 0.16, ground)
    assert _ratio(tint, ground) >= 1.1, (tint, ground)
    for words in ("--text-primary", "--text-secondary"):
        assert _ratio(t[words], tint) >= 4.5, words
    # The usage toggle's icon, in the accent, on it: 3:1 for a mark.
    assert _ratio(t["--accent-text"], tint) >= 3.0


@pytest.mark.parametrize("scheme", ["dark", "light"])
def test_the_soft_ground_stands_out_of_a_panel_or_popup(scheme) -> None:
    t = _tokens(scheme, "cyan")
    for under in WHITE_IN_LIGHT:
        assert _ratio(t["--background-soft"], t[under]) >= 1.1, (under, scheme)


@pytest.mark.parametrize("scheme", ["dark", "light"])
def test_live_and_the_states_are_never_the_accent(scheme) -> None:
    shown = {accent: {name: _tokens(scheme, accent)[name]
                      for name in ("--status-live", "--accent-green", "--accent-orange",
                                   "--accent-red", "--hue-cyan")}
             for accent in ACCENTS}
    assert all(each == shown["cyan"] for each in shown.values()), shown
    # Live is cyan: the Cyan accent's own value.
    assert shown["cyan"]["--status-live"] == _tokens(scheme, "cyan")["--accent"]


def test_nothing_reads_the_cyan_accent_by_habit() -> None:
    source = Path(__file__).resolve().parents[1] / "src" / "fastmdxplora"
    readers = [str(path.relative_to(source)) for path in source.rglob("*")
               if path.suffix in (".css", ".js", ".html", ".py")
               and "--accent-cyan" in path.read_text(encoding="utf-8", errors="replace")]
    assert readers == []


def test_the_five_are_named_alike_everywhere() -> None:
    import argparse

    from fastmdxplora.cli.main import _build_parser
    from fastmdxplora.gui import accents

    assert accents.ACCENTS == ACCENTS
    commands = next(action for action in _build_parser()._actions
                    if isinstance(action, argparse._SubParsersAction))
    gui = next(action for action in commands.choices["gui"]._actions
               if "--accent" in action.option_strings)
    assert tuple(gui.choices) == ACCENTS
    # Named in any case, as FASTMDX_ACCENT is.
    assert _build_parser().parse_args(["gui", "--accent", "Graphite"]).accent == "graphite"
    page = (STATIC.parent / "templates" / "dashboard.html").read_text(encoding="utf-8")
    assert tuple(re.findall(r'class="accent-swatch" data-accent="(\w+)"', page)) == ACCENTS
    assert tuple(re.findall(r':root\[data-accent="(\w+)"\]', THEME))[:4] == ACCENTS[1:]
    for script, name in (("theme-first.js", "accents"), ("frame.js", "ACCENTS")):
        said = re.search(rf"var {name} = \[([^\]]*)\]",
                         (STATIC / script).read_text(encoding="utf-8"))
        assert tuple(re.findall(r'"(\w+)"', said.group(1))) == ACCENTS, script


def test_a_chosen_tint_is_the_soft_accent_and_an_accepted_config_is_done() -> None:
    sheet = (STATIC / "dashboard.css").read_text(encoding="utf-8")

    def rule(selector: str) -> str:
        at = sheet.index(selector)
        return sheet[sheet.index("{", at) + 1:sheet.index("}", at)]

    # The tint the contrast test reads is the one drawn behind accent words.
    for chosen in ('.icon-btn.active, .icon-btn[aria-pressed="true"]',
                   '.builder-seg-btn[aria-checked="true"]'):
        said = rule(chosen)
        assert "background: var(--accent-soft)" in said and "var(--accent-text)" in said
    # The segmented choices sit on a card's ground, where the tint shows.
    assert "background: var(--panel-glass)" in rule('.page[data-page="run"] .builder-seg {')
    # The canvas's dark blocks take the dark tint with the dark accent.
    assert sheet.count("--accent-soft: var(--accent-soft-d);") == 2
    # Every control in the accent, not the browser's blue; the Files page's
    # chosen chip and its search in the accent too.
    assert ":where(input, select, progress) { accent-color: var(--accent); }" in sheet
    for chosen in ('.files-filter[aria-pressed="true"] {', ".files-search:focus-within {"):
        assert "--accent-blue" not in rule(chosen), chosen
    # On the page's own ground (the Files page's chips, the usage shown under
    # an Agent message), a chosen one is tinted with the accent itself: the
    # soft tint is for a card's ground and is lost on the warm grey.
    for chosen in ('.files-filter[aria-pressed="true"] {',
                   '.agent-usage-toggle[aria-expanded="true"]'):
        assert "background: color-mix(in srgb, var(--accent) 16%, transparent)" in rule(chosen)
    # An accepted config is a state, not the accent.
    assert "var(--status-completed)" in rule(".agent-attempt.ok {")


def test_a_service_s_accent_is_the_default_and_a_report_s(monkeypatch, tmp_path) -> None:
    import datetime

    from fastmdxplora.gui import report_dashboard
    from fastmdxplora.gui.accents import ACCENT_ENV, default_accent

    def report() -> str:
        return report_dashboard._render_dashboard(
            title="Accent", system="1L2Y", status="completed",
            generated_at=datetime.datetime.now(datetime.timezone.utc), phase_notice="",
            cards=[], sections=[], phase_rows=[], metrics=[], output_folder=str(tmp_path),
            live_html="")

    monkeypatch.delenv(ACCENT_ENV, raising=False)
    assert default_accent() == "cyan"
    assert "data-accent=" not in report()[:200]
    monkeypatch.setenv(ACCENT_ENV, "Violet")
    assert default_accent() == "violet"
    assert '<html lang="en" data-page="overview" data-accent="violet">' in report()
    monkeypatch.setenv(ACCENT_ENV, "orange")
    assert default_accent() == "cyan"


def test_a_service_s_accent_not_among_the_five_is_said(monkeypatch, capsys, tmp_path) -> None:
    from fastmdxplora.cli.main import main
    from fastmdxplora.gui.accents import ACCENT_ENV

    served = []
    monkeypatch.setattr("fastmdxplora.gui.server.serve_dashboard",
                        lambda *a, **k: served.append(1) or 0)
    monkeypatch.setenv(ACCENT_ENV, "orange")
    main(["gui", "--no-browser", "--output", str(tmp_path)])
    said = capsys.readouterr().err
    assert "fastmdx: FASTMDX_ACCENT='orange' is not one of cyan, blue, violet, magenta, graphite" in said
    monkeypatch.setenv(ACCENT_ENV, "Violet")
    main(["gui", "--no-browser", "--output", str(tmp_path)])
    assert "is not one of" not in capsys.readouterr().err
    monkeypatch.setenv(ACCENT_ENV, "orange")
    monkeypatch.chdir(tmp_path)
    # `fastmdx agent` opens the same page, and says it the same way.
    main(["agent", "--no-browser"])
    assert "fastmdx: FASTMDX_ACCENT='orange' is not one of" in capsys.readouterr().err
    # A command refused says why, and not this as well.
    monkeypatch.setenv(ACCENT_ENV, "orange")
    assert main(["gui", "--demo", str(tmp_path), "--output", str(tmp_path)]) == 2
    assert "is not one of" not in capsys.readouterr().err


@pytest.fixture(scope="module")
def browser():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        launched = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        yield launched
        launched.close()


def _session(tmp_path):
    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_the_workspace_says_its_studies import _study

    study = _study(tmp_path / "workspace" / "ubiquitin", system="1UBQ",
                   started="2026-09-01T10:00:00+00:00")
    return start_dashboard_session(output=str(study), host="127.0.0.1", port=0)


# What the page has at its first paint: read as the stylesheets arrive, before
# frame.js runs.
FIRST_PAINT = """(() => {
    document.addEventListener('readystatechange', () => {
        if (window.__firstAccent === undefined) {
            window.__firstAccent = document.documentElement.getAttribute('data-accent');
        }
    }, { once: true });
})();"""


def _page(browser, session, accent=None):
    context = browser.new_context(viewport={"width": 1400, "height": 900})
    context.add_init_script(FIRST_PAINT)
    if accent is not None:
        context.add_init_script(f"localStorage.setItem('fmx.accent', {accent!r});")
    page = context.new_page()
    page.set_default_timeout(60000)
    page.goto(session.url + "#overview", wait_until="domcontentloaded")
    page.wait_for_function("() => window.FastMDXFrame && window.FastMDXFrame.applyAccent")
    return page


def test_an_accent_chosen_is_kept_and_drawn_first(browser, tmp_path) -> None:
    session = _session(tmp_path)
    try:
        page = _page(browser, session)
        page.click("#settings-open")
        page.click(".accent-swatch[data-accent='magenta']")
        chosen = page.evaluate("""() => [document.documentElement.dataset.accent,
            getComputedStyle(document.documentElement).getPropertyValue('--accent').trim(),
            document.querySelector(".accent-swatch[data-accent='magenta']")
              .getAttribute('aria-pressed')]""")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function("() => window.__firstAccent !== undefined")
        first = page.evaluate("() => window.__firstAccent")
        live = page.evaluate("() => getComputedStyle(document.documentElement)"
                             ".getPropertyValue('--status-live').trim()")
        page.context.close()
    finally:
        session.server.shutdown()
    assert chosen[0] == "magenta" and chosen[2] == "true"
    assert chosen[1] in ("#f872b7", "#b02e79")
    # Reloaded, the page is magenta before anything of it is drawn.
    assert first == "magenta"
    # Live is still cyan.
    assert live in ("#00aed8", "#056c87")


def test_a_service_s_accent_and_a_person_s_over_it(browser, tmp_path, monkeypatch) -> None:
    from fastmdxplora.gui.accents import ACCENT_ENV

    monkeypatch.setenv(ACCENT_ENV, "violet")
    session = _session(tmp_path)
    try:
        page = _page(browser, session)
        hosted = page.evaluate("() => [document.documentElement.dataset.accent, document"
                               ".querySelector('.accent-swatch[aria-pressed=\"true\"]')"
                               ".dataset.accent]")
        page.context.close()
        page = _page(browser, session, accent="cyan")
        own = page.evaluate("() => document.documentElement.getAttribute('data-accent')")
        page.context.close()
        # A name kept that is not an accent, a property of every object
        # included, is read as none: the service's.
        page = _page(browser, session, accent="constructor")
        stray = page.evaluate("() => [document.documentElement.dataset.accent, document"
                              ".querySelector('.accent-swatch[aria-pressed=\"true\"]')"
                              ".dataset.accent]")
        page.context.close()
    finally:
        session.server.shutdown()
    assert hosted == ["violet", "violet"]
    # The person chose Cyan: theirs, over the service's.
    assert own is None
    assert stray == ["violet", "violet"]


def test_graphite_underlines_its_links_and_the_swatches_take_the_keyboard(browser,
                                                                         tmp_path) -> None:
    session = _session(tmp_path)
    try:
        page = _page(browser, session)
        page.click("#settings-open")
        # The gear's menu's, and the same five on the Settings page.
        names = page.eval_on_selector_all(
            "#settings-popup .accent-swatch", "els => els.map(e => [e.tagName, e.textContent.trim()])")
        copied = page.eval_on_selector_all(
            "#settings-page-accents .accent-swatch", "els => els.map(e => [e.tagName, e.textContent.trim()])")
        page.focus(".accent-swatch[data-accent='blue']")
        page.keyboard.press("Tab")
        page.keyboard.press("Tab")
        focused = page.evaluate("() => document.activeElement.dataset.accent")
        page.keyboard.press("Enter")
        chosen = page.evaluate("() => document.documentElement.dataset.accent")
        page.evaluate("() => window.FastMDXFrame.applyAccent('graphite', true)")
        line = page.evaluate("""() => {
            const a = document.createElement('a'); a.href = '#x'; a.textContent = 'x';
            document.body.appendChild(a);
            const all = document.createElement('a'); all.className = 'overview-all-link';
            all.href = '#analysis'; all.textContent = 'All 7 analyses';
            const fix = document.createElement('a'); fix.className = 'sidebar-fix';
            fix.href = '#overview'; fix.textContent = 'What would fix it';
            document.body.append(all, fix);
            const said = [a, all, fix].map(e => getComputedStyle(e).textDecorationLine);
            a.remove(); all.remove(); fix.remove(); return said; }""")
        # A study in the picker, marked by the accent's colour alone
        # elsewhere, is bold under Graphite.
        bold = page.evaluate("""() => {
            const row = document.createElement('div');
            row.className = 'fastmdx-picker-row is-study';
            row.innerHTML = '<span class="name">ubiquitin</span>';
            const page = document.createElement('div');
            page.className = 'page'; page.dataset.page = 'run';
            page.innerHTML = '<div class="builder-field is-changed"><div class="builder-head">'
                + '<span class="builder-default">Default 7.0</span></div></div>';
            document.body.append(row, page);
            const said = [row.querySelector('.name'), page.querySelector('.builder-default')]
                .map(e => getComputedStyle(e).fontWeight);
            row.remove(); page.remove(); return said; }""")
        # The sidebar's places, the buttons drawn as links, an Overview tile
        # and a menu's item are not underlined.
        page.evaluate("""() => {
            const tile = document.createElement('a');
            tile.className = 'overview-tile'; tile.href = '#t'; tile.textContent = 't';
            const item = document.createElement('a');
            item.setAttribute('role', 'menuitem'); item.href = '#m'; item.textContent = 'm';
            const names = document.createElement('span');
            names.className = 'analysis-results-name';
            names.innerHTML = '<a href="#" data-goto-analysis="rmsd">RMSD</a>';
            document.body.append(tile, item, names); }""")
        plain = page.eval_on_selector_all(
            "a.nav-link, a.ghost-btn, a.file-action, a.settings-item, a.overview-tile,"
            " a[role=menuitem], a[data-goto-analysis]",
            "els => [els.length, els.filter(e => getComputedStyle(e).textDecorationLine"
            " !== 'none').map(e => e.className || e.getAttribute('role'))]")
        page.context.close()
    finally:
        session.server.shutdown()
    assert names == copied == [["BUTTON", "Cyan"], ["BUTTON", "Blue"], ["BUTTON", "Violet"],
                               ["BUTTON", "Magenta"], ["BUTTON", "Graphite"]]
    assert focused == chosen == "magenta"
    assert line == ["underline"] * 3
    assert bold == ["600", "600"]
    assert plain[0] > 5 and plain[1] == [], plain


RUNNING = """() => {
    const host = document.createElement('div');
    host.innerHTML = '<span class="analysis-results-status" data-status="running">r</span>'
        + '<span class="stage-pill" data-state="live">l</span>'
        + '<div class="analysis-card" data-state="running"><span class="ac-status">a</span></div>'
        + '<span class="status-dot status-dot-live"></span>';
    document.body.appendChild(host);
    const said = [...host.querySelectorAll('.analysis-results-status, .stage-pill, .ac-status')]
        .map(e => getComputedStyle(e).color);
    const probe = document.createElement('span');
    probe.style.color = 'var(--status-live)'; host.appendChild(probe);
    const live = getComputedStyle(probe).color;
    host.remove();
    return [said, live];
}"""


@pytest.mark.parametrize("scheme", ["dark", "light"])
def test_a_running_study_s_words_are_cyan_whatever_the_accent(browser, tmp_path,
                                                               scheme) -> None:
    session = _session(tmp_path)
    try:
        page = _page(browser, session)
        page.evaluate(f"() => {{ document.documentElement.dataset.theme = '{scheme}';"
                      f" document.body.dataset.theme = '{scheme}'; }}")
        seen = {}
        for accent in ACCENTS:
            page.evaluate(f"() => window.FastMDXFrame.applyAccent('{accent}', false)")
            seen[accent] = page.evaluate(RUNNING)
        page.context.close()
    finally:
        session.server.shutdown()
    cyan = "rgb(0, 174, 216)" if scheme == "dark" else "rgb(5, 108, 135)"
    for accent, (words, live) in seen.items():
        assert live == cyan, (accent, live)
        assert words == [cyan] * 3, (accent, words)
