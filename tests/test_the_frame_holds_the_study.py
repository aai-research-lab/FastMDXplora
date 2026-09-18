"""Three columns, one of them always the run.

The sidebar is the study: what is running and how far along. The main
column is the task. The side panel is what the command line prints and the
browser used to throw away -- the explain text, the refusals, the stage
transitions, as they happen -- and a Files tab for what the run has
written, opened in place.

None of the frame knows which page is in the middle. That is what lets a
person look at the molecule without losing the run.
"""

from __future__ import annotations

import json
import pathlib
import unittest
from urllib.request import urlopen

import fastmdxplora.gui as gui

STATIC = pathlib.Path(gui.__file__).parent / "static"
TEMPLATE = pathlib.Path(gui.__file__).parent / "templates" / "dashboard.html"


def _page() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def _script() -> str:
    return (STATIC / "frame.js").read_text(encoding="utf-8")


def _css() -> str:
    return (STATIC / "dashboard.css").read_text(encoding="utf-8")


class TestTheColumns(unittest.TestCase):

    def test_there_are_three_and_two_handles_between_them(self):
        page = _page()
        self.assertIn('class="sidebar"', page)
        self.assertIn('class="main"', page)
        self.assertIn('id="side-panel"', page)
        self.assertEqual(page.count('class="col-handle"'), 2)

    def test_the_handles_are_separators_a_screen_reader_can_name(self):
        page = _page()
        self.assertEqual(page.count('role="separator"'), 2)
        self.assertIn('aria-label="Resize sidebar"', page)
        self.assertIn('aria-label="Resize side panel"', page)

    def test_widths_are_tokens_the_grid_reads(self):
        css = _css()
        self.assertIn("--sidebar-width", css)
        self.assertIn("--panel-width", css)
        grid = css[css.index(".app-shell {", css.index("The frame:")):]
        self.assertIn("var(--sidebar-width)", grid[:400])
        self.assertIn("var(--panel-width)", grid[:400])

    def test_dragging_is_bounded(self):
        # A sidebar dragged to zero, or a panel dragged across the whole
        # window, is a page nobody can use and a preference nobody meant.
        script = _script()
        self.assertIn("LIMITS", script)
        self.assertIn("Math.max(lim[0], Math.min(lim[1], px))", script)

    def test_a_double_click_restores_the_default(self):
        self.assertIn('handle.addEventListener("dblclick"', _script())

    def test_the_panel_collapses_and_comes_back(self):
        page = _page()
        self.assertIn('id="side-collapse"', page)
        self.assertIn('id="side-expand"', page)
        self.assertIn("body.panel-collapsed .app-shell", _css())


class TestTheSidePanel(unittest.TestCase):

    def test_it_has_a_log_and_a_files_tab(self):
        page = _page()
        self.assertIn('data-side-tab="log"', page)
        self.assertIn('data-side-tab="files"', page)
        self.assertIn('role="tablist"', page)

    def test_the_log_reads_the_events_the_run_already_writes(self):
        # Not a second stream. /api/events is what the overview page shows;
        # the panel is the same events with room to be read.
        self.assertIn('fetch("/api/events")', _script())

    def test_the_files_tab_reads_what_the_run_wrote(self):
        self.assertIn('fetch("/api/artifacts")', _script())

    def test_a_refusal_is_told_from_a_line(self):
        # The point of the column. A refusal in the middle of ordinary
        # output is what a person needed to see and could not.
        script = _script()
        self.assertIn('kind: "refused"', script)
        self.assertIn('data-kind="refused"', _css())

    def test_the_explain_text_is_a_block_not_a_line(self):
        # The "why" text has a citation on the end and reads as prose. On
        # one line it is a ribbon; in a block it is the paragraph the CLI
        # prints.
        script = _script()
        self.assertIn('kind: "why"', script)
        self.assertIn("et al", script)

    def test_the_log_can_be_filtered_to_what_matters(self):
        page = _page()
        for f in ("all", "refusals", "why"):
            with self.subTest(filter=f):
                self.assertIn(f'data-log-filter="{f}"', page)

    def test_follow_is_on_by_default_and_a_scroll_up_is_respected(self):
        page = _page()
        self.assertIn('id="side-follow" checked', page)
        # Only scroll to the bottom when following or already there.
        self.assertIn("wasAtBottom", _script())

    def test_a_file_opens_in_place_by_kind(self):
        script = _script()
        self.assertIn("function preview(item)", script)
        for kind in ('"png"', '"pdf"', '"yml"'):
            with self.subTest(kind=kind):
                self.assertIn(kind, script)

    def test_every_file_can_be_downloaded_or_opened(self):
        page = _page()
        self.assertIn('id="side-preview-download"', page)
        self.assertIn('id="side-preview-open"', page)

    def test_the_panel_is_not_load_bearing(self):
        # A run must not fail because the panel could not fetch. Every
        # fetch swallows its error.
        script = _script()
        self.assertGreaterEqual(script.count(".catch(function ()"), 3)


class TestTheSettingsPopup(unittest.TestCase):

    def test_the_trigger_is_at_the_foot_of_the_sidebar(self):
        page = _page()
        trigger = page.index('id="settings-open"')
        sidebar_close = page.index("</aside>", trigger)
        self.assertLess(trigger, sidebar_close)
        # Nothing after it inside the sidebar: it is the foot.
        between = page[trigger:sidebar_close]
        self.assertNotIn("<nav", between)

    def test_the_trigger_says_what_the_agent_is_configured_with(self):
        page = _page()
        self.assertIn('id="account-detail"', page)
        self.assertIn('el("account-detail")', _script())

    def test_the_popup_is_a_dialog_with_a_theme_switch(self):
        page = _page()
        popup = page[page.index('id="settings-popup"'):]
        self.assertIn('role="dialog"', popup[:200])
        for theme in ("graphite", "ink", "paper"):
            with self.subTest(theme=theme):
                self.assertIn(f'data-theme="{theme}"', popup)

    def test_escape_and_a_click_outside_close_it(self):
        script = _script()
        self.assertIn('e.key === "Escape"', script)
        self.assertIn("!popup.contains(e.target)", script)

    def test_the_page_has_exactly_the_external_links_it_had(self):
        # test_dashboard_html_has_aai_branding counts external references
        # on purpose. The Tools group moved its two links into the popup;
        # the total is unchanged, and the DOI on the Cite page is the third.
        page = _page()
        self.assertEqual(page.count("https://"), 3)
        popup = page[page.index('id="settings-popup"'):page.index('<div class="app-shell">')]
        self.assertIn("readthedocs.io", popup)
        self.assertIn("github.com/aai-research-lab", popup)

    def test_cite_is_not_behind_the_popup(self):
        # The one thing a scientific tool most needs its user to find is
        # a line in the sidebar on every page, not an item in a menu.
        page = _page()
        sidebar = page[page.index('<aside class="sidebar"'):page.index("</aside>")]
        self.assertIn('data-view-link="cite"', sidebar)
        # The popup's About may point there too. What the principle
        # forbids is the citation being *only* reachable through a menu.
        self.assertIn("Cite FastMDXplora", sidebar)


class TestTheThemes(unittest.TestCase):

    def test_three_schemes_over_one_set_of_tokens(self):
        theme = (STATIC / "theme.css").read_text(encoding="utf-8")
        self.assertIn('body[data-theme="ink"]', theme)
        self.assertIn('body[data-theme="paper"]', theme)
        # Graphite is the default: the tokens themselves, no override.
        self.assertNotIn('body[data-theme="graphite"]', theme)

    def test_a_scheme_changes_the_ground_and_not_the_meaning_of_colour(self):
        # Green done, amber qualified, red refused are the same in all
        # three. A scheme that recoloured status would make a warning look
        # like one thing in Graphite and another in Paper.
        theme = (STATIC / "theme.css").read_text(encoding="utf-8")
        for name in ("ink", "paper"):
            block = theme[theme.index(f'body[data-theme="{name}"]'):]
            block = block[:block.index("}")]
            with self.subTest(theme=name):
                for token in ("--status-error", "--status-warning", "--status-success"):
                    self.assertNotIn(token, block)

    def test_the_choice_is_remembered(self):
        self.assertIn('store.set("theme", name)', _script())

    def test_the_wordmark_needs_no_network(self):
        # This GUI runs on machines with no route out -- a cluster login
        # node, a lab box behind a firewall. A web font is a font that
        # fails there, and the branding test refuses googleapis for that
        # reason.
        self.assertNotIn("googleapis", _page().lower())
        css = _css()
        mark = css[css.index(".brand-product {"):]
        mark = mark[:mark.index("}")]
        self.assertIn("Georgia", mark)
        self.assertNotIn("googleapis", mark)


class TestItServes(unittest.TestCase):

    def test_the_frame_is_in_the_page_a_browser_gets(self):
        import tempfile

        from fastmdxplora.gui.server import start_dashboard_session

        session = start_dashboard_session(
            output=tempfile.mkdtemp(), host="127.0.0.1", port=0)
        self.addCleanup(session.server.shutdown)
        base = f"http://127.0.0.1:{session.port}"
        page = urlopen(base + "/").read().decode("utf-8")
        self.assertIn('id="side-panel"', page)
        self.assertEqual(urlopen(base + "/static/frame.js").getcode(), 200)
        events = json.loads(urlopen(base + "/api/events").read())
        self.assertIn("events", events)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


class TestTheStudyIsInTheSidebar(unittest.TestCase):
    """The top bar became a sidebar block, and nothing the JS writes to moved
    out from under it."""

    def test_the_bar_is_gone(self):
        self.assertNotIn('class="top-bar"', _page())

    def test_every_id_the_dashboard_writes_to_is_still_there_once(self):
        page = _page()
        for run_id in ("topbar-run-id", "topbar-run-title", "topbar-status-dot",
                       "topbar-status-text", "topbar-stage", "topbar-step",
                       "topbar-total", "topbar-progress", "topbar-eta",
                       "pause-toggle", "refresh-now", "open-output",
                       "refreshed-at"):
            with self.subTest(id=run_id):
                self.assertEqual(page.count(f'id="{run_id}"'), 1)

    def test_they_are_inside_the_sidebar(self):
        page = _page()
        sidebar = page[page.index('<aside class="sidebar"'):page.index("</aside>")]
        for run_id in ("topbar-run-title", "topbar-eta", "pause-toggle"):
            with self.subTest(id=run_id):
                self.assertIn(f'id="{run_id}"', sidebar)

    def test_the_stages_reuse_the_class_the_dashboard_drives(self):
        # dashboard.js updates `.stage-step` by class selector, so the
        # sidebar list and the overview timeline are painted by the same
        # code and cannot disagree.
        page = _page()
        sidebar = page[page.index('<aside class="sidebar"'):page.index("</aside>")]
        for stage in ("setup", "nvt", "npt", "production", "analysis", "report"):
            with self.subTest(stage=stage):
                self.assertIn(f'class="stage-step" data-stage="{stage}"', sidebar)
        script = (STATIC / "dashboard.js").read_text(encoding="utf-8")
        self.assertIn("$$('.stage-step')", script)


class TestTheBuilderAsksFourQuestions(unittest.TestCase):
    """Four numbered cards, the phases as tiles, and the second question
    continued inside the first rather than opened as a card of its own."""

    def builder(self):
        page = _page()
        start = page.index('<section class="page" data-page="run">')
        # Anchored on the section, not on `data-page="overview"` alone: the
        # <html> element carries that attribute too, and matching it sent
        # an earlier edit slicing backwards through the file and
        # duplicating two hundred lines.
        end = page.index('<section class="page" data-page="overview"', start)
        self.assertGreater(end, start)
        return page[start:end]

    def test_four_cards_numbered_in_order(self):
        import re

        steps = re.findall(r'builder-step">(\d\d)<', self.builder())
        self.assertEqual(steps, ["01", "02", "03", "04"])
        self.assertEqual(self.builder().count('class="card builder-card"'), 4)

    def test_where_it_is_continues_the_first_card(self):
        builder = self.builder()
        self.assertNotIn("Where is it?", builder)
        self.assertIn('id="run-input-card" hidden', builder)
        # Inside the first card: before the phases card opens.
        self.assertLess(builder.index('id="run-input-card"'),
                        builder.index('id="run-phases-card"'))

    def test_every_field_the_js_drives_is_still_there_once(self):
        builder = self.builder()
        for field in ("run-start", "run-system", "run-trajectory",
                      "run-topology", "run-config-path", "run-output",
                      "run-phases", "run-start-button", "run-download"):
            with self.subTest(field=field):
                self.assertEqual(builder.count(f'id="{field}"'), 1)

    def test_the_phases_are_tiles(self):
        css = _css()
        rule = css[css.index(".run-phases {"):]
        rule = rule[:rule.index("}")]
        self.assertIn("grid-template-columns: 1fr 1fr", rule)
        # A chosen tile is bordered in the accent, not striped down one
        # edge, so the set that will run reads at a glance.
        self.assertIn('.run-phase[data-chosen="true"] { border-color: var(--accent-cyan)', css)

    def test_the_page_is_not_duplicated(self):
        # The regression the slicing bug produced: the run section's cards
        # appearing twice. Once, and once only.
        page = _page()
        self.assertEqual(page.count('<section class="page" data-page="run">'), 1)
        self.assertEqual(page.count('id="run-phases-card"'), 1)


class TestEachColumnScrollsAlone(unittest.TestCase):
    """The shell is the viewport; each column is its own scroll region; the
    wordmark and the settings trigger stay where they are however long the
    sidebar gets."""

    def test_the_body_does_not_scroll(self):
        css = _css()
        self.assertIn("body { overflow: hidden; }", css)
        self.assertIn(".app-shell { height: 100vh; min-height: 0; }", css)

    def test_the_columns_do(self):
        css = _css()
        block = css[css.index("Each column scrolls by itself"):]
        self.assertIn("overflow-y: auto;", block)
        self.assertIn(".sidebar, .main, .side-panel {", block)

    def test_the_wordmark_is_pinned(self):
        css = _css()
        block = css[css.index("Each column scrolls by itself"):]
        rule = block[block.index(".sidebar-brand {"):]
        rule = rule[:rule.index("}")]
        self.assertIn("position: sticky; top: 0;", rule)

    def test_the_settings_trigger_is_pinned_at_the_foot(self):
        css = _css()
        block = css[css.index("Each column scrolls by itself"):]
        self.assertIn(".sidebar-account { position: sticky; bottom: 0;", block)


class TestThePopupItemsAct(unittest.TestCase):

    def test_agent_settings_opens_the_agents_dialog_without_leaving(self):
        # Landing on the page and leaving somebody to find the button was
        # the same as not linking it. And navigating to the Agent page in
        # order to open a settings dialog was a detour: the dialog is a
        # fixed overlay and belongs at body level, where it opens over
        # whatever page is showing.
        script = _script()
        self.assertIn('el("settings-agent-link")', script)
        self.assertIn("window.FastMDXAgent.openSettings()", script)
        handler = script[script.index('el("settings-agent-link")'):script.index("The other popup items navigate")]
        self.assertNotIn('navigate("agent")', handler)
        page = _page()
        self.assertLess(page.index('id="agent-settings"'), page.index('<div class="app-shell">'))
        agent = (STATIC / "agent-panel.js").read_text(encoding="utf-8")
        self.assertIn("window.FastMDXAgent = { openSettings: openSettings", agent)

    def test_about_goes_somewhere(self):
        page = _page()
        popup = page[page.index('id="settings-popup"'):page.index('<div class="app-shell">')]
        about = popup[popup.index("About FastMDXplora") - 120:popup.index("About FastMDXplora")]
        self.assertIn('href="#cite"', about)

    def test_the_version_comes_from_the_cite_page(self):
        # One copy, filled in by the server, rather than a second
        # placeholder to keep in step.
        self.assertIn('el("cite-version")', _script())

    def test_the_settings_page_is_named_for_what_it_holds(self):
        # "Browser settings" read as configuring the browser. The page is
        # viewer and dashboard preferences.
        self.assertIn("Display preferences", _page())

    def test_pause_is_one_word(self):
        # "Pause updates" overlapped Refresh in a 232px sidebar.
        self.assertIn('<span id="pause-label">Pause</span>', _page())
